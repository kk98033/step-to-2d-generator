"""Render experiment 09: automatic dimension layout from 08.1 intents."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from dimension_layout import count_lane_collisions, plan_dimension_layout  # noqa: E402
from run_classification_tree_audit import (  # noqa: E402
    BOX, CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, RED, WHITE, YELLOW,
    _edge_points, _font, _mapper,
)
from run_view_plan_experiment import _standard_view_data  # noqa: E402


METHOD = "AUTOMATIC_DIMENSION_LAYOUT_AUDIT_V1"
MODEL = "#d4d4d4"
HIDDEN = "#525252"
DIMENSION = "#facc15"
EXTENSION = "#a3a3a3"
REVIEW = "#f97316"


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _combined_bbox(view_bbox: Sequence[float], placements: Sequence[dict[str, Any]]) -> list[float]:
    x0, y0, x1, y1 = (float(value) for value in view_bbox)
    xs, ys = [x0, x1], [y0, y1]
    for item in placements:
        for point in item["dimension_points"]:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
        xs.append(float(item["text_position"][0]))
        ys.append(float(item["text_position"][1]))
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    return [min(xs) - width * 0.04, min(ys) - height * 0.04, max(xs) + width * 0.04, max(ys) + height * 0.04]


def _arrow(draw: ImageDraw.ImageDraw, tip: Sequence[float], toward: Sequence[float], colour: str) -> None:
    dx, dy = float(toward[0]) - float(tip[0]), float(toward[1]) - float(tip[1])
    length = max(math.hypot(dx, dy), 1e-9)
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    size = 13.0
    base_x, base_y = float(tip[0]) + ux * size, float(tip[1]) + uy * size
    draw.polygon([
        (float(tip[0]), float(tip[1])),
        (base_x + px * size * 0.38, base_y + py * size * 0.38),
        (base_x - px * size * 0.38, base_y - py * size * 0.38),
    ], fill=colour)


def _draw_projection(draw: ImageDraw.ImageDraw, view: dict[str, Any], mapper) -> None:
    for edge in view.get("hidden") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill=HIDDEN, width=1)
    for edge in view.get("visible") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill=MODEL, width=3)


def _draw_placement(draw: ImageDraw.ImageDraw, mapper, item: dict[str, Any], ordinal: int) -> None:
    source = [mapper(point) for point in item["source_anchors"]]
    dimension = [mapper(point) for point in item["dimension_points"]]
    text_point = mapper(item["text_position"])
    colour = DIMENSION if item["verification_status"] == "AUTO_VERIFIED" else REVIEW
    for p, q in zip(source, dimension):
        draw.line((p, q), fill="#171717", width=7)
        draw.line((p, q), fill=EXTENSION, width=2)
        draw.ellipse((p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5), fill=CYAN, outline="#111111", width=2)
    draw.line((dimension[0], dimension[1]), fill="#111111", width=9)
    draw.line((dimension[0], dimension[1]), fill=colour, width=4)
    _arrow(draw, dimension[0], dimension[1], colour)
    _arrow(draw, dimension[1], dimension[0], colour)
    label = f"{ordinal:02d}  {item['label']}"
    box = draw.textbbox(text_point, label, font=_font(17, True), anchor="mm")
    draw.rounded_rectangle((box[0] - 7, box[1] - 4, box[2] + 7, box[3] + 4), radius=4, fill="#111111")
    draw.text(text_point, label, font=_font(17, True), fill=colour, anchor="mm")


def render_layout_page(
    path: Path,
    case_name: str,
    view_name: str,
    view: dict[str, Any],
    plan: dict[str, Any],
    page: int,
    page_count: int,
) -> None:
    placements = plan["placements"]
    canvas = Image.new("RGB", (4700, 2700), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 30), f"09 Dimension Layout | {case_name} | {view_name.upper()}", font=_font(38, True), fill=WHITE)
    draw.text((55, 86), f"Page {page}/{page_count} | cyan=unchanged topology anchor | yellow=verified | orange=review", font=_font(19), fill=MUTED)
    drawing_box = (55, 145, 3470, 2580)
    draw.rectangle(drawing_box, fill="#0b0b0b", outline=GRID, width=2)
    mapper = _mapper(_combined_bbox(view["bbox"], placements), (90, 180, 3435, 2545))
    _draw_projection(draw, view, mapper)
    for ordinal, item in enumerate(placements, start=1):
        _draw_placement(draw, mapper, item, ordinal)

    left = 3525
    for index, item in enumerate(placements):
        y = 150 + index * 190
        colour = DIMENSION if item["verification_status"] == "AUTO_VERIFIED" else REVIEW
        draw.rounded_rectangle((left, y, 4645, y + 170), radius=10, fill=BOX, outline=colour, width=3)
        draw.text((left + 14, y + 10), f"{index + 1:02d}  {item['measurement_role']}", font=_font(15, True), fill=colour)
        draw.text((left + 14, y + 45), item["label"], font=_font(20, True), fill=WHITE)
        draw.text((left + 14, y + 82), f"{item['side']} lane {item['lane'] + 1} | {item['renderer']}", font=_font(14), fill=MUTED)
        draw.text((left + 14, y + 114), item["semantic"], font=_font(13), fill=MUTED)
        draw.text((left + 14, y + 141), item["verification_status"], font=_font(13, True), fill=colour)
    canvas.save(path)


def render_overview(path: Path, cases: Sequence[dict[str, Any]]) -> None:
    canvas = Image.new("RGB", (4300, 540 + len(cases) * 340), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((50, 30), "09 Automatic Dimension Layout Audit", font=_font(40, True), fill=WHITE)
    draw.text((50, 91), "08.1 validated anchors -> semantic side policy -> collision-free lanes -> explicit render geometry", font=_font(19), fill=MUTED)
    headers = ((60, "MODEL"), (850, "INTENTS / PLACED"), (1650, "COLLISIONS"), (2300, "MAX LANES"), (3050, "VIEWS"))
    for x, label in headers:
        draw.text((x, 175), label, font=_font(18, True), fill=MUTED)
    for index, case in enumerate(cases):
        y = 230 + index * 340
        draw.rounded_rectangle((40, y, 4250, y + 255), radius=14, fill=BOX, outline=GRID, width=3)
        draw.text((60, y + 30), case["name"], font=_font(25, True), fill=CYAN)
        draw.text((850, y + 30), f"{case['input_intent_count']} / {case['placed_count']}", font=_font(30, True), fill=WHITE)
        draw.text((1650, y + 30), str(case["collision_count"]), font=_font(30, True), fill=GREEN if not case["collision_count"] else RED)
        draw.text((2300, y + 30), str(case["max_lane_count"]), font=_font(30, True), fill=WHITE)
        draw.text((3050, y + 30), ", ".join(sorted(case["views"])), font=_font(20, True), fill=WHITE)
        draw.text((60, y + 104), Path(case["step_path"]).name, font=_font(15), fill=MUTED)
        draw.text((60, y + 150), f"skipped={case['skipped_count']} | production_updated=False", font=_font(15), fill=MUTED)
    canvas.save(path)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    upstream_path = _resolve_path(str(config["upstream_report"]))
    upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    case_reports: list[dict[str, Any]] = []
    page_size = int(config.get("page_size") or 10)

    for case_index, source_case in enumerate(upstream.get("cases") or [], start=3):
        name = str(source_case["name"])
        shape = load_step(str(_resolve_path(str(source_case["step_path"]))))
        views = _standard_view_data(ViewProjector(), shape)
        by_view: dict[str, list[dict[str, Any]]] = {}
        for intent in source_case.get("intents") or []:
            by_view.setdefault(str(intent["projection"]["preferred_view"]), []).append(intent)

        case_dir = output_dir / f"{case_index:02d}_{name}_dimension_layout_review"
        case_dir.mkdir(parents=True, exist_ok=True)
        public_plans: dict[str, Any] = {}
        review_images: list[str] = []
        for view_name, intents in sorted(by_view.items()):
            view = views[view_name]
            full_plan = plan_dimension_layout(intents, view["bbox"], include_review=True)
            public_plans[view_name] = full_plan
            full_path = case_dir / f"{len(review_images) + 1:02d}_{view_name}_full_layout.png"
            render_layout_page(full_path, name, view_name, view, full_plan, 1, 1)
            review_images.append(full_path.relative_to(output_dir).as_posix())

            page_count = max(1, math.ceil(len(intents) / page_size))
            for page_index in range(page_count):
                page_intents = intents[page_index * page_size:(page_index + 1) * page_size]
                page_plan = plan_dimension_layout(page_intents, view["bbox"], include_review=True)
                page_path = case_dir / f"{len(review_images) + 1:02d}_{view_name}_page_{page_index + 1:02d}.png"
                render_layout_page(page_path, name, view_name, view, page_plan, page_index + 1, page_count)
                review_images.append(page_path.relative_to(output_dir).as_posix())

        all_plans = list(public_plans.values())
        case_report = {
            "name": name,
            "step_path": str(source_case["step_path"]),
            "upstream_method": str(upstream.get("method")),
            "input_intent_count": len(source_case.get("intents") or []),
            "placed_count": sum(len(plan["placements"]) for plan in all_plans),
            "skipped_count": sum(len(plan["skipped"]) for plan in all_plans),
            "collision_count": sum(count_lane_collisions(plan) for plan in all_plans),
            "max_lane_count": max((max(plan["lane_counts"].values()) for plan in all_plans), default=0),
            "views": public_plans,
            "review_images": review_images,
        }
        case_reports.append(case_report)
        (case_dir / "layout_report.json").write_text(json.dumps(case_report, ensure_ascii=False, indent=2), encoding="utf-8")

    render_overview(output_dir / "02_dimension_layout_overview.png", case_reports)
    metrics = {
        "case_count": len(case_reports),
        "input_intent_count": sum(case["input_intent_count"] for case in case_reports),
        "placed_count": sum(case["placed_count"] for case in case_reports),
        "skipped_count": sum(case["skipped_count"] for case in case_reports),
        "collision_count": sum(case["collision_count"] for case in case_reports),
        "placement_rate": (
            sum(case["placed_count"] for case in case_reports)
            / max(sum(case["input_intent_count"] for case in case_reports), 1)
        ),
    }
    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "upstream_report": str(upstream_path),
        "cases": case_reports,
        "metrics": metrics,
    }
    (output_dir / "06_dimension_layout_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.output_dir)
    print(json.dumps(report["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
