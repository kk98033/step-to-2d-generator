"""Render a clean, component-aware fan centre section for drawing-rule experiments.

The experiment scans radial cutting-plane angles, sections every placed STEP
occurrence independently, and selects the cleanest plane that still intersects
the central shaft and hub.  It does not modify the production drawing engine.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform  # noqa: E402
from OCC.Core.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf  # noqa: E402

from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from assembly_feature_resolver import (  # noqa: E402
    extract_component_occurrences,
    view_axis_2d,
)
from auto_2d_drawing.config import VIEW_CONFIG  # noqa: E402
from run_fan_half_section_projection_experiment import (  # noqa: E402
    _central_section_curves,
    _project,
    _shape_bounds,
)


METHOD = "COMPONENT_AWARE_FAN_CENTRE_SECTION_V1"
BACKGROUND = "#111111"
PANEL = "#171717"
GRID = "#404040"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
CYAN = "#22d3ee"
ORANGE = "#f97316"
GREEN = "#34d399"
YELLOW = "#fde047"
COMPONENT_COLOURS = (CYAN, ORANGE, GREEN, "#c084fc", "#60a5fa")


def _font(size: int, bold: bool = False):
    candidates = (
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


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


def _axis_vector(axis_index: int) -> tuple[float, float, float]:
    return tuple(1.0 if index == axis_index else 0.0 for index in range(3))


def _rotated(shape: Any, bounds: Sequence[float], axial_axis: int, angle_degrees: float):
    center = [(float(bounds[index]) + float(bounds[index + 3])) / 2.0 for index in range(3)]
    transform = gp_Trsf()
    transform.SetRotation(
        gp_Ax1(gp_Pnt(*center), gp_Dir(*_axis_vector(axial_axis))),
        math.radians(angle_degrees),
    )
    return BRepBuilderAPI_Transform(shape, transform, True).Shape()


def classify_component_role(
    component_bounds: Sequence[float],
    assembly_bounds: Sequence[float],
    axial_axis: int,
) -> str:
    """Classify participation from relative geometry, never from a part name."""
    radial_axes = [index for index in range(3) if index != axial_axis]
    component_radial = max(component_bounds[index + 3] - component_bounds[index] for index in radial_axes)
    assembly_radial = max(assembly_bounds[index + 3] - assembly_bounds[index] for index in radial_axes)
    ratio = component_radial / max(assembly_radial, 1e-9)
    if ratio <= 0.12:
        return "CENTRAL_SHAFT"
    if ratio <= 0.62:
        return "CENTRAL_HUB"
    return "ROTATING_ENVELOPE"


def _candidate_angles(step_degrees: float) -> list[float]:
    if step_degrees <= 0 or step_degrees > 90:
        raise ValueError("angle step must be in (0, 90]")
    count = max(1, int(math.ceil(180.0 / step_degrees)))
    return [round(index * 180.0 / count, 6) for index in range(count)]


def build_section_candidates(step_path: Path, angle_step_degrees: float = 10.0) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    shape = load_step(str(step_path))
    assembly_bounds = _shape_bounds(shape)
    spans = [assembly_bounds[index + 3] - assembly_bounds[index] for index in range(3)]
    axial_axis = min(range(3), key=lambda index: spans[index])
    radial_axes = [index for index in range(3) if index != axial_axis]
    cut_axis = radial_axes[0]
    view_name = {0: "right", 1: "top", 2: "front"}[cut_axis]
    occurrences = extract_component_occurrences(step_path, axial_axis)
    component_specs = [
        {
            "name": occurrence.name,
            "occurrence_entry": occurrence.occurrence_entry,
            "shape": occurrence.shape,
            "bounds": occurrence.bounds,
            "role": classify_component_role(occurrence.bounds, assembly_bounds, axial_axis),
        }
        for occurrence in occurrences
    ]
    projector = ViewProjector()
    candidates: list[dict[str, Any]] = []
    for angle in _candidate_angles(angle_step_degrees):
        parts = []
        all_points: list[tuple[float, float]] = []
        for spec in component_specs:
            rotated = _rotated(spec["shape"], assembly_bounds, axial_axis, angle)
            try:
                section_shape = _central_section_curves(rotated, assembly_bounds, cut_axis)
                view = _project(projector, section_shape, view_name)
                paths = [_edge_points(edge) for edge in view.get("visible") or []]
                paths = [path for path in paths if len(path) >= 2]
            except RuntimeError:
                paths = []
            all_points.extend(point for path in paths for point in path)
            parts.append({**{key: value for key, value in spec.items() if key != "shape"}, "paths": paths})
        roles = {part["role"] for part in parts if part["paths"]}
        required = {spec["role"] for spec in component_specs if spec["role"] in {"CENTRAL_SHAFT", "CENTRAL_HUB"}}
        required_coverage = len(required & roles) / max(len(required), 1)
        participating = sum(bool(part["paths"]) for part in parts)
        segment_count = sum(max(len(path) - 1, 1) for part in parts for path in part["paths"])
        if all_points:
            bbox = (
                min(point[0] for point in all_points), min(point[1] for point in all_points),
                max(point[0] for point in all_points), max(point[1] for point in all_points),
            )
        else:
            bbox = (0.0, 0.0, 1.0, 1.0)
        # Central components are mandatory; among valid planes prefer complete
        # component participation and then the least cluttered section.
        score = 100.0 * required_coverage + 8.0 * participating - 2.0 * math.log1p(segment_count)
        candidates.append({
            "angle_degrees": angle,
            "score": round(score, 6),
            "required_role_coverage": round(required_coverage, 6),
            "participating_count": participating,
            "segment_count": segment_count,
            "bbox": bbox,
            "parts": parts,
        })
    best = max(candidates, key=lambda item: (item["required_role_coverage"], item["score"], -item["angle_degrees"]))
    center = [(assembly_bounds[index] + assembly_bounds[index + 3]) / 2.0 for index in range(3)]
    config = VIEW_CONFIG[view_name]
    direction = tuple(float(value) for value in config["direction"])
    horizontal = tuple(float(value) for value in config["up"])
    vertical = (
        direction[1] * horizontal[2] - direction[2] * horizontal[1],
        direction[2] * horizontal[0] - direction[0] * horizontal[2],
        direction[0] * horizontal[1] - direction[1] * horizontal[0],
    )
    metadata = {
        "assembly_bounds": assembly_bounds,
        "axial_axis": "XYZ"[axial_axis],
        "cut_normal_axis_after_rotation": "XYZ"[cut_axis],
        "view_direction": view_name,
        "component_count": len(component_specs),
        "projected_axis_origin": [
            sum(center[index] * horizontal[index] for index in range(3)),
            sum(center[index] * vertical[index] for index in range(3)),
        ],
        "projected_axis_direction": list(view_axis_2d(view_name, axial_axis)),
    }
    return metadata, candidates


def _closed_loops(paths: list[list[tuple[float, float]]], tolerance: float) -> list[list[tuple[float, float]]]:
    pending = [list(path) for path in paths if len(path) >= 2]
    loops = []
    while pending:
        chain = pending.pop(0)
        changed = True
        while changed and math.dist(chain[0], chain[-1]) > tolerance:
            changed = False
            for index, path in enumerate(pending):
                options = (
                    (math.dist(chain[-1], path[0]), False, False),
                    (math.dist(chain[-1], path[-1]), True, False),
                    (math.dist(chain[0], path[-1]), False, True),
                    (math.dist(chain[0], path[0]), True, True),
                )
                distance, reverse, prepend = min(options, key=lambda item: item[0])
                if distance > tolerance:
                    continue
                candidate = list(reversed(path)) if reverse else path
                chain = candidate[:-1] + chain if prepend else chain + candidate[1:]
                pending.pop(index)
                changed = True
                break
        if len(chain) >= 3 and math.dist(chain[0], chain[-1]) <= tolerance:
            loops.append(chain)
    return loops


def _draw_hatching(canvas: Image.Image, paths: list[list[tuple[float, float]]], mapper, colour: str, bbox: Sequence[float]) -> int:
    diagonal = math.hypot(float(bbox[2]) - float(bbox[0]), float(bbox[3]) - float(bbox[1]))
    loops = _closed_loops(paths, max(diagonal * 0.0015, 1e-4))
    if not loops:
        return 0
    mask = Image.new("1", canvas.size, 0)
    for loop in loops:
        polygon = Image.new("1", canvas.size, 0)
        ImageDraw.Draw(polygon).polygon([mapper(point) for point in loop], fill=1)
        mask = ImageChops.logical_xor(mask, polygon)
    hatch = Image.new("RGB", canvas.size, BACKGROUND)
    draw = ImageDraw.Draw(hatch)
    spacing = 20
    for offset in range(-canvas.height, canvas.width + canvas.height, spacing):
        draw.line((offset, canvas.height, offset + canvas.height, 0), fill=colour, width=2)
    canvas.paste(hatch, mask=mask)
    return len(loops)


def render_section(output: Path, step_path: Path, metadata: dict[str, Any], best: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, int]:
    width, height = 4200, 2200
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 35), f"Fan centre section | {step_path.name}", font=_font(38, True), fill=WHITE)
    draw.text((55, 92), "Angle scan + XCAF occurrence sectioning; central shaft/hub must participate.", font=_font(20), fill=MUTED)
    section_box = (70, 180, 3140, 2070)
    draw.rectangle(section_box, fill=BACKGROUND, outline=GRID, width=3)
    mapper = _mapper(best["bbox"], (130, 245, 3080, 1980))
    loop_counts: dict[str, int] = {}
    for index, part in enumerate(best["parts"]):
        colour = COMPONENT_COLOURS[index % len(COMPONENT_COLOURS)]
        loop_counts[part["occurrence_entry"]] = _draw_hatching(canvas, part["paths"], mapper, colour, best["bbox"])
        for path in part["paths"]:
            draw.line([mapper(point) for point in path], fill=colour, width=6, joint="curve")
    axis_origin = metadata["projected_axis_origin"]
    axis_direction = metadata["projected_axis_direction"]
    span = 2.0 * math.hypot(best["bbox"][2] - best["bbox"][0], best["bbox"][3] - best["bbox"][1])
    axis_start = mapper((axis_origin[0] - span * axis_direction[0], axis_origin[1] - span * axis_direction[1]))
    axis_end = mapper((axis_origin[0] + span * axis_direction[0], axis_origin[1] + span * axis_direction[1]))
    centreline_mask = Image.new("L", canvas.size, 0)
    centreline_draw = ImageDraw.Draw(centreline_mask)
    for index in range(0, 40, 2):
        t0, t1 = index / 40.0, (index + 1) / 40.0
        centreline_draw.line((
            axis_start[0] + (axis_end[0] - axis_start[0]) * t0,
            axis_start[1] + (axis_end[1] - axis_start[1]) * t0,
            axis_start[0] + (axis_end[0] - axis_start[0]) * t1,
            axis_start[1] + (axis_end[1] - axis_start[1]) * t1,
        ), fill=255, width=3)
    panel_mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(panel_mask).rectangle((section_box[0] + 3, section_box[1] + 3, section_box[2] - 3, section_box[3] - 3), fill=255)
    centreline_mask = ImageChops.multiply(centreline_mask, panel_mask)
    canvas.paste(Image.new("RGB", canvas.size, YELLOW), mask=centreline_mask)

    side_left = 3210
    draw.text((side_left, 190), "Selected cutting plane", font=_font(25, True), fill=WHITE)
    draw.text((side_left, 238), f"rotation: {best['angle_degrees']:.2f} deg", font=_font(21), fill=CYAN)
    draw.text((side_left, 274), f"score: {best['score']:.3f}", font=_font(18), fill=MUTED)
    draw.text((side_left, 310), f"segments: {best['segment_count']}", font=_font(18), fill=MUTED)
    draw.text((side_left, 374), "Component participation", font=_font(25, True), fill=WHITE)
    y = 425
    for index, part in enumerate(best["parts"]):
        colour = COMPONENT_COLOURS[index % len(COMPONENT_COLOURS)]
        status = "SECTIONED" if part["paths"] else "NOT INTERSECTED"
        draw.rectangle((side_left, y + 3, side_left + 24, y + 27), fill=colour)
        draw.text((side_left + 40, y), part["name"][:34], font=_font(17, True), fill=WHITE)
        draw.text((side_left + 40, y + 31), f"{part['role']} | {status}", font=_font(15), fill=MUTED)
        y += 88
    draw.text((side_left, y + 22), "Angle candidates", font=_font(25, True), fill=WHITE)
    y += 72
    for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True)[:10]:
        selected = abs(candidate["angle_degrees"] - best["angle_degrees"]) < 1e-6
        fill = GREEN if selected else MUTED
        text = f"{candidate['angle_degrees']:6.2f} deg  score {candidate['score']:7.3f}  parts {candidate['participating_count']}"
        draw.text((side_left, y), text, font=_font(16, selected), fill=fill)
        y += 35
    draw.text((85, 2115), f"Method: {METHOD} | axial={metadata['axial_axis']} | view={metadata['view_direction']}", font=_font(18), fill=MUTED)
    canvas.save(output)
    return loop_counts


def run(step_path: Path, output_dir: Path, angle_step_degrees: float = 10.0) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata, candidates = build_section_candidates(step_path, angle_step_degrees)
    best = max(candidates, key=lambda item: (item["required_role_coverage"], item["score"], -item["angle_degrees"]))
    image_path = output_dir / "02_fan_centre_section_review.png"
    loop_counts = render_section(image_path, step_path, metadata, best, candidates)
    public_candidates = []
    for candidate in candidates:
        public_candidates.append({
            key: value for key, value in candidate.items() if key != "parts"
        } | {
            "parts": [
                {key: value for key, value in part.items() if key != "paths"} | {
                    "path_count": len(part["paths"]),
                    "closed_loop_count": loop_counts.get(part["occurrence_entry"], 0) if candidate is best else None,
                }
                for part in candidate["parts"]
            ]
        })
    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "step_path": str(step_path.resolve()),
        "angle_step_degrees": angle_step_degrees,
        "metadata": metadata,
        "selected_angle_degrees": best["angle_degrees"],
        "selected_score": best["score"],
        "selected_required_role_coverage": best["required_role_coverage"],
        "candidates": public_candidates,
        "review_image": str(image_path.resolve()),
    }
    (output_dir / "03_fan_centre_section_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--angle-step", type=float, default=10.0)
    args = parser.parse_args()
    report = run(args.step, args.output_dir, args.angle_step)
    print(json.dumps({
        "selected_angle_degrees": report["selected_angle_degrees"],
        "selected_score": report["selected_score"],
        "review_image": report["review_image"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
