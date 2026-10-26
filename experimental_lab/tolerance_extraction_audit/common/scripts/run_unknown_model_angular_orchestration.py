"""Run experiment 12: unknown-model angular trigger, selection and rendering."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import ezdxf
from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from angular_dimension_renderer import build_angular_render_tasks  # noqa: E402
from run_classification_tree_audit import (  # noqa: E402
    BOX, CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, RED, WHITE, YELLOW,
    _edge_points, _font, _mapper,
)
from unknown_model_angular_orchestrator import orchestrate_radial_views  # noqa: E402


METHOD = "UNKNOWN_MODEL_ANGULAR_ORCHESTRATION_V1"
STANDARD_VIEWS = ("front", "top", "right", "left", "back")


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower() or "case"


def _ray(center: Sequence[float], radius: float, angle: float) -> tuple[float, float]:
    radians = math.radians(angle)
    return float(center[0]) + radius * math.cos(radians), float(center[1]) + radius * math.sin(radians)


def _expanded_bbox(bbox: Sequence[float], fraction: float = 0.40) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = [float(value) for value in bbox]
    pad = max(x1 - x0, y1 - y0) * fraction
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad


def _format_angle(value: float) -> str:
    return f"{float(value):.2f}°"


def _render_case_png(
    path: Path,
    case: dict[str, Any],
    view: dict[str, Any],
    result: dict[str, Any],
    tasks: Sequence[dict[str, Any]],
    experiment_label: str,
) -> None:
    canvas = Image.new("RGB", (4400, 2550), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 28), f"{experiment_label} | {case['name']}", font=_font(40, True), fill=WHITE)
    draw.text((55, 88), "Unknown-model geometry trigger; expected labels are audit-only and never enter the detector.", font=_font(19), fill=MUTED)
    drawing_box = (55, 165, 3060, 2390)
    mapped_view = dict(view)
    mapped_view["bbox"] = _expanded_bbox(view["bbox"])
    draw.rectangle(drawing_box, fill="#0b0b0b", outline=GRID, width=2)
    mapper = _mapper(mapped_view["bbox"], (drawing_box[0] + 25, drawing_box[1] + 25, drawing_box[2] - 25, drawing_box[3] - 25))
    for layer, colour, width in (("hidden", "#3f3f46", 1), ("visible", "#d4d4d8", 2)):
        for edge in view.get(layer) or []:
            points = _edge_points(edge)
            if len(points) >= 2:
                draw.line([mapper(point) for point in points], fill=colour, width=width)

    detection = result["best_detection"]
    for reference in detection.get("selected_boundary_edge_refs") or []:
        if ":" not in reference:
            continue
        collection_name, index_text = reference.split(":", 1)
        collection = view.get(collection_name) or []
        edge_index = int(index_text)
        if 0 <= edge_index < len(collection):
            points = _edge_points(collection[edge_index])
            if len(points) >= 2:
                draw.line([mapper(point) for point in points], fill=CYAN, width=5)
    if detection.get("center"):
        center = detection["center"]
        radius = max(view["bbox"][2] - view["bbox"][0], view["bbox"][3] - view["bbox"][1]) / 2.0
        cp = mapper(center)
        draw.ellipse((cp[0] - 10, cp[1] - 10, cp[0] + 10, cp[1] + 10), fill=RED, outline=WHITE, width=2)
        draw.line((mapper((center[0] - radius * 1.3, center[1])), mapper((center[0] + radius * 1.3, center[1]))), fill="#2563eb", width=3)
        draw.line((mapper((center[0], center[1] - radius * 1.3)), mapper((center[0], center[1] + radius * 1.3))), fill="#2563eb", width=3)
        extension_start = float(detection.get("reference_radius") or radius * 0.2) * 0.75
        for task in tasks:
            colour = GREEN if result["decision"] == "AUTO_RENDER" else ORANGE
            extension_end = float(task["radius"]) + radius * 0.08
            for angle in (task["start_angle"], task["end_angle"]):
                draw.line((mapper(_ray(center, extension_start, angle)), mapper(_ray(center, extension_end, angle))), fill=colour, width=5)
            steps = max(10, int(float(task["angle"]) / 2.0) + 1)
            arc = [mapper(_ray(center, task["radius"], task["start_angle"] + task["angle"] * index / steps)) for index in range(steps + 1)]
            draw.line(arc, fill=colour, width=7)
            mid = (task["start_angle"] + task["angle"] / 2.0) % 360.0
            label_at = mapper(_ray(center, task["radius"] + radius * 0.06, mid))
            draw.text(label_at, _format_angle(task["angle"]), font=_font(25, True), fill=colour, anchor="mm")

    x = 3140
    decision_colour = GREEN if result["decision"] == "AUTO_RENDER" else ORANGE if result["decision"] == "REVIEW_RENDER" else RED
    draw.rounded_rectangle((x, 165, 4335, 960), radius=14, fill=BOX, outline=decision_colour, width=4)
    lines = [
        f"decision: {result['decision']}",
        f"verification: {result['verification_status']}",
        f"selected view: {result['best_view']}",
        f"detector: {result['selected_detector']}",
        f"trigger score: {result['best_assessment']['score']:.3f}",
        f"reason: {result['best_assessment']['reason']}",
        f"boundaries: {len(detection.get('boundary_angles') or [])}",
        f"render tasks: {len(tasks)}",
        f"expected trigger: {case['expected_trigger']}",
    ]
    for index, line in enumerate(lines):
        draw.text((x + 22, 195 + index * 88), line, font=_font(17, index < 2), fill=decision_colour if index < 2 else WHITE)

    draw.text((x, 1030), "VIEW CANDIDATES", font=_font(19, True), fill=MUTED)
    y = 1090
    for candidate in result["view_candidates"]:
        detector = "curve" if candidate["detector"].startswith("CURVED") else "line"
        draw.text((x + 15, y), f"{candidate['view']:<6} {detector:<5} {candidate['score']:.3f} | bnd {candidate['boundary_count']:>3} | {candidate['reason']}", font=_font(13), fill=CYAN if candidate["view"] == result["best_view"] and candidate["detector"] == result["selected_detector"] else WHITE)
        y += 52
    draw.text((x, y + 35), "AUTOMATIC ANGLE INTENTS", font=_font(19, True), fill=MUTED)
    y += 95
    for task in tasks:
        draw.rounded_rectangle((x, y, 4335, y + 150), radius=10, fill=BOX, outline=ORANGE, width=2)
        draw.text((x + 18, y + 15), f"{task['id']} | {_format_angle(task['angle'])}", font=_font(18, True), fill=ORANGE)
        draw.text((x + 18, y + 62), task["semantic"], font=_font(14), fill=WHITE)
        draw.text((x + 18, y + 103), str(task["source_relation"].get("selection_reason") or "MEASURED_RELATION"), font=_font(13), fill=MUTED)
        y += 170
    canvas.save(path)


def _render_dxf(path: Path, view: dict[str, Any], detection: dict[str, Any], tasks: Sequence[dict[str, Any]]) -> None:
    document = ezdxf.new("R2010", setup=True)
    document.header["$INSUNITS"] = 4
    for name, colour in (("VISIBLE", 8), ("HIDDEN", 8), ("CENTER", 5), ("ANGULAR_DIM", 3)):
        if name not in document.layers:
            document.layers.add(name, color=colour)
    modelspace = document.modelspace()
    for layer_name, edges in (("VISIBLE", view.get("visible") or []), ("HIDDEN", view.get("hidden") or [])):
        for edge in edges:
            points = _edge_points(edge)
            if len(points) == 2 and edge.get("type") == "line":
                modelspace.add_line(points[0], points[1], dxfattribs={"layer": layer_name})
            elif len(points) >= 2:
                modelspace.add_lwpolyline(points, dxfattribs={"layer": layer_name})
    center = detection["center"]
    reference = float(detection.get("reference_radius") or 1.0) * 0.75
    for task in tasks:
        p1, p2 = _ray(center, reference, task["start_angle"]), _ray(center, reference, task["end_angle"])
        mid = (task["start_angle"] + task["angle"] / 2.0) % 360.0
        override = modelspace.add_angular_dim_3p(
            base=_ray(center, task["radius"], mid), center=center, p1=p1, p2=p2,
            text=_format_angle(task["angle"]),
            override={"dimclrd": 3, "dimclre": 3, "dimclrt": 3, "dimasz": 2.3, "dimtxt": 3.2},
            dxfattribs={"layer": "ANGULAR_DIM"},
        )
        override.render()
    document.saveas(path)


def _render_overview(path: Path, rows: Sequence[dict[str, Any]], metrics: dict[str, Any], experiment_label: str) -> None:
    height = 520 + len(rows) * 245
    canvas = Image.new("RGB", (3900, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 28), f"{experiment_label} | Unknown-model angular orchestration", font=_font(40, True), fill=WHITE)
    draw.text((55, 92), "Geometry trigger → view selection → automatic relation selection → native DXF angular dimensions", font=_font(19), fill=MUTED)
    draw.text((55, 155), f"TP {metrics['true_positive']} | TN {metrics['true_negative']} | FP {metrics['false_positive']} | FN {metrics['false_negative']} | recall {metrics['recall']:.3f} | precision {metrics['precision']:.3f}", font=_font(24, True), fill=CYAN)
    y = 245
    for row in rows:
        correct = bool(row["triggered"]) == bool(row["expected_trigger"])
        colour = GREEN if correct else RED
        draw.rounded_rectangle((55, y, 3845, y + 210), radius=14, fill=BOX, outline=colour, width=4)
        draw.text((80, y + 20), row["name"], font=_font(22, True), fill=WHITE)
        draw.text((850, y + 20), row["decision"], font=_font(21, True), fill=colour)
        draw.text((1450, y + 20), f"view {row['best_view']} | score {row['score']:.3f}", font=_font(18), fill=CYAN)
        draw.text((2300, y + 20), f"expected {row['expected_trigger']} | {'PASS' if correct else 'MISMATCH'}", font=_font(18, True), fill=colour)
        draw.text((80, y + 82), f"angles: {', '.join(_format_angle(value) for value in row['angles']) or '<none>'}", font=_font(18), fill=ORANGE)
        draw.text((80, y + 137), f"time {row['elapsed_seconds']:.2f}s | {row['reason']}", font=_font(15), fill=MUTED)
        y += 245
    canvas.save(path)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    experiment_label = str(config.get("experiment_label") or "Experiment 12")
    output_dir.mkdir(parents=True, exist_ok=True)
    reports, rows = [], []
    for index, case in enumerate(config["cases"], start=1):
        started = time.perf_counter()
        step_path = _resolve(case["step_path"])
        shape = load_step(str(step_path))
        views = ViewProjector().project_all_views(shape, view_names=list(STANDARD_VIEWS))
        result = orchestrate_radial_views(views)
        selected_view = views[result["best_view"]]
        detection = result["best_detection"]
        radius = max(selected_view["bbox"][2] - selected_view["bbox"][0], selected_view["bbox"][3] - selected_view["bbox"][1]) / 2.0
        tasks = build_angular_render_tasks(
            detection.get("pairwise_center_relations") or [],
            result["angle_specifications"],
            base_radius=radius * 1.08,
            lane_spacing=radius * 0.13,
        ) if result["decision"] != "NO_RENDER" else []
        case_dir = output_dir / f"{index + 2:02d}_{_slug(case['name'])}_review"
        case_dir.mkdir(parents=True, exist_ok=True)
        _render_case_png(case_dir / "01_trigger_and_angle_review.png", case, selected_view, result, tasks, experiment_label)
        if tasks:
            _render_dxf(case_dir / "02_automatic_angular_dimensions.dxf", selected_view, detection, tasks)
        elapsed = time.perf_counter() - started
        row = {
            "name": case["name"],
            "step_path": str(step_path),
            "expected_trigger": bool(case["expected_trigger"]),
            "triggered": result["decision"] != "NO_RENDER",
            "decision": result["decision"],
            "best_view": result["best_view"],
            "selected_detector": result["selected_detector"],
            "score": result["best_assessment"]["score"],
            "reason": result["best_assessment"]["reason"],
            "angles": [float(task["angle"]) for task in tasks],
            "elapsed_seconds": elapsed,
        }
        rows.append(row)
        reports.append({**row, "orchestration": result, "render_tasks": tasks})
    tp = sum(row["triggered"] and row["expected_trigger"] for row in rows)
    tn = sum(not row["triggered"] and not row["expected_trigger"] for row in rows)
    fp = sum(row["triggered"] and not row["expected_trigger"] for row in rows)
    fn = sum(not row["triggered"] and row["expected_trigger"] for row in rows)
    metrics = {
        "case_count": len(rows), "true_positive": tp, "true_negative": tn,
        "false_positive": fp, "false_negative": fn,
        "precision": tp / max(tp + fp, 1), "recall": tp / max(tp + fn, 1),
        "accuracy": (tp + tn) / max(len(rows), 1),
        "mean_elapsed_seconds": sum(row["elapsed_seconds"] for row in rows) / max(len(rows), 1),
    }
    _render_overview(output_dir / "02_unknown_model_trigger_benchmark.png", rows, metrics, experiment_label)
    report = {
        "schema_version": 1, "method": METHOD, "production_updated": False,
        "expected_labels_used_by_detector": False, "metrics": metrics, "cases": reports,
    }
    (output_dir / "01_experiment_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
