"""Run experiment 11: relational angle and mounting-hole dimension intents."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw, ImageOps
import ezdxf


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from angular_dimension_renderer import build_angular_render_tasks  # noqa: E402
from relational_dimension_intents import (  # noqa: E402
    detect_hub_radial_partitions,
    detect_rectangular_hole_pattern,
)
from run_classification_tree_audit import (  # noqa: E402
    BOX, CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, RED, WHITE, YELLOW,
    _edge_points, _font, _mapper,
)
from run_view_plan_experiment import _standard_view_data  # noqa: E402


METHOD = "RELATIONAL_DIMENSION_INTENT_V1"
MAGENTA = "#f472b6"
BLUE = "#60a5fa"


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower() or "case"


def _draw_projection(draw: ImageDraw.ImageDraw, view: dict[str, Any], box: Sequence[int]):
    draw.rectangle(box, fill="#0b0b0b", outline=GRID, width=2)
    mapper = _mapper(view["bbox"], (box[0] + 35, box[1] + 35, box[2] - 35, box[3] - 35))
    for edge in view.get("hidden") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#3f3f46", width=1)
    for edge in view.get("visible") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#d4d4d8", width=2)
    return mapper


def _select_end_view(views: dict[str, dict[str, Any]]) -> str:
    ranked = []
    for order, (name, view) in enumerate(views.items()):
        bbox = view["bbox"]
        width, height = max(bbox[2] - bbox[0], 1e-9), max(bbox[3] - bbox[1], 1e-9)
        aspect_score = min(width, height) / max(width, height)
        circle_count = sum(edge.get("type") == "circle" for edge in (view.get("visible") or []) + (view.get("hidden") or []))
        ranked.append((circle_count * aspect_score, aspect_score, -order, name))
    return max(ranked)[3]


def _ray_point(center: Sequence[float], radius: float, angle: float) -> tuple[float, float]:
    radians = math.radians(angle)
    return float(center[0]) + radius * math.cos(radians), float(center[1]) + radius * math.sin(radians)


def _draw_world_arc(draw: ImageDraw.ImageDraw, mapper, center, radius, start, span, colour, width=6):
    steps = max(8, int(abs(span) / 2.0) + 1)
    points = [mapper(_ray_point(center, radius, start + span * index / steps)) for index in range(steps + 1)]
    draw.line(points, fill=colour, width=width)
    return points


def _edge_from_ref(view: dict[str, Any], reference: str | None) -> dict[str, Any] | None:
    if not reference or ":" not in reference:
        return None
    collection_name, index_text = reference.split(":", 1)
    collection = view.get(collection_name) or []
    index = int(index_text)
    return collection[index] if 0 <= index < len(collection) else None


def render_radial_feature_identity(path: Path, view: dict[str, Any], result: dict[str, Any]) -> None:
    """Render only the actual hub radial structures that drive the trigger."""
    canvas = Image.new("RGB", (4200, 2450), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 30), "Experiment 11 | Detected hub radial features", font=_font(40, True), fill=WHITE)
    draw.text((55, 91), "Orange fill = detected material element | cyan = actual projected boundaries | yellow = derived element centre line", font=_font(19), fill=MUTED)
    box = (55, 165, 2790, 2320)
    mapper = _draw_projection(draw, view, box)
    center = result["center"]
    elements = result.get("elements") or []

    radii = []
    for element in elements:
        for reference in (element.get("left_boundary_ref"), element.get("right_boundary_ref")):
            edge = _edge_from_ref(view, reference)
            if edge and edge.get("type") == "line":
                radii.extend(math.dist(tuple(edge[key]), tuple(center)) for key in ("p1", "p2"))
    inner_radius = min(radii) if radii else float(result.get("reference_radius") or 1.0) * 0.8
    outer_radius = max(radii) if radii else float(result.get("reference_radius") or 1.0) * 1.2

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for element in elements:
        start = float(element["start_angle"])
        span = float(element["angle"])
        steps = max(4, int(span * 2) + 1)
        outer = [mapper(_ray_point(center, outer_radius, start + span * index / steps)) for index in range(steps + 1)]
        inner = [mapper(_ray_point(center, inner_radius, start + span * index / steps)) for index in reversed(range(steps + 1))]
        overlay_draw.polygon(outer + inner, fill=(249, 115, 22, 120))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    centre_px = mapper(center)
    draw.ellipse((centre_px[0] - 14, centre_px[1] - 14, centre_px[0] + 14, centre_px[1] + 14), fill=RED, outline=WHITE, width=3)
    for index, element in enumerate(elements, start=1):
        for reference in (element.get("left_boundary_ref"), element.get("right_boundary_ref")):
            edge = _edge_from_ref(view, reference)
            if edge and edge.get("type") == "line":
                draw.line((mapper(edge["p1"]), mapper(edge["p2"])), fill=CYAN, width=9)
        center_angle = float(element["center_angle"])
        draw.line(
            (mapper(_ray_point(center, inner_radius, center_angle)), mapper(_ray_point(center, outer_radius, center_angle))),
            fill=YELLOW, width=5,
        )
        label_point = mapper(_ray_point(center, outer_radius * 1.18, center_angle))
        draw.ellipse((label_point[0] - 19, label_point[1] - 19, label_point[0] + 19, label_point[1] + 19), fill=BOX, outline=ORANGE, width=3)
        draw.text((label_point[0] - 11, label_point[1] - 13), str(index), font=_font(13, True), fill=WHITE)

    right = 2860
    draw.rounded_rectangle((right, 165, 4140, 545), radius=14, fill=BOX, outline=ORANGE, width=4)
    summary = [
        "semantic family: hub_radial_partition.element",
        f"detected elements: {len(elements)}",
        f"dynamic centre: ({center[0]:.3f}, {center[1]:.3f})",
        f"detected radial band: {inner_radius:.3f} → {outer_radius:.3f} mm",
        "status: REVIEW_CANDIDATE (HLR edge identity not persisted to BRep)",
    ]
    for line_index, line in enumerate(summary):
        draw.text((right + 22, 190 + line_index * 66), line, font=_font(16, line_index in (0, 4)), fill=ORANGE if line_index == 4 else WHITE)

    draw.text((right, 600), "FEATURE INSTANCES", font=_font(19, True), fill=MUTED)
    y = 650
    for index, element in enumerate(elements, start=1):
        colour = "#202020" if index % 2 else "#262626"
        draw.rectangle((right, y, 4140, y + 100), fill=colour, outline=GRID, width=1)
        draw.text((right + 15, y + 12), f"E{index:02d}", font=_font(16, True), fill=ORANGE)
        draw.text((right + 120, y + 10), f"centre {element['center_angle']:.3f}°", font=_font(16), fill=WHITE)
        draw.text((right + 430, y + 10), f"width {element['angle']:.3f}°", font=_font(16), fill=WHITE)
        draw.text((right + 15, y + 55), f"{element.get('left_boundary_ref')}  →  {element.get('right_boundary_ref')}", font=_font(13), fill=MUTED)
        y += 105
    draw.text((right, 2175), "These are the trigger features. The outer fan blades are context only and are not used as the angle divisor.", font=_font(15, True), fill=YELLOW)
    draw.text((right, 2225), "The feature count is observed geometry; no blade-count or file-name rule is used.", font=_font(15), fill=MUTED)
    canvas.save(path)


def _expanded_bbox(bbox: Sequence[float], fraction: float) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = [float(value) for value in bbox]
    pad = max(x1 - x0, y1 - y0) * float(fraction)
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad


def _draw_arrowhead(draw: ImageDraw.ImageDraw, tip, toward, colour: str, size: float = 16.0) -> None:
    dx, dy = toward[0] - tip[0], toward[1] - tip[1]
    length = max(math.hypot(dx, dy), 1e-9)
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    base = (tip[0] + ux * size, tip[1] + uy * size)
    draw.polygon([
        tip,
        (base[0] + px * size * 0.42, base[1] + py * size * 0.42),
        (base[0] - px * size * 0.42, base[1] - py * size * 0.42),
    ], fill=colour)


def render_angular_engineering_drawing(
    path: Path,
    dxf_path: Path,
    view: dict[str, Any],
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Render generic line-extension + arc + arrow angular dimensions."""
    center = [float(value) for value in result["center"]]
    original_bbox = view["bbox"]
    outer_model_radius = max(original_bbox[2] - original_bbox[0], original_bbox[3] - original_bbox[1]) / 2.0
    relations = result.get("pairwise_center_relations") or []
    tasks = build_angular_render_tasks(
        relations,
        [
            {"id": "angle_20", "target_angle": 20.0, "preferred_mid_angle": 0.0, "label": "20.00°"},
            {"id": "angle_30", "target_angle": 30.0, "preferred_mid_angle": 45.0, "label": "30.00°"},
            {"id": "angle_60", "target_angle": 60.0, "preferred_mid_angle": 240.0, "label": "60.00°"},
        ],
        base_radius=outer_model_radius * 1.08,
        lane_spacing=outer_model_radius * 0.13,
    )

    canvas = Image.new("RGB", (4300, 2500), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 28), "Experiment 11 | Generic angular dimension renderer", font=_font(40, True), fill=WHITE)
    draw.text((55, 90), "Actual feature rays → extension lines → dimension arc → arrowheads → angle text", font=_font(20), fill=MUTED)
    drawing_box = (55, 165, 3050, 2360)
    expanded_view = dict(view)
    expanded_view["bbox"] = _expanded_bbox(original_bbox, 0.43)
    mapper = _draw_projection(draw, expanded_view, drawing_box)
    colour = "#22c55e"

    # Centre lines provide the same drafting context as the engineer drawing.
    radius_for_axis = outer_model_radius * 1.38
    draw.line((mapper((center[0] - radius_for_axis, center[1])), mapper((center[0] + radius_for_axis, center[1]))), fill="#2563eb", width=3)
    draw.line((mapper((center[0], center[1] - radius_for_axis)), mapper((center[0], center[1] + radius_for_axis))), fill="#2563eb", width=3)
    centre_px = mapper(center)
    draw.ellipse((centre_px[0] - 10, centre_px[1] - 10, centre_px[0] + 10, centre_px[1] + 10), fill=RED, outline=WHITE, width=2)

    extension_start = float(result.get("reference_radius") or outer_model_radius * 0.25) * 0.75
    for task in tasks:
        extension_end = task["radius"] + outer_model_radius * 0.07
        for angle in (task["start_angle"], task["end_angle"]):
            draw.line(
                (mapper(_ray_point(center, extension_start, angle)), mapper(_ray_point(center, extension_end, angle))),
                fill=colour, width=5,
            )
        arc_points = _draw_world_arc(draw, mapper, center, task["radius"], task["start_angle"], task["angle"], colour, 7)
        if len(arc_points) >= 3:
            _draw_arrowhead(draw, arc_points[0], arc_points[1], colour, 18)
            _draw_arrowhead(draw, arc_points[-1], arc_points[-2], colour, 18)
        mid_angle = (task["start_angle"] + task["angle"] / 2.0) % 360.0
        label_point = mapper(_ray_point(center, task["radius"] + outer_model_radius * 0.065, mid_angle))
        text_box = draw.textbbox((0, 0), task["label"], font=_font(28, True))
        text_width = text_box[2] - text_box[0]
        draw.rectangle((label_point[0] - text_width / 2 - 10, label_point[1] - 22, label_point[0] + text_width / 2 + 10, label_point[1] + 24), fill=PANEL)
        draw.text((label_point[0] - text_width / 2, label_point[1] - 20), task["label"], font=_font(28, True), fill=colour)

    right = 3130
    draw.rounded_rectangle((right, 165, 4240, 710), radius=14, fill=BOX, outline=colour, width=4)
    contract = [
        "renderer: ANGULAR_RELATION_RENDERER",
        "part type required: no",
        "inputs: centre + ray A + ray B + measured angle",
        "placement: collision lanes around view envelope",
        "output: extension lines + arc + arrows + text",
        "source: measured STEP feature relations",
    ]
    for index, line in enumerate(contract):
        draw.text((right + 20, 195 + index * 78), line, font=_font(17, index == 0), fill=colour if index == 0 else WHITE)
    draw.text((right, 770), "RENDER TASKS", font=_font(19, True), fill=MUTED)
    y = 825
    for task in tasks:
        draw.rounded_rectangle((right, y, 4240, y + 260), radius=12, fill=BOX, outline=colour, width=3)
        lines = [
            f"{task['id']}  |  {task['label']}",
            f"ray A: {task['start_angle']:.3f}°",
            f"ray B: {task['end_angle']:.3f}°",
            f"measured relation: {task['measured_relation_angle']:.3f}°",
            f"layout radius: {task['radius']:.3f} mm | lane {task['lane']}",
        ]
        for line_index, line in enumerate(lines):
            draw.text((right + 18, y + 15 + line_index * 44), line, font=_font(15, line_index == 0), fill=colour if line_index == 0 else WHITE)
        y += 285
    draw.text((right, 1760), "This renderer is geometry-driven. A fan classifier may request it, but does not change how the dimension is drawn.", font=_font(15, True), fill=YELLOW)
    canvas.save(path)

    document = ezdxf.new("R2010", setup=True)
    document.header["$INSUNITS"] = 4
    for layer_name, layer_colour in (("VISIBLE", 8), ("HIDDEN", 8), ("CENTER", 5), ("ANGULAR_DIM", 3)):
        if layer_name not in document.layers:
            document.layers.add(layer_name, color=layer_colour)
    modelspace = document.modelspace()
    for layer_name, edges in (("VISIBLE", view.get("visible") or []), ("HIDDEN", view.get("hidden") or [])):
        for edge in edges:
            points = _edge_points(edge)
            if len(points) == 2 and edge.get("type") == "line":
                modelspace.add_line(points[0], points[1], dxfattribs={"layer": layer_name})
            elif len(points) >= 2:
                modelspace.add_lwpolyline(points, dxfattribs={"layer": layer_name})
    modelspace.add_line((center[0] - radius_for_axis, center[1]), (center[0] + radius_for_axis, center[1]), dxfattribs={"layer": "CENTER"})
    modelspace.add_line((center[0], center[1] - radius_for_axis), (center[0], center[1] + radius_for_axis), dxfattribs={"layer": "CENTER"})
    for task in tasks:
        p1 = _ray_point(center, extension_start, task["start_angle"])
        p2 = _ray_point(center, extension_start, task["end_angle"])
        base = _ray_point(center, task["radius"], (task["start_angle"] + task["angle"] / 2.0) % 360.0)
        override = modelspace.add_angular_dim_3p(
            base=base,
            center=center,
            p1=p1,
            p2=p2,
            text=task["label"],
            override={"dimclrd": 3, "dimclre": 3, "dimclrt": 3, "dimasz": 2.3, "dimtxt": 3.2},
            dxfattribs={"layer": "ANGULAR_DIM"},
        )
        override.render()
    document.saveas(dxf_path)
    return tasks


def _unique_examples(items: Sequence[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    result, seen = [], set()
    for item in sorted(items, key=lambda value: value["angle"]):
        key = round(float(item["angle"]), 1)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _historical_angular_dimensions(path: Path) -> list[dict[str, Any]]:
    document = ezdxf.readfile(path)
    dimensions = []
    for entity in document.modelspace().query("DIMENSION"):
        base_type = int(entity.dxf.dimtype) & 15
        if base_type not in (2, 5):
            continue
        measurement = float(entity.get_measurement())
        dimensions.append({
            "handle": str(entity.dxf.handle),
            "base_type": base_type,
            "text_override": str(entity.dxf.text or ""),
            "measurement_degrees": measurement,
            "definition_points": [
                [float(value) for value in list(getattr(entity.dxf, key))[:2]]
                for key in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5")
            ],
        })
    return dimensions


def _match_historical_angles(result: dict[str, Any], dimensions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    relations = result.get("pairwise_center_relations") or []
    matches = []
    for dimension in dimensions:
        measured = float(dimension["measurement_degrees"])
        target = min(measured % 360.0, 360.0 - (measured % 360.0))
        nearest = min(relations, key=lambda item: abs(float(item["angle"]) - target), default=None)
        error = abs(float(nearest["angle"]) - target) if nearest else None
        matches.append({
            **dimension,
            "minor_angle_degrees": target,
            "nearest_step_relation": nearest,
            "absolute_error_degrees": error,
            "numeric_match": error is not None and error <= 0.15,
            "anchor_registration_status": "UNRESOLVED",
            "verification_status": "REVIEW_CANDIDATE",
        })
    return matches


def render_fan_review(path: Path, views: dict[str, dict[str, Any]], view_name: str, result: dict[str, Any], historical_image: Path | None) -> None:
    canvas = Image.new("RGB", (4300, 2700), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 28), "Experiment 11 | Hub radial relational angles", font=_font(40, True), fill=WHITE)
    draw.text((55, 88), "Measured from actual projected radial boundaries. Never inferred from 360° / count.", font=_font(20), fill=MUTED)
    projection_box = (55, 165, 2630, 2250)
    mapper = _draw_projection(draw, views[view_name], projection_box)
    center = result["center"]
    reference_radius = float(result.get("reference_radius") or 1.0)
    centre_px = mapper(center)
    draw.ellipse((centre_px[0] - 15, centre_px[1] - 15, centre_px[0] + 15, centre_px[1] + 15), fill=RED, outline=WHITE, width=3)

    for angle in result.get("boundary_angles") or []:
        p0 = mapper(_ray_point(center, reference_radius * 0.72, angle))
        p1 = mapper(_ray_point(center, reference_radius * 1.34, angle))
        draw.line((p0, p1), fill=CYAN, width=4)

    examples = []
    colour_sets = (
        ("ELEMENT", result.get("elements") or [], ORANGE),
        ("GAP", result.get("gaps") or [], MAGENTA),
        ("CENTER PITCH", result.get("center_pitches") or [], BLUE),
    )
    radii = {"ELEMENT": 1.48, "GAP": 1.64, "CENTER PITCH": 1.82}
    for label, items, colour in colour_sets:
        for item in _unique_examples(items):
            _draw_world_arc(draw, mapper, center, reference_radius * radii[label], item["start_angle"], item["angle"], colour, 7)
            examples.append((label, item, colour))

    right = 2700
    draw.rounded_rectangle((right, 165, 4240, 920), radius=14, fill=BOX, outline=ORANGE, width=4)
    summary = [
        f"view: {view_name}",
        f"status: {result.get('verification_status')}",
        f"centre: ({center[0]:.3f}, {center[1]:.3f})",
        f"boundaries: {len(result.get('boundary_angles') or [])}",
        f"elements / gaps / pitches: {len(result.get('elements') or [])} / {len(result.get('gaps') or [])} / {len(result.get('center_pitches') or [])}",
        f"projected BRep edge identity persisted: {result.get('projected_edge_identity_persisted')}",
    ]
    for index, line in enumerate(summary):
        draw.text((right + 25, 195 + index * 92), line, font=_font(19, index == 1), fill=ORANGE if index == 1 else WHITE)
    draw.text((right + 25, 770), "Candidate is deliberate: geometry is measured, but HLR edge → BRep identity is not yet persistent.", font=_font(16), fill=MUTED)

    y = 975
    draw.text((right, y), "DISTINCT MEASURED ANGLES", font=_font(20, True), fill=WHITE)
    y += 55
    for label, item, colour in examples:
        draw.rounded_rectangle((right, y, 4240, y + 105), radius=10, fill=BOX, outline=colour, width=3)
        draw.text((right + 20, y + 18), label, font=_font(16, True), fill=colour)
        draw.text((right + 380, y + 13), f"{item['angle']:.3f}°", font=_font(24, True), fill=WHITE)
        draw.text((right + 700, y + 21), f"{item['start_angle']:.3f}° → {item['end_angle']:.3f}°", font=_font(15), fill=MUTED)
        y += 122

    historical_dimensions = result.get("historical_angular_dimensions") or []
    if historical_dimensions:
        draw.text((right, y + 10), "NATIVE DXF ANGULAR DIMENSIONS", font=_font(18, True), fill=GREEN)
        y += 55
        for item in historical_dimensions[:4]:
            match = "numeric STEP match" if item["numeric_match"] else "no numeric match"
            draw.text(
                (right + 15, y),
                f"{item['handle']} | text={item['text_override'] or '<>'} | {item['measurement_degrees']:.3f}° | {match} | anchors unresolved",
                font=_font(14), fill=GREEN if item["numeric_match"] else ORANGE,
            )
            y += 42

    if historical_image and historical_image.exists():
        reference = Image.open(historical_image).convert("RGB")
        reference = ImageOps.contain(reference, (1450, 560))
        ref_y = 2070
        canvas.paste(reference, (right, ref_y))
        draw.rectangle((right, ref_y, right + reference.width, ref_y + reference.height), outline=GRID, width=3)
        draw.text((right, ref_y - 35), "Engineer-authored drawing reference", font=_font(17, True), fill=GREEN)
    canvas.save(path)


def render_housing_review(path: Path, views: dict[str, dict[str, Any]], result: dict[str, Any]) -> None:
    canvas = Image.new("RGB", (4000, 2250), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 28), "Experiment 11 | Rectangular mounting-hole relations", font=_font(40, True), fill=WHITE)
    draw.text((55, 88), "Same-axis, same-diameter BRep hole faces; dimensions come from actual projected centres.", font=_font(20), fill=MUTED)
    view_name = result["view"]
    mapper = _draw_projection(draw, views[view_name], (55, 165, 2720, 2140))
    points = [mapper(point) for point in result["centers"]]
    radius_px = 18
    for index, point in enumerate(points, start=1):
        draw.ellipse((point[0] - radius_px, point[1] - radius_px, point[0] + radius_px, point[1] + radius_px), fill=ORANGE, outline=WHITE, width=3)
        draw.text((point[0] + 22, point[1] - 22), f"H{index}", font=_font(15, True), fill=ORANGE)
    left_x, right_x = min(point[0] for point in points), max(point[0] for point in points)
    top_y, bottom_y = min(point[1] for point in points), max(point[1] for point in points)
    draw.line((left_x, top_y - 65, right_x, top_y - 65), fill=CYAN, width=7)
    draw.line((right_x + 65, top_y, right_x + 65, bottom_y), fill=YELLOW, width=7)
    draw.text(((left_x + right_x) / 2 - 100, top_y - 115), f"{result['horizontal_center_distance']:.3f} mm", font=_font(19, True), fill=CYAN)
    draw.text((right_x + 90, (top_y + bottom_y) / 2), f"{result['vertical_center_distance']:.3f} mm", font=_font(19, True), fill=YELLOW)

    x = 2800
    draw.rounded_rectangle((x, 165, 3940, 970), radius=14, fill=BOX, outline=GREEN if result["verification_status"] == "AUTO_VERIFIED" else ORANGE, width=4)
    lines = [
        f"status: {result['verification_status']}",
        f"confidence: {result['confidence']:.3f}",
        f"view: {view_name}",
        f"hole diameter: Ø{result['diameter']:.3f} mm",
        f"horizontal centres: {result['horizontal_center_distance']:.3f} mm",
        f"vertical centres: {result['vertical_center_distance']:.3f} mm",
        f"rectangle residual: {result['rectangle_residual_ratio']:.5f}",
        f"topology refs: {len(result['topology_refs'])}",
    ]
    for index, line in enumerate(lines):
        draw.text((x + 22, 195 + index * 88), line, font=_font(18, index == 0), fill=GREEN if index == 0 else WHITE)
    draw.text((x, 1040), "TOPOLOGY REFERENCES", font=_font(19, True), fill=MUTED)
    for index, reference in enumerate(result["topology_refs"]):
        draw.text((x + 20, 1100 + index * 65), reference, font=_font(16), fill=ORANGE)
    canvas.save(path)


def render_overview(path: Path, fan: dict[str, Any], holes: dict[str, Any]) -> None:
    canvas = Image.new("RGB", (3600, 1400), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 35), "Relational Dimension Intent V1", font=_font(42, True), fill=WHITE)
    draw.text((55, 100), "Dimensions between features, not isolated feature extents.", font=_font(21), fill=MUTED)
    cards = [
        ("FAN HUB RADIAL PARTITIONS", fan, ORANGE, [
            f"boundaries: {len(fan.get('boundary_angles') or [])}",
            f"element angles: {len(fan.get('elements') or [])}",
            f"gap angles: {len(fan.get('gaps') or [])}",
            f"centre pitches: {len(fan.get('center_pitches') or [])}",
            "source: actual STEP projection boundaries",
            "forbidden inference: 360° / count",
        ]),
        ("HOUSING RECTANGULAR HOLE PATTERN", holes, CYAN, [
            f"hole diameter: {holes.get('diameter', 0):.3f} mm",
            f"horizontal centres: {holes.get('horizontal_center_distance', 0):.3f} mm",
            f"vertical centres: {holes.get('vertical_center_distance', 0):.3f} mm",
            f"topology references: {len(holes.get('topology_refs') or [])}",
            "source: same-axis BRep cylindrical faces",
            "relations: diameter + X pitch + Y pitch",
        ]),
    ]
    for index, (title, result, colour, lines) in enumerate(cards):
        left = 55 + index * 1770
        draw.rounded_rectangle((left, 210, left + 1690, 1280), radius=18, fill=BOX, outline=colour, width=5)
        draw.text((left + 30, 250), title, font=_font(25, True), fill=colour)
        status = result.get("verification_status") or ("UNRESOLVED" if not result.get("resolved") else "REVIEW_CANDIDATE")
        draw.text((left + 30, 335), status, font=_font(31, True), fill=GREEN if status == "AUTO_VERIFIED" else ORANGE)
        for line_index, line in enumerate(lines):
            draw.text((left + 30, 450 + line_index * 115), line, font=_font(20), fill=WHITE if line_index < 4 else MUTED)
    canvas.save(path)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)

    fan_config = config["fan_case"]
    fan_shape = load_step(str(_resolve_path(fan_config["step_path"])))
    fan_views = _standard_view_data(ViewProjector(), fan_shape)
    fan_view = str(fan_config.get("view") or "auto")
    if fan_view == "auto":
        fan_view = _select_end_view(fan_views)
    fan = detect_hub_radial_partitions(fan_views[fan_view])
    fan["view"] = fan_view
    fan_dir = output_dir / "03_fan_impeller_relational_angle_review"
    fan_dir.mkdir(parents=True, exist_ok=True)
    historical_value = fan_config.get("historical_reference_image")
    historical_path = _resolve_path(historical_value) if historical_value else None
    historical_dxf_value = fan_config.get("historical_dxf")
    if historical_dxf_value:
        historical_dimensions = _historical_angular_dimensions(_resolve_path(historical_dxf_value))
        fan["historical_angular_dimensions"] = _match_historical_angles(fan, historical_dimensions)
    render_fan_review(fan_dir / "01_hub_radial_angle_debug.png", fan_views, fan_view, fan, historical_path)
    render_radial_feature_identity(fan_dir / "02_hub_radial_feature_identity.png", fan_views[fan_view], fan)
    angular_tasks = render_angular_engineering_drawing(
        fan_dir / "03_hub_radial_angular_dimension_render.png",
        fan_dir / "04_hub_radial_angular_dimension_render.dxf",
        fan_views[fan_view],
        fan,
    )
    fan["angular_render_tasks"] = angular_tasks

    housing_config = config["housing_case"]
    housing_shape = load_step(str(_resolve_path(housing_config["step_path"])))
    housing_views = _standard_view_data(ViewProjector(), housing_shape)
    holes = detect_rectangular_hole_pattern(housing_shape)
    housing_dir = output_dir / "04_fan_housing_relational_hole_review"
    housing_dir.mkdir(parents=True, exist_ok=True)
    if holes.get("resolved"):
        render_housing_review(housing_dir / "01_mounting_hole_pattern_debug.png", housing_views, holes)

    render_overview(output_dir / "02_relational_dimension_overview.png", fan, holes)
    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "fan_case": {"name": fan_config["name"], "step_path": str(_resolve_path(fan_config["step_path"])), **fan},
        "housing_case": {"name": housing_config["name"], "step_path": str(_resolve_path(housing_config["step_path"])), **holes},
        "metrics": {
            "fan_resolved": bool(fan.get("resolved")),
            "fan_boundary_count": len(fan.get("boundary_angles") or []),
            "fan_element_angle_count": len(fan.get("elements") or []),
            "fan_gap_angle_count": len(fan.get("gaps") or []),
            "fan_center_pitch_count": len(fan.get("center_pitches") or []),
            "housing_pattern_resolved": bool(holes.get("resolved")),
            "housing_dimension_intent_count": len(holes.get("intents") or []),
        },
    }
    (output_dir / "05_relational_dimension_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
