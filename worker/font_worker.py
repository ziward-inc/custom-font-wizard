from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from worker.font_engine import BuildStep, BuildStepStatus, analyze_fonts, build_font


class RequestError(ValueError):
    pass


def write_event(*, event: Mapping[str, object]) -> None:
    json.dump(event, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    sys.stdout.flush()


def write_progress_event(*, step: BuildStep, status: BuildStepStatus, message: str) -> None:
    write_event(
        event={
            "type": "progress",
            "step": step,
            "status": status,
            "message": message,
        }
    )


def read_request() -> Mapping[str, object]:
    raw_request: object = json.load(sys.stdin)
    if not isinstance(raw_request, Mapping):
        raise RequestError("request must be a JSON object")
    if not all(isinstance(key, str) for key in raw_request):
        raise RequestError("request keys must be strings")
    return cast("Mapping[str, object]", raw_request)


def require_string(request: Mapping[str, object], key: str) -> str:
    value: object | None = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RequestError(f"{key} must be a non-empty string")
    return value


def require_number(request: Mapping[str, object], key: str) -> float:
    value: object | None = request.get(key)
    if not isinstance(value, int | float):
        raise RequestError(f"{key} must be a number")
    return float(value)


def require_codepoints(request: Mapping[str, object]) -> list[int]:
    value: object | None = request.get("codepoints")
    if not isinstance(value, list):
        raise RequestError("codepoints must be an array")

    codepoints: list[int] = []
    for item in value:
        if not isinstance(item, int) or not 0 <= item <= 0x10FFFF:
            raise RequestError("codepoints must contain valid Unicode scalar values")
        codepoints.append(item)
    return sorted(set(codepoints))


def run_analyze(request: Mapping[str, object]) -> dict[str, object]:
    base_path: Path = Path(require_string(request, "base_path"))
    donor_path: Path = Path(require_string(request, "donor_path"))
    return analyze_fonts(base_path=base_path, donor_path=donor_path)


def run_build(request: Mapping[str, object]) -> dict[str, object]:
    base_path: Path = Path(require_string(request, "base_path"))
    donor_path: Path = Path(require_string(request, "donor_path"))
    output_path: Path = Path(require_string(request, "output_path"))
    family_name: str = require_string(request, "family_name")
    weight_min: float = require_number(request, "weight_min")
    weight_max: float = require_number(request, "weight_max")
    codepoints: list[int] = require_codepoints(request)
    return build_font(
        base_path=base_path,
        donor_path=donor_path,
        output_path=output_path,
        family_name=family_name,
        weight_min=weight_min,
        weight_max=weight_max,
        selected_codepoints=codepoints,
        progress=write_progress_event,
    )


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"analyze", "build"}:
        raise RequestError("usage: font_worker.py analyze|build")

    action: str = sys.argv[1]
    request: Mapping[str, object] = read_request()
    if action == "analyze":
        response: dict[str, object] = run_analyze(request)
        json.dump(response, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    else:
        result: dict[str, object] = run_build(request)
        write_event(event={"type": "result", "result": result})


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if len(sys.argv) == 2 and sys.argv[1] == "build":
            write_event(event={"type": "error", "message": str(error)})
        else:
            print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
