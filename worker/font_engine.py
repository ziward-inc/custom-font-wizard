from __future__ import annotations

import math
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, Protocol, cast

from fontTools.designspaceLib import AxisDescriptor, DesignSpaceDocument, SourceDescriptor
from fontTools.merge import Merger
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.scaleUpem import scale_upem
from fontTools.ttLib.tables.DefaultTable import DefaultTable
from fontTools.varLib import build as build_variable_font
from fontTools.varLib.instancer import instantiateVariableFont

FontFlavor = Literal["ttf", "otf"]
GlyphStatus = Literal["visible", "blank", "missing"]
BuildStep = Literal[
    "validate_inputs",
    "analyze_sources",
    "prepare_glyphs",
    "generate_masters",
    "build_variable_font",
    "save_output",
    "verify_output",
]
BuildStepStatus = Literal["running", "completed"]


class BuildProgress(Protocol):
    def __call__(self, *, step: BuildStep, status: BuildStepStatus, message: str) -> None: ...


class FontBuildError(RuntimeError):
    pass


def analyze_fonts(*, base_path: Path, donor_path: Path) -> dict[str, object]:
    validate_input_path(input_path=base_path)
    validate_input_path(input_path=donor_path)
    base_font: TTFont = TTFont(base_path, recalcTimestamp=False)
    donor_font: TTFont = TTFont(donor_path, recalcTimestamp=False)
    try:
        base_flavor: FontFlavor = detect_flavor(font=base_font)
        donor_flavor: FontFlavor = detect_flavor(font=donor_font)
        ensure_matching_flavors(base_flavor=base_flavor, donor_flavor=donor_flavor)
        ensure_weight_axis(font=base_font, role="Base")
        ensure_weight_axis(font=donor_font, role="Donor")

        base_cmap: dict[int, str] = dict(base_font.getBestCmap() or {})
        donor_cmap: dict[int, str] = dict(donor_font.getBestCmap() or {})
        union_codepoints: list[int] = sorted(set(base_cmap) | set(donor_cmap))
        base_statuses: dict[str, GlyphStatus] = {}
        donor_statuses: dict[str, GlyphStatus] = {}
        codepoints: list[dict[str, object]] = []

        for codepoint in union_codepoints:
            character: str = chr(codepoint)
            base_status: GlyphStatus = codepoint_status(
                font=base_font,
                cmap=base_cmap,
                codepoint=codepoint,
                cache=base_statuses,
            )
            donor_status: GlyphStatus = codepoint_status(
                font=donor_font,
                cmap=donor_cmap,
                codepoint=codepoint,
                cache=donor_statuses,
            )
            codepoints.append(
                {
                    "codepoint": codepoint,
                    "base": base_status,
                    "donor": donor_status,
                    "whitespace": character.isspace(),
                }
            )

        return {
            "base": font_info(path=base_path, font=base_font, flavor=base_flavor),
            "donor": font_info(path=donor_path, font=donor_font, flavor=donor_flavor),
            "codepoints": codepoints,
        }
    finally:
        base_font.close()
        donor_font.close()


def build_font(
    *,
    base_path: Path,
    donor_path: Path,
    output_path: Path,
    family_name: str,
    weight_min: float,
    weight_max: float,
    selected_codepoints: list[int],
    progress: BuildProgress | None = None,
) -> dict[str, object]:
    report_progress(
        progress=progress,
        step="validate_inputs",
        status="running",
        message="Input path와 build 설정을 확인합니다",
    )
    validate_input_path(input_path=base_path)
    validate_input_path(input_path=donor_path)
    if not selected_codepoints:
        raise FontBuildError("선택된 codepoint가 없습니다")
    if weight_min >= weight_max:
        raise FontBuildError("wght minimum은 maximum보다 작아야 합니다")
    report_progress(
        progress=progress,
        step="validate_inputs",
        status="completed",
        message=f"{len(selected_codepoints)}개 codepoint와 wght {weight_min:g}–{weight_max:g} 설정을 확인했습니다",
    )

    report_progress(
        progress=progress,
        step="analyze_sources",
        status="running",
        message="Base와 Donor font를 분석합니다",
    )
    analysis: dict[str, object] = analyze_fonts(base_path=base_path, donor_path=donor_path)
    base_info: dict[str, object] = require_object(value=analysis["base"], label="base analysis")
    donor_info: dict[str, object] = require_object(value=analysis["donor"], label="donor analysis")
    flavor: FontFlavor = require_flavor(value=base_info["flavor"])
    validate_output_suffix(output_path=output_path, flavor=flavor)
    validate_family_name(family_name=family_name, base_info=base_info, donor_info=donor_info)
    report_progress(
        progress=progress,
        step="analyze_sources",
        status="completed",
        message=f"Source analysis를 완료했습니다 · {flavor.upper()}",
    )

    report_progress(
        progress=progress,
        step="prepare_glyphs",
        status="running",
        message="선택한 codepoint의 glyph source와 master sample을 준비합니다",
    )
    base_font: TTFont = TTFont(base_path, recalcTimestamp=False)
    donor_font: TTFont = TTFont(donor_path, recalcTimestamp=False)
    try:
        base_axis: tuple[float, float, float] = weight_axis_values(font=base_font)
        donor_axis: tuple[float, float, float] = weight_axis_values(font=donor_font)
        effective_min: float = max(weight_min, base_axis[0])
        effective_max: float = min(weight_max, base_axis[2])
        if effective_min > effective_max:
            raise FontBuildError("요청 wght range가 Base wght range와 겹치지 않습니다")
        if donor_axis[0] > effective_min or donor_axis[2] < effective_max:
            raise FontBuildError("Donor wght range가 Base의 실제 build range를 포함하지 않습니다")

        base_cmap: dict[int, str] = dict(base_font.getBestCmap() or {})
        donor_cmap: dict[int, str] = dict(donor_font.getBestCmap() or {})
        base_status_cache: dict[str, GlyphStatus] = {}
        donor_status_cache: dict[str, GlyphStatus] = {}
        base_codepoints: set[int] = set()
        donor_codepoints: set[int] = set()
        donor_repaired: int = 0
        donor_added: int = 0
        unavailable: int = 0

        for codepoint in selected_codepoints:
            base_status: GlyphStatus = codepoint_status(
                font=base_font,
                cmap=base_cmap,
                codepoint=codepoint,
                cache=base_status_cache,
            )
            donor_status: GlyphStatus = codepoint_status(
                font=donor_font,
                cmap=donor_cmap,
                codepoint=codepoint,
                cache=donor_status_cache,
            )
            whitespace: bool = chr(codepoint).isspace()
            if base_status == "visible" or (whitespace and base_status != "missing"):
                base_codepoints.add(codepoint)
            elif donor_status == "visible" or (whitespace and donor_status != "missing"):
                donor_codepoints.add(codepoint)
                if base_status == "blank":
                    donor_repaired += 1
                else:
                    donor_added += 1
            else:
                unavailable += 1

        output_codepoints: set[int] = base_codepoints | donor_codepoints
        if not output_codepoints:
            raise FontBuildError("선택 결과에 build 가능한 codepoint가 없습니다")

        output_default: float = clamp(value=base_axis[1], minimum=weight_min, maximum=weight_max)
        sample_weights: list[float] = collect_sample_weights(
            base_font=base_font,
            donor_font=donor_font,
            requested_min=weight_min,
            requested_default=output_default,
            requested_max=weight_max,
            effective_min=effective_min,
            effective_max=effective_max,
        )
        base_copyright: str = name_value(font=base_font, name_id=0)
        donor_copyright: str = name_value(font=donor_font, name_id=0)
        base_license: str = name_value(font=base_font, name_id=13)
        donor_license: str = name_value(font=donor_font, name_id=13)
        base_upem: int = units_per_em(font=base_font)
    finally:
        base_font.close()
        donor_font.close()
    report_progress(
        progress=progress,
        step="prepare_glyphs",
        status="completed",
        message=(
            f"Base {len(base_codepoints)}개 · Donor {len(donor_codepoints)}개 · "
            f"master sample {len(sample_weights)}개를 준비했습니다"
        ),
    )

    report_progress(
        progress=progress,
        step="generate_masters",
        status="running",
        message=f"Static master {len(sample_weights)}개를 생성합니다",
    )
    donor_gsub_data: bytes | None = None
    if flavor == "ttf":
        donor_gsub_data = static_table_data(
            path=donor_path,
            weight=output_default,
            flavor=flavor,
            table_tag="GSUB",
        )
    with tempfile.TemporaryDirectory(prefix="custom-font-wizard-") as temporary_directory:
        temporary_root: Path = Path(temporary_directory)
        master_paths: list[tuple[float, Path]] = []
        cff_target_names: dict[int, str] | None = None

        for index, output_weight in enumerate(sample_weights):
            source_weight: float = clamp(value=output_weight, minimum=base_axis[0], maximum=base_axis[2])
            base_master: TTFont = static_instance(path=base_path, weight=source_weight, flavor=flavor)
            donor_master: TTFont = static_instance(path=donor_path, weight=source_weight, flavor=flavor)
            scale_upem(donor_master, base_upem)
            if flavor == "ttf":
                replace_table_data(font=donor_master, table_tag="GSUB", table_data=donor_gsub_data)
            subset_font(font=base_master, codepoints=base_codepoints)

            master_path: Path = temporary_root / f"master-{index:02d}.{flavor}"
            if flavor == "ttf":
                subset_font(font=donor_master, codepoints=donor_codepoints)
                base_path_for_merge: Path = temporary_root / f"base-{index:02d}.ttf"
                donor_path_for_merge: Path = temporary_root / f"donor-{index:02d}.ttf"
                base_master.save(base_path_for_merge, reorderTables=True)
                donor_master.save(donor_path_for_merge, reorderTables=True)
                merged_master: TTFont = Merger().merge([str(base_path_for_merge), str(donor_path_for_merge)])
            else:
                if cff_target_names is None:
                    cff_target_names = allocate_cff_names(font=base_master, codepoints=donor_codepoints)
                append_cff_glyphs(
                    base_font=base_master,
                    donor_font=donor_master,
                    codepoints=donor_codepoints,
                    target_names=cff_target_names,
                )
                merged_master = base_master

            merged_master.save(master_path, reorderTables=True)
            merged_master.close()
            donor_master.close()
            if flavor == "ttf":
                base_master.close()
            master_paths.append((output_weight, master_path))
            report_progress(
                progress=progress,
                step="generate_masters",
                status="running",
                message=f"Master {index + 1}/{len(sample_weights)} · wght {output_weight:g}",
            )

        report_progress(
            progress=progress,
            step="generate_masters",
            status="completed",
            message=f"Static master {len(sample_weights)}개를 생성했습니다",
        )
        report_progress(
            progress=progress,
            step="build_variable_font",
            status="running",
            message="Static master를 Variable Font로 조합합니다",
        )
        designspace: DesignSpaceDocument = create_designspace(
            master_paths=master_paths,
            minimum=weight_min,
            default=output_default,
            maximum=weight_max,
        )
        variable_font_result: tuple[TTFont, object, list[TTFont]] = build_variable_font(designspace)
        variable_font: TTFont = variable_font_result[0]
        if "DSIG" in variable_font:
            del variable_font["DSIG"]
        report_progress(
            progress=progress,
            step="build_variable_font",
            status="completed",
            message="Variable Font 조합을 완료했습니다",
        )
        report_progress(
            progress=progress,
            step="save_output",
            status="running",
            message="Font metadata를 적용하고 output file을 저장합니다",
        )
        update_names(
            font=variable_font,
            family_name=family_name,
            base_copyright=base_copyright,
            donor_copyright=donor_copyright,
            base_license=base_license,
            donor_license=donor_license,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        variable_font.save(output_path, reorderTables=True)
        variable_font.close()
        report_progress(
            progress=progress,
            step="save_output",
            status="completed",
            message=f"Output을 저장했습니다 · {output_path}",
        )

    report_progress(
        progress=progress,
        step="verify_output",
        status="running",
        message="생성된 font의 format, wght range, cmap과 glyph를 검증합니다",
    )
    verify_output(
        output_path=output_path,
        flavor=flavor,
        expected_codepoints=output_codepoints,
        weight_min=weight_min,
        weight_max=weight_max,
    )
    report_progress(
        progress=progress,
        step="verify_output",
        status="completed",
        message=f"Output 검증을 완료했습니다 · {len(output_codepoints)}개 codepoint",
    )
    return {
        "output_path": str(output_path.resolve()),
        "flavor": flavor,
        "codepoint_count": len(output_codepoints),
        "base_kept": len(base_codepoints),
        "donor_repaired": donor_repaired,
        "donor_added": donor_added,
        "unavailable": unavailable,
        "sample_weights": sample_weights,
    }


def report_progress(
    *,
    progress: BuildProgress | None,
    step: BuildStep,
    status: BuildStepStatus,
    message: str,
) -> None:
    if progress is not None:
        progress(step=step, status=status, message=message)


def validate_input_path(*, input_path: Path) -> None:
    if not input_path.is_file():
        raise FontBuildError(f"Font file을 찾을 수 없습니다: {input_path}")
    if input_path.suffix.lower() not in {".ttf", ".otf"}:
        raise FontBuildError(f"TTF 또는 OTF만 사용할 수 있습니다: {input_path}")


def detect_flavor(*, font: TTFont) -> FontFlavor:
    tables: set[str] = set(font.keys())
    if {"glyf", "gvar", "fvar"}.issubset(tables):
        return "ttf"
    if {"CFF2", "fvar"}.issubset(tables):
        return "otf"
    raise FontBuildError("glyf/gvar 또는 CFF2 기반 Variable Font가 아닙니다")


def ensure_matching_flavors(*, base_flavor: FontFlavor, donor_flavor: FontFlavor) -> None:
    if base_flavor != donor_flavor:
        raise FontBuildError("Base와 Donor는 모두 Variable TTF이거나 모두 Variable OTF여야 합니다")


def ensure_weight_axis(*, font: TTFont, role: str) -> None:
    if not any(axis.axisTag == "wght" for axis in font["fvar"].axes):
        raise FontBuildError(f"{role}에 wght axis가 없습니다")


def font_info(*, path: Path, font: TTFont, flavor: FontFlavor) -> dict[str, object]:
    cmap: dict[int, str] = dict(font.getBestCmap() or {})
    axes: list[dict[str, object]] = [
        {
            "tag": axis.axisTag,
            "minimum": float(axis.minValue),
            "default": float(axis.defaultValue),
            "maximum": float(axis.maxValue),
        }
        for axis in font["fvar"].axes
    ]
    return {
        "path": str(path.resolve()),
        "family": family_name(font=font),
        "flavor": flavor,
        "units_per_em": units_per_em(font=font),
        "cmap_count": len(cmap),
        "axes": axes,
    }


def family_name(*, font: TTFont) -> str:
    name_table = font["name"]
    for name_id in (16, 1):
        value: str | None = name_table.getDebugName(name_id)
        if value:
            return value
    return "Unknown Family"


def codepoint_status(
    *,
    font: TTFont,
    cmap: dict[int, str],
    codepoint: int,
    cache: dict[str, GlyphStatus],
) -> GlyphStatus:
    glyph_name: str | None = cmap.get(codepoint)
    if glyph_name is None:
        return "missing"
    cached: GlyphStatus | None = cache.get(glyph_name)
    if cached is not None:
        return cached

    status: GlyphStatus = "blank" if glyph_is_blank(font=font, glyph_name=glyph_name) else "visible"
    cache[glyph_name] = status
    return status


def glyph_is_blank(*, font: TTFont, glyph_name: str) -> bool:
    if "glyf" in font:
        glyph = font["glyf"][glyph_name]
        if glyph.isComposite():
            return len(glyph.components) == 0
        return int(glyph.numberOfContours) == 0

    glyph_set = font.getGlyphSet()
    bounds_pen = BoundsPen(glyph_set)
    glyph_set[glyph_name].draw(bounds_pen)
    return bounds_pen.bounds is None


def weight_axis_values(*, font: TTFont) -> tuple[float, float, float]:
    for axis in font["fvar"].axes:
        if axis.axisTag == "wght":
            return float(axis.minValue), float(axis.defaultValue), float(axis.maxValue)
    raise FontBuildError("wght axis가 없습니다")


def static_instance(*, path: Path, weight: float, flavor: FontFlavor) -> TTFont:
    font: TTFont = TTFont(path, recalcTimestamp=False)
    instantiated: TTFont = instantiateVariableFont(
        font,
        {"wght": weight},
        inplace=True,
        static=True,
        downgradeCFF2=flavor == "otf",
    )
    return instantiated


def static_table_data(*, path: Path, weight: float, flavor: FontFlavor, table_tag: str) -> bytes | None:
    font: TTFont = static_instance(path=path, weight=weight, flavor=flavor)
    try:
        if table_tag not in font:
            return None
        table_data: bytes = font.getTableData(table_tag)
        return table_data
    finally:
        font.close()


def replace_table_data(*, font: TTFont, table_tag: str, table_data: bytes | None) -> None:
    if table_data is None:
        if table_tag in font:
            del font[table_tag]
        return

    table: DefaultTable = newTable(table_tag)
    table.decompile(table_data, font)
    font[table_tag] = table


def subset_font(*, font: TTFont, codepoints: set[int]) -> None:
    options = Options()
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14, 16, 17, 25]
    options.name_languages = [0x409]
    options.layout_features = ["*"]
    options.notdef_glyph = True
    options.notdef_outline = True
    options.recommended_glyphs = True
    options.glyph_names = True
    options.hinting = False
    options.recalc_bounds = True
    options.recalc_timestamp = False
    subsetter = Subsetter(options=options)
    subsetter.populate(unicodes=codepoints)
    subsetter.subset(font)


def collect_sample_weights(
    *,
    base_font: TTFont,
    donor_font: TTFont,
    requested_min: float,
    requested_default: float,
    requested_max: float,
    effective_min: float,
    effective_max: float,
) -> list[float]:
    samples: set[float] = {requested_min, requested_default, requested_max, effective_min, effective_max}
    first_hundred: int = math.ceil(effective_min / 100.0) * 100
    last_hundred: int = math.floor(effective_max / 100.0) * 100
    for weight in range(first_hundred, last_hundred + 1, 100):
        samples.add(float(weight))
    for font in (base_font, donor_font):
        for instance in font["fvar"].instances:
            value: float | None = instance.coordinates.get("wght")
            if value is not None and effective_min <= value <= effective_max:
                samples.add(float(value))
    ordered: list[float] = sorted(samples)
    if len(ordered) > 64:
        raise FontBuildError("wght sample이 64개를 초과합니다")
    return ordered


def create_designspace(
    *,
    master_paths: list[tuple[float, Path]],
    minimum: float,
    default: float,
    maximum: float,
) -> DesignSpaceDocument:
    designspace = DesignSpaceDocument()
    axis = AxisDescriptor()
    axis.name = "Weight"
    axis.tag = "wght"
    axis.minimum = minimum
    axis.default = default
    axis.maximum = maximum
    designspace.addAxis(axis)

    for index, (weight, path) in enumerate(master_paths):
        source = SourceDescriptor()
        source.name = f"master-{index:02d}"
        source.path = str(path)
        source.location = {"Weight": weight}
        if weight == default:
            source.copyInfo = True
            source.copyLib = True
            source.copyFeatures = True
        designspace.addSource(source)
    return designspace


def allocate_cff_names(*, font: TTFont, codepoints: set[int]) -> dict[int, str]:
    top_dict = font["CFF "].cff.topDictIndex[0]
    if hasattr(top_dict, "FDArray"):
        used_cids: set[int] = {int(name[3:]) for name in top_dict.charset if re.fullmatch(r"cid\d+", name) is not None}
        available_cids: Iterable[int] = (cid for cid in range(1, 65536) if cid not in used_cids)
        names: dict[int, str] = {}
        for codepoint, cid in zip(sorted(codepoints), available_cids, strict=False):
            names[codepoint] = f"cid{cid:05d}"
        if len(names) != len(codepoints):
            raise FontBuildError("CID-keyed CFF2에 donor glyph를 추가할 CID 공간이 부족합니다")
        return names
    return {codepoint: f"cfw{codepoint:06X}" for codepoint in sorted(codepoints)}


def append_cff_glyphs(
    *,
    base_font: TTFont,
    donor_font: TTFont,
    codepoints: set[int],
    target_names: dict[int, str],
) -> None:
    if not codepoints:
        return
    base_cff = base_font["CFF "].cff
    top_dict = base_cff.topDictIndex[0]
    donor_cmap: dict[int, str] = dict(donor_font.getBestCmap() or {})
    donor_glyph_set = donor_font.getGlyphSet()
    glyph_order: list[str] = list(base_font.getGlyphOrder())

    for codepoint in sorted(codepoints):
        donor_name: str = donor_cmap[codepoint]
        target_name: str = target_names[codepoint]
        advance_width: int = int(donor_font["hmtx"].metrics[donor_name][0])
        pen = T2CharStringPen(width=advance_width, glyphSet=None, CFF2=False)
        donor_glyph_set[donor_name].draw(pen)
        if hasattr(top_dict, "FDArray"):
            fd_index: int | None = 0
            private_dict = top_dict.FDArray[fd_index].Private
        else:
            fd_index = None
            private_dict = top_dict.Private
        char_string = pen.getCharString(private=private_dict, globalSubrs=[])
        char_strings_index = top_dict.CharStrings.charStringsIndex
        char_strings_index.append(char_string)
        top_dict.CharStrings.charStrings[target_name] = len(char_strings_index) - 1
        top_dict.charset.append(target_name)
        if fd_index is not None:
            top_dict.FDSelect.gidArray.append(fd_index)
        glyph_order.append(target_name)
        base_font["hmtx"].metrics[target_name] = donor_font["hmtx"].metrics[donor_name]
        add_cmap_mapping(font=base_font, codepoint=codepoint, glyph_name=target_name)

    base_font.setGlyphOrder(glyph_order)
    base_font["maxp"].numGlyphs = len(glyph_order)


def add_cmap_mapping(*, font: TTFont, codepoint: int, glyph_name: str) -> None:
    mapped: bool = False
    for cmap_table in font["cmap"].tables:
        if cmap_table.isUnicode() and hasattr(cmap_table, "cmap"):
            if codepoint <= 0xFFFF or cmap_table.format in {12, 13}:
                cmap_table.cmap[codepoint] = glyph_name
                mapped = True
    if not mapped:
        raise FontBuildError(f"U+{codepoint:04X}를 표현할 Unicode cmap format이 없습니다")


def update_names(
    *,
    font: TTFont,
    family_name: str,
    base_copyright: str,
    donor_copyright: str,
    base_license: str,
    donor_license: str,
) -> None:
    name_table = font["name"]
    postscript_name: str = re.sub(r"[^A-Za-z0-9-]", "", family_name.replace(" ", "-")) or "CustomFont"
    copyright_value: str = combine_unique(base_copyright, donor_copyright)
    license_value: str = combine_unique(base_license, donor_license)
    records: dict[int, str] = {
        0: copyright_value,
        1: family_name,
        2: "Regular",
        3: f"{family_name};CustomFontWizard;1.0",
        4: family_name,
        6: postscript_name,
        16: family_name,
        17: "Regular",
        25: postscript_name,
    }
    if license_value:
        records[13] = license_value

    for name_id, value in records.items():
        name_table.setName(value, name_id, 3, 1, 0x409)
        name_table.setName(value, name_id, 1, 0, 0)


def verify_output(
    *,
    output_path: Path,
    flavor: FontFlavor,
    expected_codepoints: set[int],
    weight_min: float,
    weight_max: float,
) -> None:
    font: TTFont = TTFont(output_path, recalcTimestamp=False)
    try:
        actual_flavor: FontFlavor = detect_flavor(font=font)
        if actual_flavor != flavor:
            raise FontBuildError("Output outline format이 Base와 다릅니다")
        axis_min, _, axis_max = weight_axis_values(font=font)
        if axis_min != weight_min or axis_max != weight_max:
            raise FontBuildError("Output wght range가 요청과 다릅니다")
        cmap: dict[int, str] = dict(font.getBestCmap() or {})
        missing: set[int] = expected_codepoints - set(cmap)
        if missing:
            sample: str = ", ".join(f"U+{codepoint:04X}" for codepoint in sorted(missing)[:8])
            raise FontBuildError(f"Output cmap에서 codepoint가 누락되었습니다: {sample}")
        for codepoint in expected_codepoints:
            if chr(codepoint).isspace():
                continue
            glyph_name: str = cmap[codepoint]
            if glyph_is_blank(font=font, glyph_name=glyph_name):
                raise FontBuildError(f"Output glyph가 비어 있습니다: U+{codepoint:04X}")
    finally:
        font.close()


def validate_output_suffix(*, output_path: Path, flavor: FontFlavor) -> None:
    expected_suffix: str = ".ttf" if flavor == "ttf" else ".otf"
    if output_path.suffix.lower() != expected_suffix:
        raise FontBuildError(f"Output extension은 {expected_suffix}여야 합니다")


def validate_family_name(*, family_name: str, base_info: dict[str, object], donor_info: dict[str, object]) -> None:
    normalized: str = family_name.strip().casefold()
    if not normalized:
        raise FontBuildError("Family name이 비어 있습니다")
    source_names: set[str] = {
        str(base_info["family"]).strip().casefold(),
        str(donor_info["family"]).strip().casefold(),
    }
    if normalized in source_names:
        raise FontBuildError("합성 font에는 Base 또는 Donor와 다른 family name을 사용해야 합니다")


def name_value(*, font: TTFont, name_id: int) -> str:
    value: str | None = font["name"].getDebugName(name_id)
    return value or ""


def combine_unique(first: str, second: str) -> str:
    values: list[str] = []
    for value in (first.strip(), second.strip()):
        if value and value not in values:
            values.append(value)
    return "\n".join(values)


def clamp(*, value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def require_object(*, value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise FontBuildError(f"{label} is not an object")
    if not all(isinstance(key, str) for key in value):
        raise FontBuildError(f"{label} contains a non-string key")
    return cast("dict[str, object]", value)


def require_flavor(*, value: object) -> FontFlavor:
    if value == "ttf":
        return "ttf"
    if value == "otf":
        return "otf"
    raise FontBuildError("invalid font flavor")


def units_per_em(*, font: TTFont) -> int:
    value: object = getattr(font["head"], "unitsPerEm", None)
    if not isinstance(value, int):
        raise FontBuildError("head.unitsPerEm이 유효하지 않습니다")
    return value
