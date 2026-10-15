"""Render an audit of solid stepped-shaft dimension semantics."""

from __future__ import annotations

import argparse
import json
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
from feature_anchor_repair import _primitive_bbox  # noqa: E402
from run_classification_tree_audit import (  # noqa: E402
    BOX, CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, RED, WHITE, YELLOW,
    _edge_points, _font, _mapper,
)
from run_view_plan_experiment import _standard_view_data  # noqa: E402
from solid_shaft_features import (  # noqa: E402
    build_solid_shaft_dimension_candidates,
    extract_solid_shaft_features,
)


METHOD = "SOLID_SHAFT_DIMENSION_AUDIT_V1"
SEGMENT_COLOURS = (CYAN, ORANGE, GREEN, "#c084fc", "#60a5fa", "#fb7185")


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


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


def _draw_line(draw: ImageDraw.ImageDraw, mapper, anchors, colour: str, width: int = 7) -> None:
    if len(anchors) != 2:
        return
    p0, p1 = mapper(anchors[0]), mapper(anchors[1])
    draw.line((p0, p1), fill=colour, width=width)
    for point in (p0, p1):
        draw.ellipse((point[0] - 8, point[1] - 8, point[0] + 8, point[1] + 8), fill=colour)


def render_chain(path: Path, case: dict[str, Any], chain: dict[str, Any]) -> None:
    view_name = chain["profile_view"]
    dimensions = build_solid_shaft_dimension_candidates(chain)
    width, height = 4600, 2450
    canvas = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 32), f"Solid Shaft Dimension Audit | {case['name']}", font=_font(38, True), fill=WHITE)
    draw.text((55, 88), f"{chain['id']} | profile={view_name.upper()} | original STEP projection retained", font=_font(19), fill=MUTED)
    projection_box = (55, 165, 3070, 2250)
    mapper = _draw_projection(draw, case["_views"][view_name], projection_box)

    dimension_by_segment = {}
    for dimension in dimensions:
        dimension_by_segment.setdefault(dimension.get("segment_id"), []).append(dimension)
    for index, segment in enumerate(chain["segments"]):
        colour = SEGMENT_COLOURS[index % len(SEGMENT_COLOURS)]
        bbox = _primitive_bbox(segment["center"], segment["axis"], segment["length"], segment["radius"], view_name)
        p0, p1 = mapper((bbox[0], bbox[1])), mapper((bbox[2], bbox[3]))
        draw.rectangle((min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1])), outline=colour, width=6)
        for dimension in dimension_by_segment.get(segment["id"], []):
            _draw_line(draw, mapper, dimension["anchors"], colour, 6 if dimension["dim_type"] == "DIAMETER" else 4)

    overall = next(item for item in dimensions if item["semantic"] == "solid_shaft.overall_length")
    _draw_line(draw, mapper, overall["anchors"], YELLOW, 8)
    for shoulder in chain["shoulders"]:
        left = next(item for item in chain["segments"] if item["id"] == shoulder["left_segment_id"])
        right = next(item for item in chain["segments"] if item["id"] == shoulder["right_segment_id"])
        left_dims = dimension_by_segment[left["id"]]
        right_dims = dimension_by_segment[right["id"]]
        left_anchor = next(item for item in left_dims if item["dim_type"] == "LINEAR")["anchors"][1]
        right_anchor = next(item for item in right_dims if item["dim_type"] == "LINEAR")["anchors"][0]
        point = mapper(((left_anchor[0] + right_anchor[0]) / 2.0, (left_anchor[1] + right_anchor[1]) / 2.0))
        draw.ellipse((point[0] - 13, point[1] - 13, point[0] + 13, point[1] + 13), fill=YELLOW, outline="#111111", width=3)

    left = 3150
    draw.rounded_rectangle((left, 165, 4540, 570), radius=14, fill=BOX, outline=GREEN if chain["status"] == "AUTO_VERIFIED" else ORANGE, width=4)
    summary = [
        f"status: {chain['status']}  confidence={chain['confidence']:.3f}",
        f"overall: {chain['overall_length']:.4f} mm",
        f"segments: {len(chain['segments'])} | shoulders: {len(chain['shoulders'])}",
        f"cylinder coverage: {chain['coverage_ratio']:.1%}",
        f"terminal allowance: {chain['terminal_start_allowance']:.4f} / {chain['terminal_end_allowance']:.4f} mm",
    ]
    for index, line in enumerate(summary):
        draw.text((left + 22, 190 + index * 66), line, font=_font(17, index == 0), fill=WHITE if index else GREEN)

    y = 625
    for index, segment in enumerate(chain["segments"]):
        colour = SEGMENT_COLOURS[index % len(SEGMENT_COLOURS)]
        draw.rounded_rectangle((left, y, 4540, y + 250), radius=12, fill=BOX, outline=colour, width=4)
        draw.text((left + 20, y + 15), segment["id"], font=_font(17, True), fill=colour)
        draw.text((left + 20, y + 58), f"OD {segment['diameter']:.4f} mm | axial {segment['length']:.4f} mm", font=_font(21, True), fill=WHITE)
        draw.text((left + 20, y + 105), f"face {segment['face_index']} | {segment['status']} | confidence {segment['confidence']:.3f}", font=_font(15), fill=MUTED)
        draw.text((left + 20, y + 145), f"interval {segment['start']:.4f} -> {segment['end']:.4f}", font=_font(15), fill=MUTED)
        shoulder = next((item for item in chain["shoulders"] if item["left_segment_id"] == segment["id"]), None)
        if shoulder:
            draw.text((left + 20, y + 187), f"next shoulder radial step {shoulder['radial_step']:.4f} mm", font=_font(15, True), fill=YELLOW)
        y += 275
    canvas.save(path)


def render_overview(path: Path, cases: Sequence[dict[str, Any]]) -> None:
    canvas = Image.new("RGB", (4100, 650 + len(cases) * 390), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((55, 35), "Solid Shaft Dimension Audit V1", font=_font(40, True), fill=WHITE)
    draw.text((55, 98), "Same-solid coaxial outer-cylinder chains; hollow intervals are delegated to the annular workflow.", font=_font(19), fill=MUTED)
    headers = ((65, "MODEL"), (700, "CYLINDERS"), (1330, "CHAINS / SEGMENTS"), (2200, "SHOULDERS"), (2800, "DIMENSIONS"), (3490, "AUTO / REVIEW"))
    for x, label in headers:
        draw.text((x, 190), label, font=_font(19, True), fill=MUTED)
    for index, case in enumerate(cases):
        y = 260 + index * 390
        draw.rounded_rectangle((45, y, 4045, y + 290), radius=15, fill=BOX, outline=GRID, width=3)
        draw.text((65, y + 42), case["name"], font=_font(24, True), fill=CYAN)
        draw.text((700, y + 42), str(case["cylindrical_face_count"]), font=_font(30, True), fill=WHITE)
        draw.text((1330, y + 42), f"{case['shaft_chain_count']} / {case['segment_count']}", font=_font(30, True), fill=WHITE)
        draw.text((2200, y + 42), str(case["shoulder_count"]), font=_font(30, True), fill=YELLOW)
        draw.text((2800, y + 42), str(case["dimension_candidate_count"]), font=_font(30, True), fill=WHITE)
        draw.text((3490, y + 42), f"{case['auto_verified_dimension_count']} / {case['review_candidate_dimension_count']}", font=_font(30, True), fill=GREEN if not case["review_candidate_dimension_count"] else ORANGE)
        draw.text((65, y + 120), Path(case["step_path"]).name, font=_font(16), fill=MUTED)
        draw.text((65, y + 170), f"rejected cylinders: {len(case['rejected_cylinders'])}", font=_font(16), fill=MUTED)
    canvas.save(path)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    artifact_index = 3
    for case_config in config.get("cases") or []:
        step_path = _resolve_path(str(case_config["step_path"]))
        shape = load_step(str(step_path))
        result = extract_solid_shaft_features(shape)
        case = {"name": str(case_config["name"]), "step_path": str(case_config["step_path"]), **result}
        case["_views"] = _standard_view_data(ViewProjector(), shape)
        case_dir = output_dir / f"{artifact_index:02d}_{case['name']}_solid_shaft_review"
        case_dir.mkdir(parents=True, exist_ok=True)
        review_images = []
        for chain_index, chain in enumerate(case["chains"], start=1):
            image_path = case_dir / f"{chain_index:02d}_{chain['id']}.png"
            render_chain(image_path, case, chain)
            review_images.append(image_path.relative_to(output_dir).as_posix())
        case["review_directory"] = case_dir.relative_to(output_dir).as_posix()
        case["review_images"] = review_images
        case.pop("_views", None)
        cases.append(case)
        artifact_index += 1
    render_overview(output_dir / "02_solid_shaft_overview.png", cases)
    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "cases": cases,
        "metrics": {
            "case_count": len(cases),
            "shaft_chain_count": sum(case["shaft_chain_count"] for case in cases),
            "segment_count": sum(case["segment_count"] for case in cases),
            "shoulder_count": sum(case["shoulder_count"] for case in cases),
            "dimension_candidate_count": sum(case["dimension_candidate_count"] for case in cases),
            "auto_verified_dimension_count": sum(case["auto_verified_dimension_count"] for case in cases),
            "review_candidate_dimension_count": sum(case["review_candidate_dimension_count"] for case in cases),
        },
    }
    (output_dir / "05_solid_shaft_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
