from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast

from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from worker.font_engine import BuildStep, BuildStepStatus, analyze_fonts, build_font

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

    def test_build_uses_default_weight_donor_gsub_across_masters(self) -> None:
        with tempfile.TemporaryDirectory(prefix="custom-font-wizard-test-") as temporary_directory:
            output_path: Path = Path(temporary_directory) / "Kana.ttf"
            result: dict[str, object] = build_font(
                base_path=BASE_TTF,
                donor_path=DONOR_TTF,
                output_path=output_path,
                family_name="Custom Font Wizard Kana Test",
                weight_min=300,
                weight_max=700,
                selected_codepoints=[0x30AC],
            )

            self.assertEqual(result["donor_added"], 1)
            font: TTFont = TTFont(output_path)
            self.assertIn("GSUB", font)
            self.assertEqual(set(font.getBestCmap() or {}), {0x30AC})
            font.close()


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


if __name__ == "__main__":
    unittest.main()
