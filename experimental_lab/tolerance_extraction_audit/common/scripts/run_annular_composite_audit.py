"""Audit composite annular features and their four dimension semantics."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.drawing_projection import project_point  # noqa: E402
from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from annular_composite_features import (  # noqa: E402
    _best_views,
    build_annular_dimension_candidates,
    extract_annular_composites,
)
from feature_anchor_repair import _primitive_bbox  # noqa: E402
from run_classification_tree_audit import (  # noqa: E402
    BOX, CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, RED, WHITE, YELLOW,
    _edge_points, _font, _mapper,
)
from run_view_plan_experiment import _standard_view_data  # noqa: E402


METHOD = "ANNULAR_COMPOSITE_FEATURE_AUDIT_V1"
DIMENSION_COLOURS = {
    "annular_section.inner_diameter": ORANGE,
    "annular_section.outer_diameter": CYAN,
    "annular_section.radial_wall_thickness": YELLOW,
    "annular_section.axial_thickness": GREEN,
}


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower() or "feature"


def _draw_projection(draw: ImageDraw.ImageDraw, view: dict[str, Any], box: Sequence[int]):
    draw.rectangle(box, fill="#0b0b0b", outline=GRID, width=2)
    mapper = _mapper(view["bbox"], (box[0] + 30, box[1] + 30, box[2] - 30, box[3] - 30))
    for edge in view.get("hidden") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#454545", width=1)
    for edge in view.get("visible") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#e5e5e5", width=3)
    return mapper


def _draw_world_box(draw: ImageDraw.ImageDraw, mapper, bbox: Sequence[float], colour: str, width: int = 5) -> None:
    p0, p1 = mapper((bbox[0], bbox[1])), mapper((bbox[2], bbox[3]))
    draw.rectangle((min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1])), outline=colour, width=width)


def _draw_dimension(draw: ImageDraw.ImageDraw, mapper, dimension: dict[str, Any]) -> None:
    anchors = dimension.get("anchors") or []
    if len(anchors) != 2:
        return
    p0, p1 = mapper(anchors[0]), mapper(anchors[1])
    colour = DIMENSION_COLOURS[dimension["semantic"]]
    draw.line((p0, p1), fill=colour, width=7)
    for point in (p0, p1):
        draw.ellipse((point[0] - 10, point[1] - 10, point[0] + 10, point[1] + 10), fill=colour)


def render_feature(path: Path, case: dict[str, Any], feature: dict[str, Any]) -> None:
    dimensions = build_annular_dimension_candidates(feature)
    end_view, profile_view = _best_views(feature["axis"])
    width, height = 4200, 2350
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    status_colour = GREEN if feature["status"] == "AUTO_VERIFIED" else ORANGE
    draw.text((55, 32), f"Annular Composite Audit | {case['name']}", font=_font(38, True), fill=WHITE)
    draw.text((55, 88), feature["id"], font=_font(20, True), fill=status_colour)
    end_box = (55, 165, 2610, 1125)
    profile_box = (55, 1240, 2610, 2200)
    draw.text((75, 180), f"END VIEW: {end_view.upper()} | inner/outer diameter + radial wall", font=_font(18, True), fill=CYAN)
    draw.text((75, 1255), f"PROFILE VIEW: {profile_view.upper()} | axial thickness", font=_font(18, True), fill=GREEN)
    end_mapper = _draw_projection(draw, case["_views"][end_view], (end_box[0], end_box[1] + 50, end_box[2], end_box[3]))
    profile_mapper = _draw_projection(draw, case["_views"][profile_view], (profile_box[0], profile_box[1] + 50, profile_box[2], profile_box[3]))

    common_length = feature["axial_thickness"]
    outer_end = _primitive_bbox(feature["center"], feature["axis"], common_length, feature["outer_radius"], end_view)
    inner_end = _primitive_bbox(feature["center"], feature["axis"], common_length, feature["inner_radius"], end_view)
    outer_profile = _primitive_bbox(feature["center"], feature["axis"], common_length, feature["outer_radius"], profile_view)
    inner_profile = _primitive_bbox(feature["center"], feature["axis"], common_length, feature["inner_radius"], profile_view)
    _draw_world_box(draw, end_mapper, outer_end, CYAN, 6)
    _draw_world_box(draw, end_mapper, inner_end, ORANGE, 6)
    _draw_world_box(draw, profile_mapper, outer_profile, CYAN, 6)
    _draw_world_box(draw, profile_mapper, inner_profile, ORANGE, 5)
    for dimension in dimensions:
        _draw_dimension(draw, end_mapper if dimension["view"] == end_view else profile_mapper, dimension)

    left = 2700
    draw.rounded_rectangle((left, 165, 4140, 710), radius=14, fill=BOX, outline=status_colour, width=4)
    lines = [
        ("STATUS", f"{feature['status']}  confidence={feature['confidence']:.3f}", status_colour),
        ("TOPOLOGY", f"solid {feature['solid_index']} | inner face {feature['inner_face_index']} | outer face {feature['outer_face_index']}", WHITE),
        ("COAXIAL", f"alignment={feature['axis_alignment']:.6f} | offset={feature['axis_line_offset']:.4f} mm", WHITE),
        ("OVERLAP", f"ratio={feature['axial_overlap_ratio']:.3f}", WHITE),
    ]
    y = 195
    for label, value, colour in lines:
        draw.text((left + 20, y), label, font=_font(16, True), fill=MUTED)
        draw.text((left + 260, y), value, font=_font(17), fill=colour)
        y += 92

    y = 775
    for dimension in dimensions:
        colour = DIMENSION_COLOURS[dimension["semantic"]]
        draw.rounded_rectangle((left, y, 4140, y + 225), radius=12, fill=BOX, outline=colour, width=4)
        draw.text((left + 20, y + 15), dimension["semantic"], font=_font(18, True), fill=colour)
        prefix = "Ø" if dimension["dim_type"] == "DIAMETER" else ""
        draw.text((left + 20, y + 62), f"{prefix}{dimension['nominal_value']:.3f} mm", font=_font(27, True), fill=WHITE)
        draw.text((left + 20, y + 115), f"view={dimension['view']} | {dimension['dim_type']}", font=_font(16), fill=MUTED)
        draw.text((left + 20, y + 155), f"anchors={dimension['anchors']}", font=_font(14), fill=MUTED)
        y += 250
    canvas.save(path)


def render_overview(path: Path, cases: Sequence[dict[str, Any]]) -> None:
    canvas = Image.new("RGB", (3900, 650 + len(cases) * 410), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 35), "Annular Composite Feature Audit V1", font=_font(40, True), fill=WHITE)
    draw.text((55, 98), "Same solid + coaxial + axial overlap; four dimensions per verified annular section.", font=_font(20), fill=MUTED)
    headers = ((65, "MODEL"), (720, "CYLINDER FACES"), (1390, "ANNULAR"), (1990, "AUTO / REVIEW"), (2760, "DIMENSION CANDIDATES"))
    for x, label in headers:
        draw.text((x, 190), label, font=_font(20, True), fill=MUTED)
    for index, case in enumerate(cases):
        y = 260 + index * 410
        draw.rounded_rectangle((45, y, 3845, y + 305), radius=15, fill=BOX, outline=GRID, width=3)
        draw.text((65, y + 45), case["name"], font=_font(25, True), fill=CYAN)
        draw.text((720, y + 45), str(case["cylindrical_face_count"]), font=_font(31, True), fill=WHITE)
        draw.text((1390, y + 45), str(case["annular_feature_count"]), font=_font(31, True), fill=WHITE)
        draw.text((1990, y + 45), f"{case['auto_verified_count']} / {case['review_candidate_count']}", font=_font(31, True), fill=GREEN if not case["review_candidate_count"] else ORANGE)
        draw.text((2760, y + 45), str(case["dimension_candidate_count"]), font=_font(31, True), fill=YELLOW)
        draw.text((65, y + 125), Path(case["step_path"]).name, font=_font(16), fill=MUTED)
        draw.text((65, y + 175), f"rejected inner faces: {len(case['rejected_inner_faces'])}", font=_font(16), fill=MUTED)
    canvas.save(path)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    artifact_index = 3
    for case_config in config.get("cases") or []:
        step_path = _resolve_path(str(case_config["step_path"]))
        shape = load_step(str(step_path))
        result = extract_annular_composites(shape)
        case = {"name": str(case_config["name"]), "step_path": str(step_path.resolve()), **result}
        case["_views"] = _standard_view_data(ViewProjector(), shape)
        case_dir = output_dir / f"{artifact_index:02d}_{_slug(case['name'])}_annular_review"
        case_dir.mkdir(parents=True, exist_ok=True)
        review_images = []
        for feature_index, feature in enumerate(case["features"], start=1):
            image_path = case_dir / f"{feature_index:02d}_{_slug(feature['id'])}.png"
            render_feature(image_path, case, feature)
            review_images.append(image_path.relative_to(output_dir).as_posix())
        case["review_directory"] = case_dir.relative_to(output_dir).as_posix()
        case["review_images"] = review_images
        case.pop("_views", None)
        cases.append(case)
        artifact_index += 1
    render_overview(output_dir / "02_annular_composite_overview.png", cases)
    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "cases": cases,
        "metrics": {
            "case_count": len(cases),
            "cylindrical_face_count": sum(case["cylindrical_face_count"] for case in cases),
            "annular_feature_count": sum(case["annular_feature_count"] for case in cases),
            "auto_verified_count": sum(case["auto_verified_count"] for case in cases),
            "review_candidate_count": sum(case["review_candidate_count"] for case in cases),
            "dimension_candidate_count": sum(case["dimension_candidate_count"] for case in cases),
        },
    }
    (output_dir / "06_annular_composite_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
