"""Render detailed geometry evidence for experiment 19 thin-wall cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.tolerance.assembly_components import load_component_shape  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from adaptive_curved_radial_features import detect_adaptive_curved_radial_partitions  # noqa: E402
from blade_section_semantics import section_shape_view  # noqa: E402
from dimension_intents import build_dimension_intent_set  # noqa: E402
from paired_surface_thickness import extract_section_boundary_thickness  # noqa: E402
from run_classification_tree_audit import _edge_points, _font, _mapper  # noqa: E402
from thin_wall_dimension_pipeline import (  # noqa: E402
    _view_data,
    extract_periodic_thin_wall_dimension,
    extract_planar_thin_wall_dimension,
    extract_thin_wall_dimension,
)
from thickness_dimension_renderer import build_thickness_render_task  # noqa: E402


METHOD = "THIN_WALL_CROSS_FAMILY_DEBUG_V1"
BACKGROUND = "#0b0b0b"
PANEL = "#171717"
GRID = "#404040"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
CYAN = "#22d3ee"
ORANGE = "#f59e0b"
RED = "#ef4444"
GREEN = "#22c55e"


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _shape(case: dict[str, Any], exp15_config: dict[str, Any], exp15_report: dict[str, Any]):
    if case.get("synthetic_kind") == "annular_cylindrical_wall":
        outer = BRepPrimAPI_MakeCylinder(10.0, 25.0).Shape()
        inner = BRepPrimAPI_MakeCylinder(9.2, 25.0).Shape()
        return BRepAlgoAPI_Cut(outer, inner).Shape()
    if case.get("experiment_15_case"):
        name = case["experiment_15_case"]
        config_case = next(item for item in exp15_config["cases"] if item["name"] == name)
        report_case = next(item for item in exp15_report["cases"] if item["name"] == name)
        return load_component_shape(config_case["step_path"], report_case["selected_component"]["label_entry"])
    return load_step(str(_resolve(case["step_path"])))


def _draw_edges(draw: ImageDraw.ImageDraw, view: dict[str, Any], box: Sequence[int]) -> Any:
    mapper = _mapper(view["bbox"], box)
    for collection, colour, width in (("hidden", "#525252", 1), ("visible", "#d4d4d4", 2)):
        for edge in view.get(collection) or []:
            points = _edge_points(edge)
            if len(points) >= 2:
                draw.line([mapper(point) for point in points], fill=colour, width=width)
    return mapper


def _draw_selected_boundaries(draw: ImageDraw.ImageDraw, view: dict[str, Any], detection: dict[str, Any], mapper) -> None:
    refs = detection.get("selected_boundary_edge_refs") or []
    for number, reference in enumerate(refs):
        try:
            collection, index_text = str(reference).split(":", 1)
            edge = (view.get(collection) or [])[int(index_text)]
        except (ValueError, IndexError):
            continue
        points = _edge_points(edge)
        if len(points) >= 2:
            colour = ORANGE if number % 2 == 0 else "#fde047"
            draw.line([mapper(point) for point in points], fill="#111111", width=8)
            draw.line([mapper(point) for point in points], fill=colour, width=4)


def _draw_anchor_pair(draw: ImageDraw.ImageDraw, anchors: Sequence[Sequence[float]], mapper) -> None:
    points = [mapper(point) for point in anchors]
    draw.line(points, fill="#111111", width=9)
    draw.line(points, fill=CYAN, width=5)
    for point in points:
        draw.ellipse((point[0] - 9, point[1] - 9, point[0] + 9, point[1] + 9), fill=RED, outline="#111111", width=3)


def _section_records(shape, fractions: Sequence[float]) -> list[dict[str, Any]]:
    records = []
    for fraction in fractions:
        try:
            view = section_shape_view(shape, float(fraction))
            detection = detect_adaptive_curved_radial_partitions(view)
            thickness = extract_section_boundary_thickness(view, detection)
            records.append({"fraction": fraction, "view": view, "detection": detection, "thickness": thickness})
        except Exception as exc:
            records.append({"fraction": fraction, "error": f"{type(exc).__name__}: {exc}"})
    return records


def _render(case: dict[str, Any], shape, output: Path, fractions: list[float]) -> dict[str, Any]:
    final = extract_thin_wall_dimension(shape, fractions=fractions)
    periodic = extract_periodic_thin_wall_dimension(shape, fractions=fractions)
    planar = extract_planar_thin_wall_dimension(shape)
    sections = _section_records(shape, fractions)
    projector = ViewProjector()
    projections = {name: _view_data(projector, shape, name) for name in ("front", "top", "right")}

    canvas = Image.new("RGB", (4400, 2700), PANEL)
    draw = ImageDraw.Draw(canvas)
    verdict_colour = GREEN if final.get("resolved") else RED
    draw.text((55, 30), f"Experiment 19 DEBUG | {case['name']}", font=_font(38, True), fill=WHITE)
    draw.text((55, 92), f"family: {case['family']} | expected: {case.get('expected_detection')} | detected: {bool(final.get('resolved'))}", font=_font(20, True), fill=verdict_colour)
    draw.text((55, 135), "White: original STEP projection/section | orange: selected repeated boundaries | red+cyan: accepted thickness anchors", font=_font(17), fill=MUTED)

    projection_boxes = [(50, 220, 1460, 1110), (1495, 220, 2905, 1110), (2940, 220, 4350, 1110)]
    for (name, view), box in zip(projections.items(), projection_boxes):
        draw.rectangle(box, fill=BACKGROUND, outline=GRID, width=2)
        mapper = _draw_edges(draw, view, (box[0] + 25, box[1] + 55, box[2] - 25, box[3] - 25))
        draw.text((box[0] + 20, box[1] + 15), f"ORIGINAL {name.upper()} PROJECTION", font=_font(16, True), fill=CYAN)
        if final.get("resolved") and final.get("view", {}).get("name") == name:
            _draw_anchor_pair(draw, final["dimensions"][0]["anchors"], mapper)

    section_boxes = [(50, 1160, 1100, 2160), (1135, 1160, 2185, 2160), (2220, 1160, 3270, 2160), (3305, 1160, 4355, 2160)]
    station_rows = []
    for record, box in zip(sections, section_boxes):
        draw.rectangle(box, fill=BACKGROUND, outline=GRID, width=2)
        fraction = float(record["fraction"])
        draw.text((box[0] + 18, box[1] + 14), f"SECTION {fraction:.2f}", font=_font(16, True), fill=CYAN)
        if record.get("error"):
            draw.text((box[0] + 25, box[1] + 90), record["error"][:75], font=_font(14), fill=RED)
            station_rows.append({"fraction": fraction, "error": record["error"]})
            continue
        view, detection, thickness = record["view"], record["detection"], record["thickness"]
        mapper = _draw_edges(draw, view, (box[0] + 25, box[1] + 70, box[2] - 25, box[3] - 155))
        _draw_selected_boundaries(draw, view, detection, mapper)
        median = (thickness.get("summary") or {}).get("median")
        line1 = f"pattern={detection.get('pattern_count', 0)} | detector={bool(detection.get('resolved'))}"
        line2 = f"pair={bool(thickness.get('resolved'))} | median={median if median is not None else '-'}"
        draw.text((box[0] + 20, box[3] - 125), line1, font=_font(14, True), fill=ORANGE if detection.get("resolved") else MUTED)
        draw.text((box[0] + 20, box[3] - 83), line2, font=_font(14), fill=GREEN if thickness.get("resolved") else RED)
        draw.text((box[0] + 20, box[3] - 43), str(thickness.get("reason") or detection.get("reason"))[:60], font=_font(12), fill=MUTED)
        station_rows.append({
            "fraction": fraction,
            "pattern_count": detection.get("pattern_count", 0),
            "partition_resolved": bool(detection.get("resolved")),
            "pair_resolved": bool(thickness.get("resolved")),
            "median": median,
            "reason": thickness.get("reason") or detection.get("reason"),
        })

    draw.rounded_rectangle((50, 2210, 4355, 2640), radius=12, fill=BACKGROUND, outline=verdict_colour, width=3)
    summary_lines = [
        f"FINAL: {final.get('reason')} | path={final.get('path') or '-'}",
        f"PERIODIC: {periodic.get('reason')} | stable stations={(periodic.get('aggregate') or {}).get('resolved_station_count', 0)}",
        f"PLANAR: {planar.get('reason')} | candidates={(planar.get('detector') or {}).get('candidate_count', 0)}",
    ]
    if final.get("resolved"):
        dimension = final["dimensions"][0]
        summary_lines.append(f"ACCEPTED: {dimension['semantic']} = {dimension['nominal_value']:.6f} mm | {dimension['verification_status']}")
    else:
        summary_lines.append("REJECTED: neither repeated-section nor parallel-planar evidence satisfied the current contract")
    for index, line in enumerate(summary_lines):
        draw.text((80, 2250 + index * 82), line, font=_font(19, index in (0, 3)), fill=verdict_colour if index in (0, 3) else WHITE)
    canvas.save(output)
    return {
        "name": case["name"],
        "method": METHOD,
        "final": {key: final.get(key) for key in ("resolved", "reason", "path")},
        "periodic": {key: periodic.get(key) for key in ("resolved", "reason", "path")},
        "planar": {key: planar.get(key) for key in ("resolved", "reason", "path")},
        "stations": station_rows,
        "output": str(output),
    }


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    exp15_config = json.loads(_resolve(config["experiment_15_config"]).read_text(encoding="utf-8"))
    exp15_report = json.loads(_resolve(config["experiment_15_report"]).read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for index, case in enumerate(config["cases"], start=int(config.get("start_index", 3))):
        case_dir = output_dir / f"{index:02d}_{case['name']}_debug"
        case_dir.mkdir(parents=True, exist_ok=True)
        report = _render(case, _shape(case, exp15_config, exp15_report), case_dir / "01_geometry_failure_debug.png", config["scan_fractions"])
        (case_dir / "debug_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(report)
    summary = {"schema_version": 1, "method": METHOD, "cases": reports}
    (output_dir / "02_debug_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def merge_existing(output_dir: Path) -> dict[str, Any]:
    reports = []
    for path in sorted(output_dir.glob("[0-9][0-9]_*_debug/debug_report.json")):
        reports.append(json.loads(path.read_text(encoding="utf-8")))
    summary = {"schema_version": 1, "method": METHOD, "cases": reports}
    (output_dir / "02_debug_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--merge-existing", action="store_true")
    args = parser.parse_args()
    if args.merge_existing:
        report = merge_existing(args.output_dir)
    else:
        if args.config is None:
            parser.error("--config is required unless --merge-existing is used")
        report = run(args.config, args.output_dir)
    print(json.dumps({"debug_case_count": len(report["cases"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
