"""Run experiment 17: generic paired-surface thickness semantics."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.tolerance.assembly_components import load_component_shape  # noqa: E402
from adaptive_curved_radial_features import detect_adaptive_curved_radial_partitions  # noqa: E402
from blade_section_semantics import section_shape_view  # noqa: E402
from paired_surface_thickness import (  # noqa: E402
    METHOD,
    aggregate_station_thickness,
    extract_parallel_planar_thickness,
    extract_section_boundary_thickness,
)
from run_classification_tree_audit import (  # noqa: E402
    CYAN, GREEN, GRID, MUTED, PANEL, RED, WHITE, YELLOW, _edge_points, _font, _mapper,
)


BACKGROUND = "#0b0b0b"
ORANGE = "#f97316"


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _expanded_bbox(bbox: Sequence[float], fraction: float = 0.08) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = [float(value) for value in bbox]
    pad = max(x1 - x0, y1 - y0, 1e-9) * fraction
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad


def _fit(canvas: Image.Image, image_path: Path, box: Sequence[int]) -> None:
    image = Image.open(image_path).convert("RGB")
    width, height = int(box[2] - box[0]), int(box[3] - box[1])
    scale = min(width / image.width, height / image.height)
    image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
    x = int(box[0] + (width - image.width) / 2)
    y = int(box[1] + (height - image.height) / 2)
    canvas.paste(image, (x, y))


def _midpoint(points: Sequence[Sequence[float]]) -> tuple[float, float]:
    point = points[len(points) // 2]
    return float(point[0]), float(point[1])


def _render_blade_case(case: dict[str, Any], drawing_path: Path | None, output: Path) -> None:
    canvas = Image.new("RGB", (4200, 2400), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((50, 28), f"Experiment 17 | {case['name']} | generic thin-wall thickness", font=_font(39, True), fill=WHITE)
    draw.text((50, 88), "Geometry-only companion boundaries; part name and historical thickness are audit-only.", font=_font(19), fill=MUTED)
    station = case["review_station"]
    view, result = station["view"], station["thickness"]
    left_box = (50, 145, 2920, 1460)
    draw.rectangle(left_box, fill=BACKGROUND, outline=GRID, width=2)
    mapper = _mapper(_expanded_bbox(view["bbox"]), (left_box[0] + 30, left_box[1] + 30, left_box[2] - 30, left_box[3] - 30))
    for edge in view.get("visible") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#737373", width=2)
    shown = result.get("measurements", [])[:12]
    for measurement in shown:
        source = measurement["source_points"]
        companion = measurement["companion_points"]
        draw.line([mapper(point) for point in source], fill=RED, width=7)
        draw.line([mapper(point) for point in companion], fill=CYAN, width=7)
        draw.line((mapper(_midpoint(source)), mapper(_midpoint(companion))), fill=YELLOW, width=4)
    side = (2960, 145, 4150, 1460)
    draw.rectangle(side, fill=BACKGROUND, outline=GRID, width=2)
    summary = case["aggregate"]["summary"]
    lines = [
        ("SEMANTIC", "thin_wall.normal_thickness", CYAN),
        ("DECISION", case["aggregate"]["verification_status"], GREEN if case["aggregate"]["resolved"] else ORANGE),
        ("MEDIAN", f"{summary['median']:.4f} mm", YELLOW),
        ("Q10-Q90", f"{summary['q10']:.4f} - {summary['q90']:.4f} mm", WHITE),
        ("STATIONS", f"{case['aggregate']['resolved_station_count']} / {case['aggregate']['station_count']}", WHITE),
        ("PATTERN", str(case.get("pattern_count") or "not required"), WHITE),
        ("HISTORICAL", case.get("historical_label") or "not supplied to detector", MUTED),
    ]
    y = 190
    for label, value, colour in lines:
        draw.text((3000, y), label, font=_font(17, True), fill=MUTED)
        draw.text((3000, y + 32), value, font=_font(25, True), fill=colour)
        y += 135
    draw.text((70, 1480), "red = detected boundary | cyan = companion boundary | yellow = measured local gap", font=_font(18, True), fill=WHITE)
    bottom = (50, 1535, 4150, 2350)
    draw.rectangle(bottom, fill=BACKGROUND, outline=GRID, width=2)
    if drawing_path and drawing_path.exists():
        _fit(canvas, drawing_path, (70, 1555, 4130, 2330))
        draw.text((85, 1570), "ORIGINAL ENGINEER DRAWING (audit reference only)", font=_font(18, True), fill=CYAN)
    else:
        draw.text((100, 1640), "No historical drawing image used for this case.", font=_font(27), fill=MUTED)
    canvas.save(output)


def _render_planar_control(case: dict[str, Any], output: Path) -> None:
    canvas = Image.new("RGB", (1800, 1000), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((45, 30), f"Experiment 17 control | {case['name']}", font=_font(36, True), fill=WHITE)
    draw.text((45, 92), "Generic parallel-planar-face path", font=_font(20), fill=MUTED)
    result = case["planar_result"]
    colour = GREEN if result["resolved"] else ORANGE
    draw.rounded_rectangle((45, 165, 1755, 930), radius=12, fill=BACKGROUND, outline=colour, width=3)
    rows = [
        ("decision", result["reason"]),
        ("expected detection", str(case["expected_detection"])),
        ("actual detection", str(result["resolved"])),
        ("planar faces", str(result["planar_face_count"])),
        ("qualified pairs", str(result["candidate_count"])),
    ]
    if result.get("candidate"):
        rows += [
            ("semantic", result["candidate"]["semantic"]),
            ("thickness", f"{result['candidate']['thickness']:.4f} mm"),
            ("normal alignment", f"{result['candidate']['normal_alignment']:.5f}"),
        ]
    y = 215
    for key, value in rows:
        draw.text((90, y), key.upper(), font=_font(18, True), fill=MUTED)
        draw.text((500, y), value, font=_font(25, True), fill=WHITE if key != "actual detection" else colour)
        y += 82
    canvas.save(output)


def _scan_blade(shape, fractions: Sequence[float]) -> list[dict[str, Any]]:
    stations = []
    for fraction in fractions:
        try:
            view = section_shape_view(shape, fraction)
            detection = detect_adaptive_curved_radial_partitions(view)
            thickness = extract_section_boundary_thickness(view, detection)
            stations.append({"fraction": fraction, "view": view, "detection": detection, "thickness": thickness})
        except Exception as exc:
            stations.append({"fraction": fraction, "error": str(exc), "thickness": {"resolved": False, "reason": "SECTION_ERROR"}})
    return stations


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    exp15_config = json.loads(_resolve(config["experiment_15_config"]).read_text(encoding="utf-8"))
    exp15_report = json.loads(_resolve(config["experiment_15_report"]).read_text(encoding="utf-8"))
    exp15_dir = _resolve(config["experiment_15_directory"])
    output_dir.mkdir(parents=True, exist_ok=True)
    config_by_name = {item["name"]: item for item in exp15_config["cases"]}
    report_by_name = {item["name"]: item for item in exp15_report["cases"]}
    fractions = [float(value) for value in config.get("scan_fractions") or (0.1, 0.3, 0.5, 0.6)]
    cases = []
    for index, spec in enumerate(config["section_cases"], start=2):
        started = time.perf_counter()
        source_spec = config_by_name[spec["source_case"]]
        source_report = report_by_name[spec["source_case"]]
        shape = load_component_shape(source_spec["step_path"], source_report["selected_component"]["label_entry"])
        if shape is None:
            raise RuntimeError(f"Cannot load selected component for {spec['name']}")
        stations = _scan_blade(shape, fractions)
        aggregate = aggregate_station_thickness([item["thickness"] for item in stations])
        resolved_stations = [item for item in stations if item["thickness"].get("resolved")]
        review_station = min(resolved_stations, key=lambda item: abs(float(item["fraction"]) - 0.5)) if resolved_stations else stations[0]
        case = {
            "name": spec["name"],
            "source_case": spec["source_case"],
            "component_label": source_report["selected_component"]["label_entry"],
            "component_name": source_report["selected_component"]["name"],
            "expected_nominal_mm": spec.get("expected_nominal_mm"),
            "expected_tolerance_mm": spec.get("expected_tolerance_mm"),
            "historical_label": spec.get("historical_label"),
            "pattern_count": int((review_station.get("detection") or {}).get("pattern_count") or 0),
            "aggregate": aggregate,
            "stations": [{
                "fraction": item["fraction"],
                "resolved": item["thickness"].get("resolved", False),
                "reason": item["thickness"].get("reason"),
                "pattern_count": int((item.get("detection") or {}).get("pattern_count") or 0),
                "summary": item["thickness"].get("summary"),
                "error": item.get("error"),
            } for item in stations],
            "review_station": review_station,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        case_dir = output_dir / f"{index:02d}_{spec['name']}_review"
        case_dir.mkdir(parents=True, exist_ok=True)
        drawing_matches = list(exp15_dir.glob(f"*_{spec['source_case']}_review/02_original_engineer_drawing.png"))
        _render_blade_case(case, drawing_matches[0] if drawing_matches else None, case_dir / "01_thickness_review.png")
        public_case = {key: value for key, value in case.items() if key != "review_station"}
        (case_dir / "thickness_report.json").write_text(json.dumps(public_case, ensure_ascii=False, indent=2), encoding="utf-8")
        cases.append(public_case)

    controls = []
    control_specs = [
        ("synthetic_thin_plate", BRepPrimAPI_MakeBox(40.0, 25.0, 0.8).Shape(), True, 0.8),
        ("synthetic_solid_cylinder", BRepPrimAPI_MakeCylinder(8.0, 30.0).Shape(), False, None),
    ]
    for spec in config.get("step_controls") or []:
        control_specs.append((spec["name"], load_step(str(_resolve(spec["step_path"]))), bool(spec["expected_detection"]), spec.get("expected_nominal_mm")))
    start_index = 2 + len(cases)
    for offset, (name, shape, expected, nominal) in enumerate(control_specs):
        result = extract_parallel_planar_thickness(shape)
        case = {
            "name": name, "expected_detection": expected, "expected_nominal_mm": nominal,
            "planar_result": result,
            "decision_correct": bool(result["resolved"]) == expected,
        }
        case_dir = output_dir / f"{start_index + offset:02d}_{name}_review"
        case_dir.mkdir(parents=True, exist_ok=True)
        _render_planar_control(case, case_dir / "01_planar_pair_review.png")
        (case_dir / "thickness_report.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
        controls.append(case)

    fq6h = next((item for item in cases if item["name"] == "fq6h_blade_assy"), None)
    fq6h_error = None
    if fq6h and fq6h.get("expected_nominal_mm") is not None and fq6h["aggregate"]["resolved"]:
        fq6h_error = abs(float(fq6h["aggregate"]["summary"]["median"]) - float(fq6h["expected_nominal_mm"]))
    metrics = {
        "section_case_count": len(cases),
        "section_resolved_count": sum(bool(item["aggregate"]["resolved"]) for item in cases),
        "section_auto_verified_count": sum(item["aggregate"]["verification_status"] == "AUTO_VERIFIED" for item in cases),
        "section_review_candidate_count": sum(item["aggregate"]["verification_status"] == "REVIEW_CANDIDATE" for item in cases),
        "control_count": len(controls),
        "control_correct_count": sum(bool(item["decision_correct"]) for item in controls),
        "fq6h_expected_nominal_mm": fq6h.get("expected_nominal_mm") if fq6h else None,
        "fq6h_detected_nominal_mm": round(float(fq6h["aggregate"]["summary"]["median"]), 6) if fq6h and fq6h["aggregate"]["resolved"] else None,
        "fq6h_absolute_error_mm": round(fq6h_error, 6) if fq6h_error is not None else None,
    }
    summary = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "experimental_main_updated": False,
        "model_names_used_by_detector": False,
        "historical_values_used_by_detector": False,
        "semantic": "thin_wall.normal_thickness",
        "metrics": metrics,
        "section_cases": cases,
        "controls": controls,
    }
    (output_dir / "01_experiment_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.output_dir)
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
