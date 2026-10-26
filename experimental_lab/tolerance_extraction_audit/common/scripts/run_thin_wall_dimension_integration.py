"""Run experiment 18: thin-wall DimensionIntent and drawing integration."""

from __future__ import annotations

import argparse
import json
import math
import sys
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
from auto_2d_drawing.tolerance.assembly_components import load_component_shape  # noqa: E402
from dimension_intents import build_dimension_intent_set  # noqa: E402
from run_classification_tree_audit import (  # noqa: E402
    CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, RED, WHITE, YELLOW, _edge_points, _font, _mapper,
)
from thin_wall_dimension_pipeline import extract_thin_wall_dimension  # noqa: E402
from thickness_dimension_renderer import build_thickness_render_task  # noqa: E402


METHOD = "THIN_WALL_DIMENSION_INTEGRATION_V1"
BACKGROUND = "#0b0b0b"
MODEL = "#d4d4d4"
HIDDEN = "#525252"


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _expanded_bbox(bbox: Sequence[float], fraction: float = 0.08) -> list[float]:
    x0, y0, x1, y1 = [float(value) for value in bbox]
    pad = max(x1 - x0, y1 - y0, 1e-9) * fraction
    return [x0 - pad, y0 - pad, x1 + pad, y1 + pad]


def _combined_bbox(view_bbox: Sequence[float], task: dict[str, Any]) -> list[float]:
    points = [list(view_bbox[:2]), list(view_bbox[2:])]
    points += task["source_anchors"] + task["dimension_points"] + [task["text_position"]]
    xs, ys = [float(point[0]) for point in points], [float(point[1]) for point in points]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    return [min(xs) - width * 0.05, min(ys) - height * 0.05, max(xs) + width * 0.05, max(ys) + height * 0.05]


def _detail_bbox(task: dict[str, Any], model_span: float) -> list[float]:
    points = task["source_anchors"] + task["dimension_points"] + [task["text_position"]]
    xs, ys = [float(point[0]) for point in points], [float(point[1]) for point in points]
    span = max(max(xs) - min(xs), max(ys) - min(ys), model_span * 0.035)
    centre = ((max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0)
    return [centre[0] - span * 1.7, centre[1] - span * 1.25, centre[0] + span * 1.7, centre[1] + span * 1.25]


def _arrow(draw: ImageDraw.ImageDraw, tip: Sequence[float], toward: Sequence[float], colour: str) -> None:
    dx, dy = float(toward[0]) - float(tip[0]), float(toward[1]) - float(tip[1])
    length = max(math.hypot(dx, dy), 1e-9)
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    size = 14.0
    base = (float(tip[0]) + ux * size, float(tip[1]) + uy * size)
    draw.polygon([
        (float(tip[0]), float(tip[1])),
        (base[0] + px * size * 0.38, base[1] + py * size * 0.38),
        (base[0] - px * size * 0.38, base[1] - py * size * 0.38),
    ], fill=colour)


def _draw_view(draw: ImageDraw.ImageDraw, view: dict[str, Any], mapper) -> None:
    for collection, colour, width in (("hidden", HIDDEN, 1), ("visible", MODEL, 2)):
        for edge in view.get(collection) or []:
            points = _edge_points(edge)
            if len(points) >= 2:
                draw.line([mapper(point) for point in points], fill=colour, width=width)


def _draw_task(draw: ImageDraw.ImageDraw, mapper, task: dict[str, Any], detail: bool = False) -> None:
    source = [mapper(point) for point in task["source_anchors"]]
    dimension = [mapper(point) for point in task["dimension_points"]]
    text = mapper(task["text_position"])
    colour = YELLOW if task["verification_status"] == "AUTO_VERIFIED" else ORANGE
    underlay = 8 if detail else 6
    for anchor, projected in zip(source, dimension):
        draw.line((anchor, projected), fill="#111111", width=underlay)
        draw.line((anchor, projected), fill=CYAN, width=3 if detail else 2)
        radius = 7 if detail else 5
        draw.ellipse((anchor[0] - radius, anchor[1] - radius, anchor[0] + radius, anchor[1] + radius), fill=RED, outline="#111111", width=2)
    draw.line((dimension[0], dimension[1]), fill="#111111", width=underlay + 3)
    draw.line((dimension[0], dimension[1]), fill=colour, width=4 if detail else 3)
    _arrow(draw, dimension[0], dimension[1], colour)
    _arrow(draw, dimension[1], dimension[0], colour)
    font = _font(24 if detail else 17, True)
    box = draw.textbbox(text, task["label"], font=font, anchor="mm")
    draw.rounded_rectangle((box[0] - 10, box[1] - 6, box[2] + 10, box[3] + 6), radius=5, fill="#111111")
    draw.text(text, task["label"], font=font, fill=colour, anchor="mm")


def _fit(canvas: Image.Image, path: Path, box: Sequence[int]) -> None:
    source = Image.open(path).convert("RGB")
    width, height = int(box[2] - box[0]), int(box[3] - box[1])
    scale = min(width / source.width, height / source.height)
    source = source.resize((max(1, int(source.width * scale)), max(1, int(source.height * scale))))
    canvas.paste(source, (int(box[0] + (width - source.width) / 2), int(box[1] + (height - source.height) / 2)))


def _render_review(case: dict[str, Any], original: Path | None, output: Path) -> None:
    canvas = Image.new("RGB", (4400, 2550), PANEL)
    draw = ImageDraw.Draw(canvas)
    intent, task, view = case["intent"], case["render_task"], case["view"]
    colour = GREEN if intent["verification"]["status"] == "AUTO_VERIFIED" else ORANGE
    draw.text((50, 28), f"Experiment 18 | {case['name']} | thin-wall dimension integration", font=_font(38, True), fill=WHITE)
    draw.text((50, 88), "geometry -> DimensionIntent -> local THICKNESS_RENDERER -> DXF", font=_font(19), fill=MUTED)
    full_box, detail_box = (45, 145, 2840, 1490), (2880, 145, 4355, 1490)
    draw.rectangle(full_box, fill=BACKGROUND, outline=GRID, width=2)
    draw.rectangle(detail_box, fill=BACKGROUND, outline=GRID, width=2)
    model_span = max(float(view["bbox"][2]) - float(view["bbox"][0]), float(view["bbox"][3]) - float(view["bbox"][1]), 1e-9)
    full_mapper = _mapper(_combined_bbox(view["bbox"], task), (70, 180, 2815, 1455))
    detail_mapper = _mapper(_detail_bbox(task, model_span), (2905, 205, 4330, 1455))
    _draw_view(draw, view, full_mapper)
    _draw_task(draw, full_mapper, task)
    _draw_view(draw, view, detail_mapper)
    _draw_task(draw, detail_mapper, task, detail=True)
    draw.text((2910, 165), "LOCAL FEATURE DETAIL", font=_font(17, True), fill=CYAN)
    status_y = 1515
    measurement = intent["measurement"]
    values = [
        f"status: {intent['verification']['status']}",
        f"semantic: {measurement['semantic']}",
        f"geometry nominal: {measurement['nominal_value']:.6f} mm",
        f"drawing label: {task['label']}",
        f"tolerance source: {task['tolerance_source']}",
        f"view: {intent['projection']['preferred_view']}",
    ]
    draw.rounded_rectangle((45, status_y, 4355, status_y + 180), radius=10, fill=BACKGROUND, outline=colour, width=3)
    for index, value in enumerate(values):
        x = 75 + (index % 3) * 1420
        y = status_y + 28 + (index // 3) * 72
        draw.text((x, y), value, font=_font(18, index in (0, 3)), fill=colour if index in (0, 3) else WHITE)
    lower = (45, 1725, 4355, 2495)
    draw.rectangle(lower, fill=BACKGROUND, outline=GRID, width=2)
    if original and original.exists():
        _fit(canvas, original, (65, 1745, 4335, 2475))
        draw.text((80, 1760), "ORIGINAL ENGINEER DRAWING", font=_font(17, True), fill=CYAN)
    else:
        draw.text((90, 1810), "No historical drawing supplied; geometry and intent evidence shown above.", font=_font(24), fill=MUTED)
    canvas.save(output)


def _dxf_text(intent: dict[str, Any]) -> str:
    measurement = intent["measurement"]
    if measurement.get("display_text"):
        return str(measurement["display_text"])
    value = float(measurement.get("display_value", measurement["nominal_value"]))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _write_dxf(view: dict[str, Any], intent: dict[str, Any], task: dict[str, Any], output: Path) -> None:
    document = ezdxf.new("R2010")
    for name, colour in (("VISIBLE", 7), ("HIDDEN", 8), ("DIM", 2), ("REVIEW", 30)):
        if name not in document.layers:
            document.layers.add(name, color=colour)
    msp = document.modelspace()
    for collection, layer in (("visible", "VISIBLE"), ("hidden", "HIDDEN")):
        for edge in view.get(collection) or []:
            points = _edge_points(edge)
            if len(points) >= 2:
                msp.add_lwpolyline(points, dxfattribs={"layer": layer})
    p0, p1 = task["source_anchors"]
    vx, vy = p1[0] - p0[0], p1[1] - p0[1]
    length = max(math.hypot(vx, vy), 1e-12)
    default_normal = (-vy / length, vx / length)
    sign = 1.0 if sum(default_normal[index] * task["offset_direction"][index] for index in range(2)) >= 0 else -1.0
    tolerance = intent["measurement"].get("tolerance") or {}
    override: dict[str, Any] = {"dimdec": 3, "dimtxt": 1.8, "dimasz": 1.2}
    if tolerance.get("mode") in {"SYMMETRIC", "LIMITS"}:
        override.update({
            "dimtol": 1,
            "dimtp": abs(float(tolerance.get("upper", 0.0))),
            "dimtm": abs(float(tolerance.get("lower", tolerance.get("upper", 0.0)))),
            "dimtfac": 0.75,
        })
    dimension = msp.add_aligned_dim(
        p1=p0,
        p2=p1,
        distance=sign * float(task["offset"]),
        text=_dxf_text(intent),
        override=override,
        dxfattribs={"layer": "DIM" if intent["verification"]["status"] == "AUTO_VERIFIED" else "REVIEW"},
    )
    dimension.render()
    document.saveas(output)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    exp15_config = json.loads(_resolve(config["experiment_15_config"]).read_text(encoding="utf-8"))
    exp15_report = json.loads(_resolve(config["experiment_15_report"]).read_text(encoding="utf-8"))
    exp15_dir = _resolve(config["experiment_15_directory"])
    config_by_name = {item["name"]: item for item in exp15_config["cases"]}
    report_by_name = {item["name"]: item for item in exp15_report["cases"]}
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for index, case_config in enumerate(config["cases"], start=2):
        if case_config.get("source_case"):
            source_config = config_by_name[case_config["source_case"]]
            source_report = report_by_name[case_config["source_case"]]
            shape = load_component_shape(source_config["step_path"], source_report["selected_component"]["label_entry"])
            original_matches = list(exp15_dir.glob(f"*_{case_config['source_case']}_review/02_original_engineer_drawing.png"))
            original = original_matches[0] if original_matches else None
        else:
            shape = load_step(str(_resolve(case_config["step_path"])))
            original = None
        result = extract_thin_wall_dimension(
            shape,
            case_config.get("drawing_specification"),
            config.get("scan_fractions") or (0.1, 0.3, 0.5, 0.6),
        )
        expected = bool(case_config["expected_detection"])
        if not result.get("resolved"):
            case = {
                "name": case_config["name"],
                "expected_detection": expected,
                "resolved": False,
                "decision_correct": not expected,
                "reason": result.get("reason"),
                "result": result,
            }
            case_dir = output_dir / f"{index:02d}_{case_config['name']}_review"
            case_dir.mkdir(parents=True, exist_ok=True)
            (case_dir / "integration_report.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
            reports.append(case)
            continue
        intent_set = build_dimension_intent_set(thin_wall_result=result)
        intent = intent_set["intents"][0]
        task = build_thickness_render_task(intent, result["view"]["bbox"])
        expected_status = case_config.get("expected_status")
        status_correct = expected_status is None or intent["verification"]["status"] == expected_status
        case = {
            "name": case_config["name"],
            "expected_detection": expected,
            "resolved": True,
            "decision_correct": expected and status_correct,
            "expected_status": expected_status,
            "status_correct": status_correct,
            "reason": result.get("reason"),
            "path": result.get("path"),
            "intent": intent,
            "intent_set_metrics": {
                "raw_intent_count": intent_set["raw_intent_count"],
                "unified_intent_count": intent_set["unified_intent_count"],
                "status_counts": intent_set["status_counts"],
            },
            "render_task": task,
            "view": result["view"],
        }
        case_dir = output_dir / f"{index:02d}_{case_config['name']}_review"
        case_dir.mkdir(parents=True, exist_ok=True)
        _render_review(case, original, case_dir / "01_dimension_integration_review.png")
        _write_dxf(result["view"], intent, task, case_dir / "02_automatic_thickness_dimension.dxf")
        public_case = {key: value for key, value in case.items() if key != "view"}
        (case_dir / "integration_report.json").write_text(json.dumps(public_case, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(public_case)
    metrics = {
        "case_count": len(reports),
        "decision_correct_count": sum(bool(item["decision_correct"]) for item in reports),
        "resolved_count": sum(bool(item["resolved"]) for item in reports),
        "auto_verified_count": sum((item.get("intent") or {}).get("verification", {}).get("status") == "AUTO_VERIFIED" for item in reports),
        "review_candidate_count": sum((item.get("intent") or {}).get("verification", {}).get("status") == "REVIEW_CANDIDATE" for item in reports),
        "dxf_count": sum(bool(item["resolved"]) for item in reports),
    }
    summary = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "experimental_main_updated": True,
        "metrics": metrics,
        "cases": reports,
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
