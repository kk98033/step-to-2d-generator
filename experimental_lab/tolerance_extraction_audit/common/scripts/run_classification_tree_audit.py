"""Audit the model-to-feature drawing classification tree with visual arrows.

The experiment calls the same canonical feature entry point used by the web
system, compares geometry-only and hint-assisted part-family classification,
then routes selected features to views and renderer contracts.  It does not
modify the production classifier or drawing engine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw, ImageFont
from OCC.Core.TopAbs import TopAbs_SOLID
from OCC.Core.TopExp import TopExp_Explorer


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.canonical_features import extract_canonical_features  # noqa: E402
from auto_2d_drawing.part_classifier import PartClassifier, normalize_part_semantic_type  # noqa: E402
from auto_2d_drawing.smart_annotation_engine import SmartAnnotationEngine  # noqa: E402
from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from run_view_plan_experiment import (  # noqa: E402
    _standard_view_data,
    build_view_candidates,
    solve_minimum_view_plan,
)


METHOD = "CLASSIFICATION_TREE_AUDIT_V1"
BACKGROUND = "#111111"
PANEL = "#171717"
BOX = "#262626"
BLUE = "#2563eb"
CYAN = "#22d3ee"
GREEN = "#34d399"
ORANGE = "#f97316"
YELLOW = "#fde047"
RED = "#f87171"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
GRID = "#404040"
FEATURE_COLOURS = (ORANGE, CYAN, GREEN, "#c084fc")


def _font(size: int, bold: bool = False):
    candidates = (
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _edge_points(edge: dict[str, Any]) -> list[tuple[float, float]]:
    if edge.get("type") == "line":
        return [tuple(edge["p1"]), tuple(edge["p2"])]
    return [tuple(point) for point in edge.get("points") or []]


def _mapper(bbox: Sequence[float], box: Sequence[int]):
    x0, y0, x1, y1 = (float(value) for value in bbox)
    left, top, right, bottom = box
    width, height = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    scale = min((right - left) / width, (bottom - top) / height)
    padx = ((right - left) - width * scale) / 2.0
    pady = ((bottom - top) - height * scale) / 2.0

    def map_point(point: Sequence[float]) -> tuple[float, float]:
        return (
            left + padx + (float(point[0]) - x0) * scale,
            bottom - pady - (float(point[1]) - y0) * scale,
        )

    return map_point


def count_solids(shape: Any) -> int:
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def structure_label(solid_count: int) -> str:
    return "SINGLE_SOLID_PART" if solid_count == 1 else "MULTI_BODY_OR_ASSEMBLY"


def renderer_for_rule(rule: dict[str, Any]) -> str:
    dim_type = str(rule.get("dim_type") or "").upper()
    category = str(rule.get("category") or "").lower()
    if dim_type == "HOLE_PATTERN" or "pattern" in category:
        return "PATTERN_DIMENSION_RENDERER"
    if dim_type == "DIAMETER":
        return "DIAMETER_RENDERER"
    if dim_type == "ANGULAR":
        return "ANGULAR_RENDERER"
    if dim_type == "LEADER" or category in {"fillet", "chamfer", "radius"}:
        return "LEADER_RADIUS_RENDERER"
    if category == "thickness":
        return "THICKNESS_RENDERER"
    if dim_type == "LINEAR":
        return "LINEAR_DIMENSION_RENDERER"
    return "GENERIC_RENDERER"


def classification_confidence(geometry_family: str, production_family: str) -> tuple[float, str]:
    if geometry_family == production_family:
        return 0.92, "GEOMETRY_AND_PRODUCTION_AGREE"
    if {geometry_family, production_family} == {"FAN_IMPELLER", "FAN"}:
        return 0.88, "NORMALIZED_FAMILY_ALIAS"
    return 0.62, "HINT_OR_POLICY_DEPENDENT"


def _arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], colour: str = BLUE, width: int = 5) -> None:
    draw.line((start, end), fill=colour, width=width)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = max((dx * dx + dy * dy) ** 0.5, 1.0)
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    size = 18
    tip = end
    left = (end[0] - ux * size + px * size * 0.55, end[1] - uy * size + py * size * 0.55)
    right = (end[0] - ux * size - px * size * 0.55, end[1] - uy * size - py * size * 0.55)
    draw.polygon((tip, left, right), fill=colour)


def _box(draw: ImageDraw.ImageDraw, rect: Sequence[int], title: str, lines: Sequence[str], accent: str) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=16, fill=BOX, outline=accent, width=4)
    draw.rectangle((left, top, right, top + 52), fill=accent)
    draw.text((left + 18, top + 11), title, font=_font(21, True), fill=BACKGROUND if accent in {CYAN, GREEN, YELLOW} else WHITE)
    y = top + 72
    for line in lines:
        draw.text((left + 18, y), str(line)[:49], font=_font(17), fill=WHITE)
        y += 31


def build_case(case: dict[str, Any]) -> dict[str, Any]:
    step_path = _resolve_path(str(case["step_path"]))
    shape = load_step(str(step_path))
    projector = ViewProjector()
    standard_views = _standard_view_data(projector, shape)

    geometry_features = extract_canonical_features(shape, part_hint=None)
    production_features = extract_canonical_features(shape, part_hint=str(case.get("part_hint") or ""))
    geometry_family = normalize_part_semantic_type(geometry_features.part_type)
    production_family = production_features.part_type
    confidence, confidence_reason = classification_confidence(geometry_family, production_family)

    rules = SmartAnnotationEngine().get_candidate_rules(
        shape,
        standard_views,
        part_hint=str(case.get("part_hint") or case["name"]),
    )
    rules_by_id = {str(rule.get("rule_id") or rule.get("id")): rule for rule in rules}
    selections = list(case.get("selections") or [])
    needs_section = any(bool(selection.get("requires_section")) for selection in selections)
    candidates = build_view_candidates(shape, standard_views, needs_section)
    plan = solve_minimum_view_plan(rules_by_id, selections, candidates)
    selected_view_by_id = {str(view["id"]): view for view in plan.get("selected_views") or []}

    feature_routes = []
    for selection in selections:
        feature_id = str(selection["feature_id"])
        rule = rules_by_id.get(feature_id)
        if rule is None:
            feature_routes.append({
                "feature_id": feature_id,
                "status": "MISSING_FEATURE",
                "semantic": "UNKNOWN",
                "view_id": None,
                "renderer": None,
            })
            continue
        assignment = plan.get("assignments", {}).get(feature_id) or {}
        selected_view = selected_view_by_id.get(str(assignment.get("view_id") or "")) or {}
        base_view = str(selected_view.get("base_view") or assignment.get("view_id") or "front")
        projection_payload = (rule.get("view_geometry") or {}).get(base_view) or {}
        projected_bbox = projection_payload.get("projected_feature_bbox") or []
        feature_routes.append({
            "feature_id": feature_id,
            "status": "ROUTED" if assignment else "UNRESOLVED_VIEW",
            "semantic": str(rule.get("category") or rule.get("dim_type") or "UNKNOWN").upper(),
            "dim_type": str(rule.get("dim_type") or "UNKNOWN").upper(),
            "nominal_value": rule.get("nominal_value"),
            "view_id": assignment.get("view_id"),
            "base_view": base_view,
            "view_score": assignment.get("score"),
            "renderer": renderer_for_rule(rule),
            "requires_section": bool(selection.get("requires_section")),
            "projected_bbox": [float(value) for value in projected_bbox] if len(projected_bbox) == 4 else None,
        })

    expected = str(case.get("expected_family") or "")
    solid_count = count_solids(shape)
    summary = geometry_features.feature_extractor.summary()
    bbox = summary.get("bounding_box") or {}
    return {
        "name": str(case["name"]),
        "step_path": str(step_path.resolve()),
        "part_hint": str(case.get("part_hint") or ""),
        "solid_count": solid_count,
        "structure": structure_label(solid_count),
        "geometry_family": geometry_family,
        "production_family": production_family,
        "expected_family": expected,
        "family_correct": not expected or production_family == expected,
        "classification_confidence": confidence,
        "classification_reason": confidence_reason,
        "canonical_feature_count": len(production_features.records),
        "candidate_rule_count": len(rules),
        "classification_evidence": {
            "bbox_mm": {
                "W": round(float(bbox.get("W") or 0.0), 3),
                "H": round(float(bbox.get("H") or 0.0), 3),
                "D": round(float(bbox.get("D") or 0.0), 3),
            },
            "main_axis": summary.get("main_axis"),
            "holes_count": int(summary.get("holes_count") or 0),
            "shafts_count": int(summary.get("shafts_count") or 0),
            "step_segments": int(summary.get("step_segments") or 0),
        },
        "view_plan_status": plan["status"],
        "selected_views": plan.get("selected_views") or [],
        "feature_routes": feature_routes,
        "_debug_views": {
            view_name: standard_views[view_name]
            for view_name in dict.fromkeys(route.get("base_view") for route in feature_routes)
            if view_name in standard_views
        },
    }


def _draw_projection_debug(draw: ImageDraw.ImageDraw, case: dict[str, Any]) -> None:
    panel = (70, 700, 2520, 2280)
    draw.rounded_rectangle(panel, radius=18, fill=BACKGROUND, outline=GRID, width=3)
    draw.text((95, 720), "ORIGINAL STEP PROJECTION + CLASSIFICATION EVIDENCE", font=_font(23, True), fill=WHITE)
    debug_views = case.get("_debug_views") or {}
    view_names = list(debug_views)[:2]
    if not view_names:
        draw.text((110, 810), "No projection available", font=_font(22), fill=RED)
        return

    row_top = 785
    row_height = 650 if len(view_names) > 1 else 1050
    for view_index, view_name in enumerate(view_names):
        top = row_top + view_index * (row_height + 45)
        bottom = top + row_height
        view_box = (105, top + 48, 1640, bottom - 25)
        label_left = 1700
        draw.text((110, top + 5), f"{view_name.upper()} PROJECTION", font=_font(19, True), fill=CYAN)
        draw.rectangle(view_box, fill="#0b0b0b", outline=GRID, width=2)
        view = debug_views[view_name]
        mapper = _mapper(view["bbox"], (view_box[0] + 35, view_box[1] + 35, view_box[2] - 35, view_box[3] - 35))
        for edge in view.get("hidden") or []:
            points = _edge_points(edge)
            if len(points) >= 2:
                draw.line([mapper(point) for point in points], fill="#525252", width=1)
        for edge in view.get("visible") or []:
            points = _edge_points(edge)
            if len(points) >= 2:
                draw.line([mapper(point) for point in points], fill="#d4d4d4", width=3)

        routes = [route for route in case["feature_routes"] if route.get("base_view") == view_name]
        label_step = max(125, (row_height - 100) // max(len(routes), 1))
        for route_index, route in enumerate(routes):
            colour = FEATURE_COLOURS[case["feature_routes"].index(route) % len(FEATURE_COLOURS)]
            bbox = route.get("projected_bbox")
            if bbox:
                p0, p1 = mapper((bbox[0], bbox[1])), mapper((bbox[2], bbox[3]))
                left = max(view_box[0] + 4, min(p0[0], p1[0]))
                right = min(view_box[2] - 4, max(p0[0], p1[0]))
                top_px = max(view_box[1] + 4, min(p0[1], p1[1]))
                bottom_px = min(view_box[3] - 4, max(p0[1], p1[1]))
                if right - left < 10:
                    left -= 5
                    right += 5
                if bottom_px - top_px < 10:
                    top_px -= 5
                    bottom_px += 5
                draw.rectangle((left, top_px, right, bottom_px), outline=colour, width=6)
                anchor = ((left + right) / 2.0, (top_px + bottom_px) / 2.0)
            else:
                anchor = (view_box[2] - 20, view_box[1] + 80 + route_index * 80)
            label_y = top + 65 + route_index * label_step
            elbow = (label_left - 45, label_y + 42)
            draw.line((anchor, elbow, (label_left, label_y + 42)), fill=colour, width=5)
            draw.ellipse((anchor[0] - 8, anchor[1] - 8, anchor[0] + 8, anchor[1] + 8), fill=colour)
            draw.text((label_left, label_y), route["feature_id"], font=_font(18, True), fill=colour)
            draw.text((label_left, label_y + 31), f"{route['semantic']} -> {route.get('view_id')}", font=_font(15), fill=WHITE)
            draw.text((label_left, label_y + 58), str(route.get("renderer") or "NONE"), font=_font(14), fill=MUTED)

    evidence = case["classification_evidence"]
    bbox = evidence["bbox_mm"]
    evidence_y = 1935 if len(view_names) == 1 else 2175
    draw.text((1700, evidence_y), "FAMILY CLASSIFICATION INPUT", font=_font(18, True), fill=YELLOW)
    draw.text((1700, evidence_y + 32), f"Bnd_Box: {bbox['W']:.2f} x {bbox['H']:.2f} x {bbox['D']:.2f} mm", font=_font(15), fill=WHITE)
    draw.text((1700, evidence_y + 59), f"axis={evidence['main_axis']} holes={evidence['holes_count']} shafts={evidence['shafts_count']} steps={evidence['step_segments']}", font=_font(15), fill=MUTED)


def render_case_tree(output: Path, case: dict[str, Any]) -> None:
    width, height = 5000, 2450
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 35), f"Classification Tree Audit | {case['name']}", font=_font(38, True), fill=WHITE)
    draw.text((55, 91), "Same canonical feature entry point as the main web system; arrows show actual routing.", font=_font(20), fill=MUTED)

    stage_x = [70, 900, 1750, 2760, 3860]
    stage_widths = [650, 650, 760, 760, 1000]
    stage_titles = ["1. MODEL", "2. STRUCTURE", "3. PART FAMILY", "4. FEATURE SEMANTIC", "5. VIEW + RENDERER"]
    for x, w, title in zip(stage_x, stage_widths, stage_titles):
        draw.text((x, 155), title, font=_font(22, True), fill=MUTED)

    model_rect = (stage_x[0], 270, stage_x[0] + stage_widths[0], 600)
    structure_rect = (stage_x[1], 270, stage_x[1] + stage_widths[1], 600)
    family_rect = (stage_x[2], 245, stage_x[2] + stage_widths[2], 625)
    _box(draw, model_rect, "STEP INPUT", [Path(case["step_path"]).name, "OpenCASCADE BRep"], BLUE)
    _box(draw, structure_rect, "MODEL STRUCTURE", [case["structure"], f"solid count: {case['solid_count']}"], CYAN)
    family_colour = GREEN if case["family_correct"] else RED
    _box(draw, family_rect, "PART FAMILY", [
        f"geometry: {case['geometry_family']}",
        f"production: {case['production_family']}",
        f"expected: {case['expected_family'] or 'not set'}",
        f"audit confidence: {case['classification_confidence']:.0%}",
        case["classification_reason"],
    ], family_colour)
    _arrow(draw, (model_rect[2], 435), (structure_rect[0], 435))
    _arrow(draw, (structure_rect[2], 435), (family_rect[0], 435))

    _draw_projection_debug(draw, case)

    routes = case["feature_routes"]
    feature_height = 290
    gap = 70
    total_height = len(routes) * feature_height + max(len(routes) - 1, 0) * gap
    first_top = max(715, (height - total_height) // 2 + 180)
    branch_x = family_rect[2] + 125
    family_mid = ((family_rect[0] + family_rect[2]) // 2, family_rect[3])
    draw.line((family_mid[0], family_mid[1], family_mid[0], first_top - 55), fill=BLUE, width=5)
    draw.line((family_mid[0], first_top - 55, branch_x, first_top - 55), fill=BLUE, width=5)
    if routes:
        last_mid_y = first_top + (len(routes) - 1) * (feature_height + gap) + feature_height // 2
        draw.line((branch_x, first_top - 55, branch_x, last_mid_y), fill=BLUE, width=5)

    for index, route in enumerate(routes):
        top = first_top + index * (feature_height + gap)
        feature_rect = (stage_x[3], top, stage_x[3] + stage_widths[3], top + feature_height)
        result_rect = (stage_x[4], top, stage_x[4] + stage_widths[4], top + feature_height)
        status_colour = ORANGE if route["status"] == "ROUTED" else RED
        _box(draw, feature_rect, route["feature_id"], [
            f"semantic: {route['semantic']}",
            f"dimension: {route.get('dim_type') or 'UNKNOWN'}",
            f"nominal: {route.get('nominal_value')}",
            f"section required: {route.get('requires_section', False)}",
        ], status_colour)
        _box(draw, result_rect, route["status"], [
            f"view: {route.get('view_id') or 'NONE'}",
            f"view score: {route.get('view_score')}",
            f"renderer: {route.get('renderer') or 'NONE'}",
        ], GREEN if route["status"] == "ROUTED" else RED)
        mid_y = top + feature_height // 2
        _arrow(draw, (branch_x, mid_y), (feature_rect[0], mid_y))
        _arrow(draw, (feature_rect[2], mid_y), (result_rect[0], mid_y))

    selected = " + ".join(view["id"] for view in case["selected_views"]) or "NONE"
    draw.text((55, 2370), f"View plan: {case['view_plan_status']} | selected views: {selected}", font=_font(21, True), fill=GREEN if case["view_plan_status"] == "PLANNED" else RED)
    canvas.save(output)


def render_overview(output: Path, cases: list[dict[str, Any]]) -> None:
    width, height = 4600, 900 + 580 * len(cases)
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 35), "Classification Tree Audit V1 | Overview", font=_font(40, True), fill=WHITE)
    draw.text((55, 96), "Green means the expected family and every selected feature route were resolved.", font=_font(20), fill=MUTED)
    headers = ((70, "MODEL"), (820, "STRUCTURE"), (1600, "PART FAMILY"), (2480, "SELECTED FEATURES"), (3520, "VIEW / RENDERER"))
    for x, label in headers:
        draw.text((x, 185), label, font=_font(22, True), fill=MUTED)
    for row, case in enumerate(cases):
        top = 260 + row * 580
        centres = []
        rects = [
            (70, top, 680, top + 360),
            (820, top, 1430, top + 360),
            (1600, top, 2260, top + 360),
            (2480, top, 3300, top + 360),
            (3520, top, 4480, top + 360),
        ]
        routed = sum(route["status"] == "ROUTED" for route in case["feature_routes"])
        all_ok = case["family_correct"] and routed == len(case["feature_routes"])
        _box(draw, rects[0], case["name"], [Path(case["step_path"]).name], BLUE)
        _box(draw, rects[1], case["structure"], [f"solids: {case['solid_count']}"], CYAN)
        _box(draw, rects[2], case["production_family"], [
            f"geometry: {case['geometry_family']}",
            f"expected: {case['expected_family']}",
            f"confidence: {case['classification_confidence']:.0%}",
        ], GREEN if case["family_correct"] else RED)
        _box(draw, rects[3], f"{routed}/{len(case['feature_routes'])} ROUTED", [
            ", ".join(route["feature_id"] for route in case["feature_routes"]),
        ], GREEN if routed == len(case["feature_routes"]) else RED)
        _box(draw, rects[4], case["view_plan_status"], [
            " + ".join(view["id"] for view in case["selected_views"]),
            ", ".join(sorted({str(route.get('renderer')) for route in case["feature_routes"]})),
        ], GREEN if all_ok else ORANGE)
        for rect in rects:
            centres.append(((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2))
        for left, right in zip(rects, rects[1:]):
            _arrow(draw, (left[2], (left[1] + left[3]) // 2), (right[0], (right[1] + right[3]) // 2))
    canvas.save(output)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = [build_case(case) for case in config.get("cases") or []]
    render_overview(output_dir / "02_classification_overview.png", cases)
    for index, case in enumerate(cases, start=3):
        image_path = output_dir / f"{index:02d}_{case['name']}_classification_tree.png"
        render_case_tree(image_path, case)
        case["review_image"] = str(image_path.resolve())
        case.pop("_debug_views", None)
    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "cases": cases,
        "metrics": {
            "case_count": len(cases),
            "family_correct_count": sum(case["family_correct"] for case in cases),
            "routed_feature_count": sum(route["status"] == "ROUTED" for case in cases for route in case["feature_routes"]),
            "selected_feature_count": sum(len(case["feature_routes"]) for case in cases),
        },
    }
    report_index = len(cases) + 3
    (output_dir / f"{report_index:02d}_classification_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
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
