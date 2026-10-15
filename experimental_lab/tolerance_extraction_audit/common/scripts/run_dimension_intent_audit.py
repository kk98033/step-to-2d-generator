"""Build and visually audit the experimental unified DimensionIntent set."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw
from OCC.Core.TopAbs import TopAbs_SOLID
from OCC.Core.TopExp import TopExp_Explorer


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from annular_composite_features import extract_annular_composites  # noqa: E402
from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from dimension_intents import build_dimension_intent_set  # noqa: E402
from run_classification_tree_audit import (  # noqa: E402
    BOX, CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, RED, WHITE, YELLOW,
    _edge_points, _font, _mapper,
)
from run_view_plan_experiment import _standard_view_data  # noqa: E402
from solid_shaft_features import extract_solid_shaft_features  # noqa: E402


METHOD = "DIMENSION_INTENT_AUDIT_V1"
ROLE_COLOURS = {
    "INNER_DIAMETER": ORANGE,
    "OUTER_DIAMETER": CYAN,
    "RADIAL_WALL_THICKNESS": YELLOW,
    "AXIAL_THICKNESS": GREEN,
    "AXIAL_SEGMENT_LENGTH": "#c084fc",
    "AXIAL_OVERALL_LENGTH": "#60a5fa",
}
STATUS_COLOURS = {
    "AUTO_VERIFIED": GREEN,
    "REVIEW_CANDIDATE": ORANGE,
    "REVIEW_CONFLICT": RED,
    "INVALID": RED,
}


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _solid_projection_bounds(shape: Any) -> dict[int, dict[str, list[float]]]:
    result: dict[int, dict[str, list[float]]] = {}
    solid_explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    solid_index = 0
    while solid_explorer.More():
        solid_index += 1
        solid = solid_explorer.Current()
        # Use the same HLR projection pipeline as the review image.  A vertex
        # envelope can miss the extrema of circular/curved edges because those
        # extrema need not be explicit BRep vertices.
        solid_views = _standard_view_data(ViewProjector(), solid)
        result[solid_index] = {
            view_name: [float(value) for value in view["bbox"]]
            for view_name, view in solid_views.items()
            if view.get("bbox") and len(view["bbox"]) == 4
        }
        solid_explorer.Next()
    return result


def _draw_projection(draw: ImageDraw.ImageDraw, view: dict[str, Any], box: Sequence[int]):
    draw.rectangle(box, fill="#0b0b0b", outline=GRID, width=2)
    mapper = _mapper(view["bbox"], (box[0] + 35, box[1] + 35, box[2] - 35, box[3] - 35))
    for edge in view.get("hidden") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#454545", width=1)
    for edge in view.get("visible") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#e5e5e5", width=3)
    return mapper


def _draw_intent(draw: ImageDraw.ImageDraw, mapper, intent: dict[str, Any], ordinal: int) -> None:
    anchors = intent["projection"]["anchors"]
    if len(anchors) != 2:
        return
    colour = ROLE_COLOURS.get(intent["measurement"]["role"], WHITE)
    p0, p1 = mapper(anchors[0]), mapper(anchors[1])
    draw.line((p0, p1), fill="#111111", width=13)
    draw.line((p0, p1), fill=colour, width=7)
    for point in (p0, p1):
        draw.ellipse((point[0] - 10, point[1] - 10, point[0] + 10, point[1] + 10), fill=colour, outline="#111111", width=3)
    mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
    draw.ellipse((mid[0] - 18, mid[1] - 18, mid[0] + 18, mid[1] + 18), fill="#111111", outline=colour, width=4)
    draw.text((mid[0] - 9, mid[1] - 13), str(ordinal), font=_font(18, True), fill=colour)


def render_intent_page(path: Path, case: dict[str, Any], view_name: str, intents: Sequence[dict[str, Any]], page: int, page_count: int) -> None:
    width, height = 4700, 2500
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 32), f"DimensionIntent Audit | {case['name']} | {view_name.upper()}", font=_font(38, True), fill=WHITE)
    draw.text((55, 88), f"Page {page}/{page_count} | original STEP projection + unified anchors + renderer contract", font=_font(19), fill=MUTED)
    mapper = _draw_projection(draw, case["_views"][view_name], (55, 155, 3150, 2375))
    for ordinal, intent in enumerate(intents, start=1):
        _draw_intent(draw, mapper, intent, ordinal)

    left = 3230
    for index, intent in enumerate(intents):
        y = 160 + index * 218
        role = intent["measurement"]["role"]
        colour = ROLE_COLOURS.get(role, WHITE)
        status = intent["verification"]["status"]
        draw.rounded_rectangle((left, y, 4640, y + 195), radius=11, fill=BOX, outline=colour, width=4)
        draw.text((left + 15, y + 12), f"{index + 1}. {role}", font=_font(16, True), fill=colour)
        prefix = intent["renderer_contract"]["prefix"]
        value = intent["measurement"]["nominal_value"]
        draw.text((left + 15, y + 48), f"{prefix}{value:.4f} mm | {status}", font=_font(19, True), fill=STATUS_COLOURS.get(status, WHITE))
        draw.text((left + 15, y + 86), intent["measurement"]["semantic"], font=_font(14), fill=WHITE)
        topology = ", ".join(intent["feature"]["topology_refs"])
        draw.text((left + 15, y + 119), f"topology: {topology}", font=_font(13), fill=MUTED)
        draw.text((left + 15, y + 150), intent["renderer_contract"]["renderer"], font=_font(13, True), fill=MUTED)
    canvas.save(path)


def render_overview(path: Path, cases: Sequence[dict[str, Any]]) -> None:
    canvas = Image.new("RGB", (4400, 680 + len(cases) * 410), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 35), "Unified DimensionIntent Audit V1", font=_font(40, True), fill=WHITE)
    draw.text((55, 98), "06 annular + 07 solid shaft -> topology identity -> validation -> deduplication -> renderer contract", font=_font(19), fill=MUTED)
    headers = ((65, "MODEL"), (720, "EXTRACTORS"), (1480, "RAW / UNIFIED"), (2250, "DEDUP / CONFLICT"), (3100, "AUTO / REVIEW / INVALID"))
    for x, label in headers:
        draw.text((x, 190), label, font=_font(19, True), fill=MUTED)
    for index, case in enumerate(cases):
        y = 265 + index * 410
        counts = case["status_counts"]
        draw.rounded_rectangle((45, y, 4345, y + 305), radius=15, fill=BOX, outline=GRID, width=3)
        draw.text((65, y + 42), case["name"], font=_font(24, True), fill=CYAN)
        draw.text((720, y + 42), " + ".join(case["enabled_extractors"]), font=_font(19, True), fill=WHITE)
        draw.text((1480, y + 42), f"{case['raw_intent_count']} / {case['unified_intent_count']}", font=_font(30, True), fill=WHITE)
        draw.text((2250, y + 42), f"{case['deduplicated_count']} / {len(case['conflict_groups'])}", font=_font(30, True), fill=YELLOW if case["deduplicated_count"] else WHITE)
        draw.text((3100, y + 42), f"{counts['AUTO_VERIFIED']} / {counts['REVIEW_CANDIDATE'] + counts['REVIEW_CONFLICT']} / {counts['INVALID']}", font=_font(30, True), fill=GREEN if not counts["INVALID"] else RED)
        role_counts = Counter(item["measurement"]["role"] for item in case["intents"])
        draw.text((65, y + 125), " | ".join(f"{key}={value}" for key, value in sorted(role_counts.items())), font=_font(15), fill=MUTED)
        draw.text((65, y + 180), Path(case["step_path"]).name, font=_font(15), fill=MUTED)
    canvas.save(path)


def build_case(case_config: dict[str, Any]) -> dict[str, Any]:
    step_path = _resolve_path(str(case_config["step_path"]))
    shape = load_step(str(step_path))
    extractors = [str(value) for value in case_config.get("extractors") or []]
    annular = extract_annular_composites(shape) if "annular" in extractors else None
    shaft = extract_solid_shaft_features(shape) if "solid_shaft" in extractors else None
    projection_bounds = _solid_projection_bounds(shape) if case_config.get("validate_projection_bounds") else None
    result = build_dimension_intent_set(
        annular_result=annular,
        solid_shaft_result=shaft,
        projection_bounds_by_solid=projection_bounds,
    )
    return {
        "name": str(case_config["name"]),
        "step_path": str(case_config["step_path"]),
        "enabled_extractors": extractors,
        **result,
        "_views": _standard_view_data(ViewProjector(), shape),
    }


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    for case_index, case_config in enumerate(config.get("cases") or [], start=3):
        case = build_case(case_config)
        case_dir = output_dir / f"{case_index:02d}_{case['name']}_dimension_intent_review"
        case_dir.mkdir(parents=True, exist_ok=True)
        review_images = []
        by_view: dict[str, list[dict[str, Any]]] = {}
        for intent in case["intents"]:
            by_view.setdefault(intent["projection"]["preferred_view"], []).append(intent)
        for view_name, view_intents in sorted(by_view.items()):
            page_size = 10
            page_count = max(1, math.ceil(len(view_intents) / page_size))
            for page_index in range(page_count):
                page_intents = view_intents[page_index * page_size:(page_index + 1) * page_size]
                image_path = case_dir / f"{len(review_images) + 1:02d}_{view_name}_page_{page_index + 1:02d}.png"
                render_intent_page(image_path, case, view_name, page_intents, page_index + 1, page_count)
                review_images.append(image_path.relative_to(output_dir).as_posix())
        case["review_directory"] = case_dir.relative_to(output_dir).as_posix()
        case["review_images"] = review_images
        case.pop("_views", None)
        cases.append(case)
    render_overview(output_dir / "02_dimension_intent_overview.png", cases)
    metrics = {
        "case_count": len(cases),
        "raw_intent_count": sum(case["raw_intent_count"] for case in cases),
        "unified_intent_count": sum(case["unified_intent_count"] for case in cases),
        "deduplicated_count": sum(case["deduplicated_count"] for case in cases),
        "conflict_group_count": sum(len(case["conflict_groups"]) for case in cases),
        "auto_verified_count": sum(case["status_counts"]["AUTO_VERIFIED"] for case in cases),
        "review_count": sum(case["status_counts"]["REVIEW_CANDIDATE"] + case["status_counts"]["REVIEW_CONFLICT"] for case in cases),
        "invalid_count": sum(case["status_counts"]["INVALID"] for case in cases),
    }
    report = {
        "schema_version": 1,
        "method": str(config.get("method") or METHOD),
        "production_updated": False,
        "cases": cases,
        "metrics": metrics,
    }
    (output_dir / "06_dimension_intent_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
