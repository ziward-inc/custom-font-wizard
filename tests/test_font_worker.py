from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import cast

from fontTools.otlLib.builder import buildLookup
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.scaleUpem import scale_upem
from fontTools.ttLib.tables import otTables
from fontTools.varLib.featureVars import addFeatureVariations, addFeatureVariationsRaw
from fontTools.varLib.instancer import instantiateVariableFont

from worker.font_engine import BuildStep, BuildStepStatus, analyze_fonts, build_font, subset_font

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
BASE_TTF: Path = PROJECT_ROOT / "SUITE-Variable-ttf/SUITE-Variable.ttf"
DONOR_TTF: Path = PROJECT_ROOT / "PretendardVariable.ttf"


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
