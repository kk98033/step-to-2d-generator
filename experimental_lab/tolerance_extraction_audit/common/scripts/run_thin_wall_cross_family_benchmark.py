"""Benchmark generic thin-wall detection across unrelated part families."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.tolerance.assembly_components import load_component_shape  # noqa: E402
from run_classification_tree_audit import _font  # noqa: E402
from thin_wall_dimension_pipeline import extract_thin_wall_dimension  # noqa: E402


METHOD = "THIN_WALL_CROSS_FAMILY_BENCHMARK_V1"


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _synthetic_shape(kind: str):
    if kind == "thin_plate":
        return BRepPrimAPI_MakeBox(40.0, 25.0, 0.8).Shape()
    if kind == "solid_box":
        return BRepPrimAPI_MakeBox(20.0, 20.0, 20.0).Shape()
    if kind == "solid_cylinder":
        return BRepPrimAPI_MakeCylinder(8.0, 30.0).Shape()
    if kind == "annular_cylindrical_wall":
        outer = BRepPrimAPI_MakeCylinder(10.0, 25.0).Shape()
        inner = BRepPrimAPI_MakeCylinder(9.2, 25.0).Shape()
        return BRepAlgoAPI_Cut(outer, inner).Shape()
    raise ValueError(f"Unknown synthetic kind: {kind}")


def _classification(expected: bool | None, resolved: bool) -> str:
    if expected is None:
        return "AUDIT_DETECTED" if resolved else "AUDIT_NOT_DETECTED"
    if expected and resolved:
        return "TRUE_POSITIVE"
    if expected and not resolved:
        return "FALSE_NEGATIVE"
    if not expected and resolved:
        return "FALSE_POSITIVE"
    return "TRUE_NEGATIVE"


def _case_result(
    case: dict[str, Any],
    fractions: list[float],
    experiment15_config: dict[str, Any] | None = None,
    experiment15_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    expected = case.get("expected_detection")
    try:
        if case.get("synthetic_kind"):
            shape = _synthetic_shape(case["synthetic_kind"])
            source_kind, source = "SYNTHETIC", case["synthetic_kind"]
        elif case.get("experiment_15_case"):
            source_name = str(case["experiment_15_case"])
            config_case = next(item for item in (experiment15_config or {}).get("cases", []) if item["name"] == source_name)
            report_case = next(item for item in (experiment15_report or {}).get("cases", []) if item["name"] == source_name)
            shape = load_component_shape(config_case["step_path"], report_case["selected_component"]["label_entry"])
            source_kind, source = "EXPERIMENT_15_COMPONENT", source_name
        else:
            shape = load_step(str(_resolve(case["step_path"])))
            source_kind, source = "REAL_STEP", case["step_path"]
        result = extract_thin_wall_dimension(shape, fractions=fractions)
        resolved = bool(result.get("resolved"))
        dimension = (result.get("dimensions") or [{}])[0]
        aggregate = result.get("aggregate") or {}
        return {
            "name": case["name"],
            "family": case["family"],
            "source_kind": source_kind,
            "source": source,
            "ground_truth": case.get("ground_truth", "AUDIT_ONLY"),
            "expected_detection": expected,
            "resolved": resolved,
            "classification": _classification(expected, resolved),
            "path": result.get("path"),
            "reason": result.get("reason"),
            "semantic": dimension.get("semantic"),
            "nominal_value": dimension.get("nominal_value"),
            "verification_status": dimension.get("verification_status"),
            "confidence": dimension.get("confidence"),
            "station_medians": aggregate.get("station_medians"),
            "periodic_failure": (result.get("periodic_attempt") or {}).get("reason"),
            "planar_failure": (result.get("planar_attempt") or {}).get("reason"),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": None,
        }
    except Exception as exc:
        return {
            "name": case["name"],
            "family": case["family"],
            "source_kind": "SYNTHETIC" if case.get("synthetic_kind") else "REAL_STEP",
            "source": case.get("synthetic_kind") or case.get("step_path"),
            "ground_truth": case.get("ground_truth", "AUDIT_ONLY"),
            "expected_detection": expected,
            "resolved": False,
            "classification": "ERROR",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    labelled = [item for item in cases if item["expected_detection"] is not None]
    counts = {key: sum(item["classification"] == key for item in cases) for key in (
        "TRUE_POSITIVE", "TRUE_NEGATIVE", "FALSE_POSITIVE", "FALSE_NEGATIVE", "ERROR",
    )}
    tp, tn, fp, fn = (counts[key] for key in ("TRUE_POSITIVE", "TRUE_NEGATIVE", "FALSE_POSITIVE", "FALSE_NEGATIVE"))
    return {
        "case_count": len(cases),
        "labelled_case_count": len(labelled),
        "audit_only_case_count": len(cases) - len(labelled),
        **{key.lower(): value for key, value in counts.items()},
        "accuracy": (tp + tn) / max(tp + tn + fp + fn, 1),
        "precision": tp / max(tp + fp, 1),
        "recall": tp / max(tp + fn, 1),
        "auto_verified_count": sum(item.get("verification_status") == "AUTO_VERIFIED" for item in cases),
        "review_candidate_count": sum(item.get("verification_status") == "REVIEW_CANDIDATE" for item in cases),
        "audit_detected_count": sum(item["classification"] == "AUDIT_DETECTED" for item in cases),
        "total_elapsed_seconds": round(sum(float(item["elapsed_seconds"]) for item in cases), 3),
    }


def _render_matrix(report: dict[str, Any], output: Path) -> None:
    cases = report["cases"]
    row_h, header_h = 104, 250
    canvas = Image.new("RGB", (3000, header_h + row_h * len(cases) + 80), "#171717")
    draw = ImageDraw.Draw(canvas)
    draw.text((50, 35), "Experiment 19 | thin-wall cross-family benchmark", font=_font(38, True), fill="#f5f5f5")
    metrics = report["metrics"]
    draw.text(
        (50, 105),
        f"labelled accuracy {metrics['accuracy']:.1%} | precision {metrics['precision']:.1%} | recall {metrics['recall']:.1%} | audit-only {metrics['audit_only_case_count']}",
        font=_font(24, True), fill="#60a5fa",
    )
    draw.text((50, 165), "Blue rows are audit-only and are excluded from accuracy.", font=_font(18), fill="#a3a3a3")
    columns = [(50, "CASE / FAMILY"), (930, "EXPECTED"), (1190, "RESULT"), (1580, "SEMANTIC / VALUE"), (2310, "PATH / REASON")]
    for x, label in columns:
        draw.text((x, 220), label, font=_font(16, True), fill="#a3a3a3")
    colours = {
        "TRUE_POSITIVE": "#22c55e", "TRUE_NEGATIVE": "#22c55e",
        "FALSE_POSITIVE": "#ef4444", "FALSE_NEGATIVE": "#ef4444",
        "ERROR": "#ef4444", "AUDIT_DETECTED": "#60a5fa", "AUDIT_NOT_DETECTED": "#60a5fa",
    }
    for index, case in enumerate(cases):
        y = header_h + index * row_h
        fill = "#202020" if index % 2 == 0 else "#262626"
        draw.rectangle((35, y, 2965, y + row_h - 4), fill=fill)
        colour = colours[case["classification"]]
        draw.rectangle((35, y, 45, y + row_h - 4), fill=colour)
        draw.text((60, y + 16), case["name"], font=_font(18, True), fill="#f5f5f5")
        draw.text((60, y + 55), case["family"], font=_font(15), fill="#a3a3a3")
        expected = "AUDIT" if case["expected_detection"] is None else ("THIN WALL" if case["expected_detection"] else "NOT THIN WALL")
        draw.text((930, y + 31), expected, font=_font(16, True), fill="#d4d4d4")
        draw.text((1190, y + 18), case["classification"], font=_font(16, True), fill=colour)
        draw.text((1190, y + 55), f"{case['elapsed_seconds']:.2f}s", font=_font(14), fill="#a3a3a3")
        semantic = case.get("semantic") or "-"
        value = case.get("nominal_value")
        value_text = "" if value is None else f" | {float(value):.6f} mm"
        draw.text((1580, y + 18), semantic + value_text, font=_font(15, True), fill="#f5f5f5")
        draw.text((1580, y + 55), case.get("verification_status") or "-", font=_font(14), fill="#a3a3a3")
        draw.text((2310, y + 18), case.get("path") or "-", font=_font(14, True), fill="#d4d4d4")
        reason = case.get("error") or case.get("reason") or case.get("periodic_failure") or "-"
        draw.text((2310, y + 55), str(reason)[:70], font=_font(13), fill="#a3a3a3")
    canvas.save(output)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    experiment15_config = json.loads(_resolve(config["experiment_15_config"]).read_text(encoding="utf-8")) if config.get("experiment_15_config") else None
    experiment15_report = json.loads(_resolve(config["experiment_15_report"]).read_text(encoding="utf-8")) if config.get("experiment_15_report") else None
    cases = [
        _case_result(case, config.get("scan_fractions") or [0.1, 0.3, 0.5, 0.6], experiment15_config, experiment15_report)
        for case in config["cases"]
    ]
    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "experimental_main_updated": False,
        "metrics": _metrics(cases),
        "cases": cases,
    }
    (output_dir / "01_benchmark_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _render_matrix(report, output_dir / "02_benchmark_matrix.png")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.output_dir)
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
