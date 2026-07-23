from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import Protocol, cast

from fontTools.designspaceLib import AxisDescriptor, DesignSpaceDocument, SourceDescriptor
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.otlLib.builder import buildLookup, buildSinglePos, buildValue
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.scaleUpem import scale_upem
from fontTools.ttLib.tables import otTables
from fontTools.varLib import build as build_variable_font
from fontTools.varLib.builder import buildVarDevTable
from fontTools.varLib.featureVars import addFeatureVariations, addFeatureVariationsRaw
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.varLib.varStore import OnlineVarStoreBuilder

from worker.font_engine import BuildStep, BuildStepStatus, analyze_fonts, build_font, subset_font

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
BASE_TTF: Path = PROJECT_ROOT / "SUITE-Variable-ttf/SUITE-Variable.ttf"
DONOR_TTF: Path = PROJECT_ROOT / "PretendardVariable.ttf"


class TestGdef(Protocol):
    Version: int
    GlyphClassDef: object | None
    AttachList: object | None
    LigCaretList: object | None
    MarkAttachClassDef: object | None
    MarkGlyphSetsDef: object | None
    VarStore: object


class ObjectFactory(Protocol):
    def __call__(self) -> object: ...


class FontWorkerTests(unittest.TestCase):
    def test_analyze_finds_blank_and_missing_glyphs(self) -> None:
        result: dict[str, object] = analyze_fonts(base_path=BASE_TTF, donor_path=DONOR_TTF)
        codepoints: object = result["codepoints"]

        self.assertIsInstance(codepoints, list)
        assert isinstance(codepoints, list)
        self.assertEqual(len(codepoints), 14_357)
        repaired: int = 0
        for item in codepoints:
            if not isinstance(item, dict) or not all(isinstance(key, str) for key in item):
                continue
            coverage: dict[str, object] = cast("dict[str, object]", item)
            if coverage.get("base") != "visible" and coverage.get("donor") == "visible":
                repaired += 1
        self.assertEqual(repaired, 11_403)

    def test_build_repairs_ttf_and_clamps_weight(self) -> None:
        with tempfile.TemporaryDirectory(prefix="custom-font-wizard-test-") as temporary_directory:
            output_path: Path = Path(temporary_directory) / "Smoke.ttf"
            progress_events: list[tuple[BuildStep, BuildStepStatus, str]] = []

            def record_progress(*, step: BuildStep, status: BuildStepStatus, message: str) -> None:
                progress_events.append((step, status, message))

            result: dict[str, object] = build_font(
                base_path=BASE_TTF,
                donor_path=DONOR_TTF,
                output_path=output_path,
                family_name="Custom Font Wizard Test",
                weight_min=100,
                weight_max=400,
                selected_codepoints=[0x2000, 0xAC00, 0xAC02],
                progress=record_progress,
            )

            self.assertEqual(result["donor_repaired"], 1)
            self.assertEqual(result["donor_added"], 1)
            completed_steps: list[BuildStep] = [step for step, status, _ in progress_events if status == "completed"]
            self.assertEqual(
                completed_steps,
                [
                    "validate_inputs",
                    "analyze_sources",
                    "prepare_glyphs",
                    "generate_masters",
                    "build_variable_font",
                    "save_output",
                    "verify_output",
                ],
            )
            self.assertTrue(any("Master" in message for _, _, message in progress_events))
            font: TTFont = TTFont(output_path)
            self.assertIn("gvar", font)
            axis = font["fvar"].axes[0]
            self.assertEqual((axis.minValue, axis.defaultValue, axis.maxValue), (100, 300, 400))
            self.assertEqual(set(font.getBestCmap() or {}), {0x2000, 0xAC00, 0xAC02})
            font.close()

            for codepoint in (0xAC00, 0xAC02):
                minimum_outline: list[tuple[str, tuple[object, ...]]] = outline_at(
                    font_path=output_path,
                    codepoint=codepoint,
                    weight=100,
                )
                source_minimum_outline: list[tuple[str, tuple[object, ...]]] = outline_at(
                    font_path=output_path,
                    codepoint=codepoint,
                    weight=300,
                )
                self.assertEqual(minimum_outline, source_minimum_outline)

    def test_build_allows_source_family_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="custom-font-wizard-test-") as temporary_directory:
            for source_path in (BASE_TTF, DONOR_TTF):
                source_font: TTFont = TTFont(source_path)
                family_name: str | None = source_font["name"].getDebugName(16) or source_font["name"].getDebugName(1)
                source_font.close()
                self.assertIsNotNone(family_name)
                assert family_name is not None

                with self.subTest(family_name=family_name):
                    output_path: Path = Path(temporary_directory) / f"{source_path.stem}-Family.ttf"
                    build_font(
                        base_path=BASE_TTF,
                        donor_path=DONOR_TTF,
                        output_path=output_path,
                        family_name=family_name,
                        weight_min=300,
                        weight_max=400,
                        selected_codepoints=[0x0041],
                    )

                    font: TTFont = TTFont(output_path)
                    self.assertEqual(font["name"].getDebugName(16), family_name)
                    font.close()

    def test_build_preserves_donor_gsub_feature_variations_across_static_masters(self) -> None:
        with tempfile.TemporaryDirectory(prefix="custom-font-wizard-test-") as temporary_directory:
            output_path: Path = Path(temporary_directory) / "Kana.ttf"
            result: dict[str, object] = build_font(
                base_path=BASE_TTF,
                donor_path=DONOR_TTF,
                output_path=output_path,
                family_name="Custom Font Wizard Kana Test",
                weight_min=100,
                weight_max=700,
                selected_codepoints=[0x30AC],
            )

            self.assertEqual(result["donor_added"], 1)
            font: TTFont = TTFont(output_path)
            self.assertIn("GSUB", font)
            self.assertEqual(set(font.getBestCmap() or {}), {0x30AC})
            font.close()

            for weight, expected_glyph in (
                (450, "uni30AC.varAlt01"),
                (475, "uni30AC.varAlt01"),
                (700, "uni30AC.varAlt02"),
            ):
                self.assertEqual(
                    substituted_glyph_at(font_path=output_path, codepoint=0x30AC, weight=weight),
                    expected_glyph,
                )
                self.assertEqual(
                    substituted_glyph_at(font_path=output_path, codepoint=0x30AC, weight=weight),
                    substituted_glyph_at(font_path=DONOR_TTF, codepoint=0x30AC, weight=weight),
                )

    def test_build_preserves_base_gsub_feature_variations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="custom-font-wizard-test-") as temporary_directory:
            temporary_root: Path = Path(temporary_directory)
            base_path: Path = temporary_root / "BaseFeatureVariations.ttf"
            add_base_feature_variations(output_path=base_path)

            for weight_min, weight_max in ((300, 900), (100, 700)):
                with self.subTest(weight_min=weight_min, weight_max=weight_max):
                    output_path: Path = temporary_root / f"Base-{weight_min}-{weight_max}.ttf"
                    build_font(
                        base_path=base_path,
                        donor_path=DONOR_TTF,
                        output_path=output_path,
                        family_name="Custom Font Wizard Base Feature Variations Test",
                        weight_min=weight_min,
                        weight_max=weight_max,
                        selected_codepoints=[0x0041],
                    )

                    self.assertEqual(
                        substituted_glyph_at(font_path=output_path, codepoint=0x0041, weight=400),
                        "A",
                    )
                    self.assertEqual(
                        substituted_glyph_at(font_path=output_path, codepoint=0x0041, weight=600),
                        "B",
                    )

    def test_build_preserves_donor_non_single_subst_feature_variations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="custom-font-wizard-test-") as temporary_directory:
            temporary_root: Path = Path(temporary_directory)
            donor_path: Path = temporary_root / "DonorMultipleSubst.ttf"
            add_donor_multiple_subst_feature_variations(output_path=donor_path)
            output_path: Path = temporary_root / "DonorMultipleSubstOutput.ttf"

            build_font(
                base_path=BASE_TTF,
                donor_path=donor_path,
                output_path=output_path,
                family_name="Custom Font Wizard Donor MultipleSubst Test",
                weight_min=100,
                weight_max=700,
                selected_codepoints=[0x30AC],
            )

            self.assertEqual(
                multiple_substitution_at(font_path=output_path, codepoint=0x30AC, weight=400),
                (),
            )
            self.assertEqual(
                multiple_substitution_at(font_path=output_path, codepoint=0x30AC, weight=650),
                ("uni30AB", "uni30AD"),
            )

    def test_build_preserves_gpos_variation_and_feature_variations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="custom-font-wizard-test-") as temporary_directory:
            temporary_root: Path = Path(temporary_directory)
            base_path: Path = temporary_root / "BaseGposVariations.ttf"
            add_gpos_variations(output_path=base_path)

            for weight_min, weight_max, samples in (
                (300, 900, ((300, 300), (450, 450), (600, 600), (900, 900))),
                (100, 700, ((100, 300), (400, 400), (600, 600), (700, 700))),
            ):
                with self.subTest(weight_min=weight_min, weight_max=weight_max):
                    output_path: Path = temporary_root / f"BaseGposVariations-{weight_min}-{weight_max}.ttf"
                    build_font(
                        base_path=base_path,
                        donor_path=DONOR_TTF,
                        output_path=output_path,
                        family_name="Custom Font Wizard GPOS Variations Test",
                        weight_min=weight_min,
                        weight_max=weight_max,
                        selected_codepoints=[0x0041],
                    )

                    for output_weight, source_weight in samples:
                        self.assertEqual(
                            positioning_at(font_path=output_path, codepoint=0x0041, weight=output_weight),
                            positioning_at(font_path=base_path, codepoint=0x0041, weight=source_weight),
                        )

    def test_build_merges_otf_donor_layout_and_position_variation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="custom-font-wizard-test-") as temporary_directory:
            temporary_root: Path = Path(temporary_directory)
            base_path: Path = temporary_root / "BaseVariable.otf"
            donor_path: Path = temporary_root / "DonorVariable.otf"
            output_path: Path = temporary_root / "MergedVariable.otf"
            build_test_variable_otf(
                output_path=base_path,
                temporary_root=temporary_root / "base-masters",
                family_name="Base CFF2 Test",
                visible_b=False,
                include_layout=False,
            )
            build_test_variable_otf(
                output_path=donor_path,
                temporary_root=temporary_root / "donor-masters",
                family_name="Donor CFF2 Test",
                visible_b=True,
                include_layout=True,
            )

            result: dict[str, object] = build_font(
                base_path=base_path,
                donor_path=donor_path,
                output_path=output_path,
                family_name="Custom Font Wizard CFF2 Layout Test",
                weight_min=300,
                weight_max=900,
                selected_codepoints=[0x0042],
            )

            self.assertEqual(result["donor_repaired"], 1)
            output_font: TTFont = TTFont(output_path)
            self.assertIn("CFF2", output_font)
            mapped_glyph: str = dict(output_font.getBestCmap() or {})[0x0042]
            output_font.close()
            substituted_glyph_name: str = substituted_glyph_at(
                font_path=output_path,
                codepoint=0x0042,
                weight=600,
            )
            self.assertNotEqual(substituted_glyph_name, mapped_glyph)
            for weight in (300, 600, 900):
                self.assertEqual(
                    positioning_at(font_path=output_path, codepoint=0x0042, weight=weight),
                    positioning_at(font_path=donor_path, codepoint=0x0042, weight=weight),
                )

    def test_build_preserves_ttf_variations_and_adds_weight_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="custom-font-wizard-test-") as temporary_directory:
            output_path: Path = Path(temporary_directory) / "Direct.ttf"
            family_name: str = "Custom Font Wizard Direct Test"
            build_font(
                base_path=BASE_TTF,
                donor_path=DONOR_TTF,
                output_path=output_path,
                family_name=family_name,
                weight_min=300,
                weight_max=900,
                selected_codepoints=[0x0041, 0x30AC, 0xAC02],
            )

            font: TTFont = TTFont(output_path)
            instances: list[tuple[float, str | None]] = [
                (float(instance.coordinates["wght"]), font["name"].getDebugName(instance.subfamilyNameID))
                for instance in font["fvar"].instances
            ]
            self.assertEqual(
                instances,
                [
                    (300, "Light"),
                    (400, "Regular"),
                    (500, "Medium"),
                    (600, "SemiBold"),
                    (700, "Bold"),
                    (800, "ExtraBold"),
                    (900, "Heavy"),
                ],
            )
            self.assertEqual(font["name"].getDebugName(1), f"{family_name} Light")
            self.assertEqual(font["name"].getDebugName(16), family_name)
            self.assertEqual(font["name"].getDebugName(17), "Light")
            stat_values = font["STAT"].table.AxisValueArray.AxisValue
            self.assertEqual({float(value.Value) for value in stat_values}, set(range(300, 901, 100)))
            self.assertNotIn("HVAR", font)
            font.close()

            for codepoint, source_path, should_scale in (
                (0x0041, BASE_TTF, False),
                (0xAC02, DONOR_TTF, True),
            ):
                self.assertEqual(
                    outline_at(font_path=output_path, codepoint=codepoint, weight=500),
                    source_outline_at(
                        font_path=source_path,
                        codepoint=codepoint,
                        weight=500,
                        scale_to_base=should_scale,
                    ),
                )
            self.assertNotEqual(
                outline_at(font_path=output_path, codepoint=0xAC02, weight=300),
                outline_at(font_path=output_path, codepoint=0xAC02, weight=900),
            )
            for weight, expected_glyph in (
                (450, "uni30AC.varAlt01"),
                (475, "uni30AC.varAlt01"),
                (700, "uni30AC.varAlt02"),
            ):
                self.assertEqual(
                    substituted_glyph_at(font_path=output_path, codepoint=0x30AC, weight=weight),
                    expected_glyph,
                )
                self.assertEqual(
                    substituted_glyph_at(font_path=output_path, codepoint=0x30AC, weight=weight),
                    substituted_glyph_at(font_path=DONOR_TTF, codepoint=0x30AC, weight=weight),
                )
            self.assertNotEqual(
                substituted_outline_at(font_path=output_path, codepoint=0x30AC, weight=450),
                substituted_outline_at(font_path=output_path, codepoint=0x30AC, weight=475),
            )


def outline_at(*, font_path: Path, codepoint: int, weight: float) -> list[tuple[str, tuple[object, ...]]]:
    font: TTFont = TTFont(font_path)
    instance: TTFont = instantiateVariableFont(font, {"wght": weight}, inplace=True, static=True)
    cmap: dict[int, str] = dict(instance.getBestCmap() or {})
    glyph_name: str = cmap[codepoint]
    pen = RecordingPen()
    instance.getGlyphSet()[glyph_name].draw(pen)
    outline: list[tuple[str, tuple[object, ...]]] = list(pen.value)
    instance.close()
    return outline


def substituted_glyph_at(*, font_path: Path, codepoint: int, weight: float) -> str:
    font: TTFont = TTFont(font_path)
    instance: TTFont = instantiateVariableFont(font, {"wght": weight}, inplace=True, static=True)
    glyph_name: str = substituted_glyph(font=instance, codepoint=codepoint)
    instance.close()
    return glyph_name


def multiple_substitution_at(*, font_path: Path, codepoint: int, weight: float) -> tuple[str, ...]:
    font: TTFont = TTFont(font_path)
    instance: TTFont = instantiateVariableFont(font, {"wght": weight}, inplace=True, static=True)
    glyph_name: str = dict(instance.getBestCmap() or {})[codepoint]
    gsub = instance["GSUB"].table
    substitutions: tuple[str, ...] = ()
    for feature_record in gsub.FeatureList.FeatureRecord:
        if feature_record.FeatureTag != "rlig":
            continue
        for lookup_index in feature_record.Feature.LookupListIndex:
            lookup = gsub.LookupList.Lookup[lookup_index]
            for subtable in lookup.SubTable:
                multiple_subst = subtable.ExtSubTable if lookup.LookupType == 7 else subtable
                targets: list[str] | None = getattr(multiple_subst, "mapping", {}).get(glyph_name)
                if lookup.LookupType in {2, 7} and targets is not None:
                    substitutions = tuple(targets)
                    break
    instance.close()
    return substitutions


def positioning_at(*, font_path: Path, codepoint: int, weight: float) -> int:
    font: TTFont = TTFont(font_path)
    instance: TTFont = instantiateVariableFont(font, {"wght": weight}, inplace=True, static=True)
    glyph_name: str = dict(instance.getBestCmap() or {})[codepoint]
    gpos = instance["GPOS"].table
    value: int = 0
    for feature_record in gpos.FeatureList.FeatureRecord:
        if feature_record.FeatureTag != "zzzz":
            continue
        for lookup_index in feature_record.Feature.LookupListIndex:
            lookup = gpos.LookupList.Lookup[lookup_index]
            for subtable in lookup.SubTable:
                single_pos = subtable.ExtSubTable if lookup.LookupType == 9 else subtable
                if glyph_name not in single_pos.Coverage.glyphs:
                    continue
                position = (
                    single_pos.Value
                    if single_pos.Format == 1
                    else single_pos.Value[single_pos.Coverage.glyphs.index(glyph_name)]
                )
                value += int(getattr(position, "XAdvance", 0))
    instance.close()
    return value


def substituted_outline_at(
    *,
    font_path: Path,
    codepoint: int,
    weight: float,
) -> list[tuple[str, tuple[object, ...]]]:
    font: TTFont = TTFont(font_path)
    instance: TTFont = instantiateVariableFont(font, {"wght": weight}, inplace=True, static=True)
    glyph_name: str = substituted_glyph(font=instance, codepoint=codepoint)
    pen = RecordingPen()
    instance.getGlyphSet()[glyph_name].draw(pen)
    outline: list[tuple[str, tuple[object, ...]]] = list(pen.value)
    instance.close()
    return outline


def substituted_glyph(*, font: TTFont, codepoint: int) -> str:
    glyph_name: str = dict(font.getBestCmap() or {})[codepoint]
    gsub = font["GSUB"].table
    feature = next(
        (
            feature_record.Feature
            for feature_record in gsub.FeatureList.FeatureRecord
            if feature_record.FeatureTag == "rlig"
        ),
        None,
    )
    if feature is None:
        return glyph_name
    for lookup_index in feature.LookupListIndex:
        lookup = gsub.LookupList.Lookup[lookup_index]
        for subtable in lookup.SubTable:
            single_subst = subtable.ExtSubTable if lookup.LookupType == 7 else subtable
            target: str | None = single_subst.mapping.get(glyph_name)
            if target is not None:
                glyph_name = target
                break
    return glyph_name


def add_base_feature_variations(*, output_path: Path) -> None:
    font: TTFont = TTFont(BASE_TTF)
    addFeatureVariations(
        font,
        [([{"wght": (0.25, 1.0)}], {"A": "B"})],
        featureTag="rlig",
    )
    font.save(output_path)
    font.close()


def add_donor_multiple_subst_feature_variations(*, output_path: Path) -> None:
    font: TTFont = TTFont(DONOR_TTF)
    gsub = font["GSUB"].table
    gsub.FeatureVariations = None
    gsub.Version = 0x00010000

    multiple_subst = otTables.MultipleSubst()
    multiple_subst.mapping = {"uni30AC": ["uni30AB", "uni30AD"]}
    lookup = buildLookup([multiple_subst])
    lookup_index: int = len(gsub.LookupList.Lookup)
    gsub.LookupList.Lookup.append(lookup)
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
    addFeatureVariationsRaw(
        font,
        gsub,
        [({"wght": (0.2, 1.0)}, [lookup_index])],
        featureTag="rlig",
    )
    font.save(output_path)
    font.close()


def add_gpos_variations(*, output_path: Path) -> None:
    font: TTFont = TTFont(BASE_TTF)
    addOpenTypeFeaturesFromString(font, "feature zzzz { pos A -20; } zzzz;")
    gpos = font["GPOS"].table

    store_builder = OnlineVarStoreBuilder(["wght"])
    store_builder.setSupports([{"wght": (0.0, 1.0, 1.0)}])
    baseline_var_index: int = store_builder.storeDeltas([120])
    alternate_var_index: int = store_builder.storeDeltas([60])
    if "GDEF" not in font:
        gdef_table = font["GDEF"] = newTable("GDEF")
        gdef_class: object = getattr(otTables, "GDEF", None)
        if not callable(gdef_class):
            raise RuntimeError("fontTools GDEF class를 찾을 수 없습니다")
        gdef_factory: ObjectFactory = cast("ObjectFactory", gdef_class)
        gdef_object: object = gdef_factory()
        gdef: TestGdef = cast("TestGdef", gdef_object)
        setattr(gdef_table, "table", gdef)
        gdef.GlyphClassDef = None
        gdef.AttachList = None
        gdef.LigCaretList = None
        gdef.MarkAttachClassDef = None
        gdef.MarkGlyphSetsDef = None
    else:
        gdef = cast("TestGdef", font["GDEF"].table)
    gdef.Version = 0x00010003
    gdef.VarStore = store_builder.finish()

    baseline_feature = next(
        feature_record.Feature
        for feature_record in gpos.FeatureList.FeatureRecord
        if feature_record.FeatureTag == "zzzz"
    )
    baseline_lookup = gpos.LookupList.Lookup[baseline_feature.LookupListIndex[0]]
    baseline_subtable = baseline_lookup.SubTable[0]
    baseline_value = baseline_subtable.Value if baseline_subtable.Format == 1 else baseline_subtable.Value[0]
    baseline_value.XAdvDevice = buildVarDevTable(baseline_var_index)
    baseline_subtable.ValueFormat |= 0x0040

    alternate_value = buildValue(
        {
            "XAdvance": 200,
            "XAdvDevice": buildVarDevTable(alternate_var_index),
        }
    )
    alternate_subtables = buildSinglePos({"A": alternate_value}, font.getReverseGlyphMap())
    alternate_lookup = buildLookup(alternate_subtables)
    alternate_lookup_index: int = len(gpos.LookupList.Lookup)
    gpos.LookupList.Lookup.append(alternate_lookup)
    gpos.LookupList.LookupCount = len(gpos.LookupList.Lookup)
    addFeatureVariationsRaw(
        font,
        gpos,
        [({"wght": (0.25, 1.0)}, [alternate_lookup_index])],
        featureTag="zzzz",
    )
    font.save(output_path)
    font.close()


def build_test_variable_otf(
    *,
    output_path: Path,
    temporary_root: Path,
    family_name: str,
    visible_b: bool,
    include_layout: bool,
) -> None:
    temporary_root.mkdir()
    master_paths: list[tuple[float, Path]] = []
    for weight, advance_adjustment in ((300.0, -20), (900.0, -80)):
        master_path: Path = temporary_root / f"master-{weight:g}.otf"
        build_test_static_otf(
            output_path=master_path,
            family_name=family_name,
            visible_b=visible_b,
            include_layout=include_layout,
            advance_adjustment=advance_adjustment,
            outline_shift=int((weight - 300) / 6),
        )
        master_paths.append((weight, master_path))

    designspace = DesignSpaceDocument()
    axis = AxisDescriptor()
    axis.name = "Weight"
    axis.tag = "wght"
    axis.minimum = 300
    axis.default = 300
    axis.maximum = 900
    designspace.addAxis(axis)
    for index, (weight, master_path) in enumerate(master_paths):
        source = SourceDescriptor()
        source.name = f"master-{index}"
        source.path = str(master_path)
        source.location = {"Weight": weight}
        if weight == 300:
            source.copyInfo = True
            source.copyLib = True
            source.copyFeatures = True
        designspace.addSource(source)
    variable_font_result: tuple[TTFont, object, list[TTFont]] = build_variable_font(designspace)
    variable_font: TTFont = variable_font_result[0]
    variable_font.save(output_path)
    variable_font.close()


def build_test_static_otf(
    *,
    output_path: Path,
    family_name: str,
    visible_b: bool,
    include_layout: bool,
    advance_adjustment: int,
    outline_shift: int,
) -> None:
    glyph_order: list[str] = [".notdef", "A", "B", "B.alt"]
    char_strings: dict[str, object] = {}
    for glyph_name in glyph_order:
        pen = T2CharStringPen(width=600, glyphSet=None, CFF2=False)
        if glyph_name == "A" or (visible_b and glyph_name in {"B", "B.alt"}):
            inset: int = 50 if glyph_name == "B.alt" else 0
            pen.moveTo((100 + inset, 0))
            pen.lineTo((300, 700 + outline_shift))
            pen.lineTo((500 - inset, 0))
            pen.closePath()
        char_strings[glyph_name] = pen.getCharString()

    builder = FontBuilder(1000, isTTF=False)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap({0x0041: "A", 0x0042: "B"})
    builder.setupCFF(
        f"{family_name.replace(' ', '')}-Regular",
        {"FullName": family_name, "FamilyName": family_name, "Weight": "Regular"},
        char_strings,
        {},
    )
    metrics: dict[str, tuple[int, int]] = {glyph_name: (600, 0) for glyph_name in glyph_order}
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": family_name,
            "styleName": "Regular",
            "uniqueFontIdentifier": f"{family_name};Regular",
            "fullName": f"{family_name} Regular",
            "psName": f"{family_name.replace(' ', '')}-Regular",
            "version": "Version 1.0",
            "typographicFamily": family_name,
            "typographicSubfamily": "Regular",
        }
    )
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
        usWeightClass=400,
    )
    builder.setupPost()
    font: TTFont = builder.font
    if include_layout:
        feature_text: str = (
            f"feature rlig {{ sub B by B.alt; }} rlig; feature zzzz {{ pos B {advance_adjustment}; }} zzzz;"
        )
        addOpenTypeFeaturesFromString(font, feature_text)
    font.save(output_path)
    font.close()


def source_outline_at(
    *,
    font_path: Path,
    codepoint: int,
    weight: float,
    scale_to_base: bool,
) -> list[tuple[str, tuple[object, ...]]]:
    font: TTFont = TTFont(font_path)
    font = instantiateVariableFont(font, {"wght": (300, 300, 900)}, inplace=True, static=False)
    if scale_to_base:
        base_font: TTFont = TTFont(BASE_TTF)
        base_upem: int = cast("int", getattr(base_font["head"], "unitsPerEm"))
        scale_upem(font, base_upem)
        base_font.close()
    for glyph_name in font.getGlyphOrder():
        if glyph_name not in font["gvar"].variations:
            font["gvar"].variations[glyph_name] = []
    subset_font(font=font, codepoints={codepoint})
    compiled_font = BytesIO()
    font.save(compiled_font)
    font.close()
    compiled_font.seek(0)
    font = TTFont(compiled_font)
    instance: TTFont = instantiateVariableFont(font, {"wght": weight}, inplace=True, static=True)
    cmap: dict[int, str] = dict(instance.getBestCmap() or {})
    glyph_name: str = cmap[codepoint]
    pen = RecordingPen()
    instance.getGlyphSet()[glyph_name].draw(pen)
    outline: list[tuple[str, tuple[object, ...]]] = list(pen.value)
    instance.close()
    return outline


if __name__ == "__main__":
    unittest.main()
