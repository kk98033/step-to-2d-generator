"""Generate visual review boards for canonical DXF-to-3D tolerance linkage."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import ezdxf
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.tolerance.canonical_feature_graph import CanonicalFeatureGraphExtractor
from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
from auto_2d_drawing.tolerance.ingest_historical_data import (
    ExtractedDimension,
    HistoricalDataIngestor,
    _extract_dxf_worker,
)
from auto_2d_drawing.pdf_exporter import export_png


BACKGROUND = "#171717"
PANEL = "#262626"
GRID = "#404040"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
BLUE = "#60a5fa"
ORANGE = "#f97316"
GREEN = "#22c55e"
PALETTE = ["#f97316", "#60a5fa", "#22c55e", "#eab308", "#a78bfa"]


def font(size: int, bold: bool = False):
    candidates = [
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def primitive_points(primitive, samples: int = 96):
    if primitive.center is not None and float(primitive.radius or 0.0) > 0.0:
        cx, cy = primitive.center
        radius = float(primitive.radius)
        return [
            (
                cx + radius * math.cos(2.0 * math.pi * index / samples),
                cy + radius * math.sin(2.0 * math.pi * index / samples),
            )
            for index in range(samples + 1)
        ]
    return [(float(point[0]), float(point[1])) for point in primitive.points]


def bounds_of_lines(lines: Iterable[Sequence[Sequence[float]]]):
    points = [point for line in lines for point in line]
    if not points:
        return (0.0, 0.0, 1.0, 1.0)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def mapper(bounds, box, padding: float = 0.96):
    xmin, ymin, xmax, ymax = bounds
    left, top, right, bottom = box
    scale = min(
        (right - left) / max(xmax - xmin, 1e-9),
        (bottom - top) / max(ymax - ymin, 1e-9),
    ) * padding
    offset_x = left + ((right - left) - (xmax - xmin) * scale) / 2.0
    offset_y = top + ((bottom - top) - (ymax - ymin) * scale) / 2.0

    def project(point):
        return (
            offset_x + (float(point[0]) - xmin) * scale,
            bottom - (offset_y - top) - (float(point[1]) - ymin) * scale,
        )

    return project


def tolerance_text(config: Dict[str, Any]) -> str:
    mode = str(config.get("mode") or "")
    if mode == "FIT":
        return str(config.get("fit_class") or "FIT")
    if mode == "CUSTOM_SYMMETRIC":
        return f"±{float(config.get('dev', 0.0)):.3f}"
    if mode in {"CUSTOM_LIMITS", "GROOVE"}:
        upper = float(config.get("upper_dev", 0.0) or 0.0)
        lower = float(config.get("lower_dev", 0.0) or 0.0)
        return f"{upper:+.3f} / {lower:+.3f} mm"
    return mode or "UNKNOWN"


def case_nominal(case) -> Tuple[str, float]:
    field_name = str(case.source_metadata.get("matched_nominal_field") or "")
    return field_name, float((case.nominal_dimensions or {}).get(field_name, 0.0) or 0.0)


def render_shape_with_features(shape, nodes, png_path: Path, stl_path: Path):
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.StlAPI import StlAPI_Writer
    import vtk

    BRepMesh_IncrementalMesh(shape, 0.18, False, 0.30, True).Perform()
    if not StlAPI_Writer().Write(shape, str(stl_path)):
        raise RuntimeError(f"STL export failed: {stl_path}")
    reader = vtk.vtkSTLReader()
    reader.SetFileName(str(stl_path))
    reader.Update()
    poly = reader.GetOutput()
    points = [poly.GetPoint(index) for index in range(poly.GetNumberOfPoints())]
    triangles = []
    for index in range(poly.GetNumberOfCells()):
        cell = poly.GetCell(index)
        if cell.GetNumberOfPoints() == 3:
            triangles.append([cell.GetPointId(offset) for offset in range(3)])

    image = Image.new("RGB", (900, 720), PANEL)
    draw = ImageDraw.Draw(image)
    if not points:
        image.save(png_path)
        return
    azimuth, elevation = math.radians(35), math.radians(28)
    ca, sa = math.cos(azimuth), math.sin(azimuth)
    ce, se = math.cos(elevation), math.sin(elevation)
    center = tuple(sum(point[axis] for point in points) / len(points) for axis in range(3))

    def transform(point):
        x, y, z = (float(point[index]) - center[index] for index in range(3))
        x1, y1 = ca * x - sa * y, sa * x + ca * y
        return x1, ce * y1 - se * z, se * y1 + ce * z

    transformed = [transform(point) for point in points]
    xs, ys = [point[0] for point in transformed], [point[1] for point in transformed]
    scale = min(790 / max(max(xs) - min(xs), 1e-9), 570 / max(max(ys) - min(ys), 1e-9))

    def screen(point):
        projected = transform(point)
        return 450 + projected[0] * scale, 365 - projected[1] * scale

    screen_points = [(450 + point[0] * scale, 365 - point[1] * scale) for point in transformed]
    order = sorted(
        range(len(triangles)),
        key=lambda index: sum(transformed[vertex][2] for vertex in triangles[index]) / 3.0,
    )
    stride = max(1, len(order) // 55000)
    for triangle_index in order[::stride]:
        ids = triangles[triangle_index]
        a, b, c = (transformed[index] for index in ids)
        u = tuple(b[index] - a[index] for index in range(3))
        v = tuple(c[index] - a[index] for index in range(3))
        normal = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        norm = math.sqrt(sum(value * value for value in normal))
        light = 0.55 if norm <= 1e-12 else 0.35 + 0.55 * abs(normal[2] / norm)
        color = tuple(
            int(base * light + fill * (1.0 - light))
            for base, fill in zip((75, 105, 135), (155, 160, 165))
        )
        draw.polygon([screen_points[index] for index in ids], fill=color, outline=(65, 75, 85))

    box_edges = (
        (0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
        (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7),
    )
    for index, node in enumerate(nodes):
        record = dict((node.source_info or {}).get("canonical_record") or {})
        geometry = dict(record.get("geometry") or {})
        feature_center = list(geometry.get("center") or [])
        feature_size = list(geometry.get("size") or [])
        if len(feature_center) < 3 or len(feature_size) < 3:
            continue
        feature_size = [max(abs(float(value)), 0.35) for value in feature_size[:3]]
        corners = [
            [
                float(feature_center[axis]) + (0.5 if mask & (1 << axis) else -0.5) * feature_size[axis]
                for axis in range(3)
            ]
            for mask in range(8)
        ]
        projected = [screen(point) for point in corners]
        color = PALETTE[index % len(PALETTE)]
        for start, end in box_edges:
            draw.line((projected[start], projected[end]), fill=color, width=4)
        label_point = screen(feature_center)
        draw.rounded_rectangle(
            (label_point[0] + 8, label_point[1] - 28, label_point[0] + 190, label_point[1] + 4),
            radius=5,
            fill=BACKGROUND,
            outline=color,
            width=2,
        )
        draw.text((label_point[0] + 15, label_point[1] - 25), node.id, fill=color, font=font(15, True))
    image.save(png_path)


def draw_dxf_panel(draw, analyzer, cases, box, zoom: bool = False):
    lines = [primitive_points(primitive) for primitive in analyzer.primitives]
    lines = [line for line in lines if len(line) >= 2]
    if zoom:
        selected_points = []
        for case in cases:
            points = case.source_metadata.get("points") or {}
            selected_points.extend(
                tuple(value[:2]) for value in points.values()
                if isinstance(value, (list, tuple)) and len(value) >= 2
            )
        if selected_points:
            xs = [point[0] for point in selected_points]
            ys = [point[1] for point in selected_points]
            span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
            pad = span * 0.35 + 1.0
            bounds = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
            visible_lines = [
                line for line in lines
                if any(bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3] for point in line)
            ]
        else:
            visible_lines, bounds = lines, bounds_of_lines(lines)
    else:
        visible_lines, bounds = lines, bounds_of_lines(lines)
    project = mapper(bounds, box, 0.94)

    attached_by_color = {}
    for index, case in enumerate(cases):
        verification = case.source_metadata.get("geometry_verification") or {}
        association = verification.get("association") or {}
        for handle in association.get("attached_geometry_handles") or []:
            attached_by_color[str(handle)] = PALETTE[index % len(PALETTE)]

    for primitive in analyzer.primitives:
        line = primitive_points(primitive)
        if len(line) < 2:
            continue
        if zoom and line not in visible_lines and not any(
            bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]
            for point in line
        ):
            continue
        color = attached_by_color.get(str(primitive.handle), "#737373")
        width = 4 if str(primitive.handle) in attached_by_color else 1
        draw.line([project(point) for point in line], fill=color, width=width)

    for index, case in enumerate(cases):
        color = PALETTE[index % len(PALETTE)]
        points = case.source_metadata.get("points") or {}
        definition_points = [
            (name, tuple(value[:2])) for name, value in points.items()
            if isinstance(value, (list, tuple)) and len(value) >= 2
        ]
        for _name, point in definition_points:
            if zoom and not (bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]):
                continue
            x, y = project(point)
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline=WHITE, width=2)
        if definition_points:
            anchor = definition_points[0][1]
            x, y = project(anchor)
            draw.rounded_rectangle((x + 8, y - 29, x + 210, y + 2), 5, fill=BACKGROUND, outline=color, width=2)
            draw.text((x + 15, y - 26), f"{index + 1}. {case.source_metadata.get('entity_handle')}", fill=color, font=font(15, True))


def draw_attachment_cells(draw, analyzer, cases, box):
    left, top, right, bottom = box
    columns = 2
    rows = max(1, math.ceil(len(cases) / columns))
    gap = 14
    cell_width = (right - left - gap * (columns - 1)) / columns
    cell_height = (bottom - top - gap * (rows - 1)) / rows
    for index, case in enumerate(cases):
        column, row = index % columns, index // columns
        x0 = left + column * (cell_width + gap)
        y0 = top + row * (cell_height + gap)
        x1, y1 = x0 + cell_width, y0 + cell_height
        color = PALETTE[index % len(PALETTE)]
        draw.rounded_rectangle((x0, y0, x1, y1), 5, fill=BACKGROUND, outline=color, width=2)
        metadata = case.source_metadata
        verification = metadata.get("geometry_verification") or {}
        association = verification.get("association") or {}
        handles = [str(value) for value in association.get("attached_geometry_handles") or []]
        primitives = [
            analyzer.primitives[analyzer.handle_index[handle]]
            for handle in handles if handle in analyzer.handle_index
        ]
        lines = [primitive_points(primitive) for primitive in primitives]
        lines = [line for line in lines if len(line) >= 2]
        linked_points = [
            tuple(item.get("point", ())[:2])
            for item in association.get("definition_point_links") or []
            if len(item.get("point", ())) >= 2
        ]
        if not linked_points:
            raw_points = metadata.get("points") or {}
            linked_points = [
                tuple(value[:2]) for value in raw_points.values()
                if isinstance(value, (list, tuple)) and len(value) >= 2
            ]
        bounds = bounds_of_lines([*lines, linked_points])
        project = mapper(bounds, (x0 + 18, y0 + 42, x1 - 18, y1 - 18), 0.78)
        for line in lines:
            draw.line([project(point) for point in line], fill=color, width=4)
        for point in linked_points:
            px, py = project(point)
            draw.ellipse((px - 6, py - 6, px + 6, py + 6), fill=WHITE, outline=color, width=3)
        field_name, nominal = case_nominal(case)
        draw.text(
            (x0 + 12, y0 + 10),
            f"{index + 1}. {metadata.get('matched_feature_id')}.{field_name}  {nominal:g}  {tolerance_text(case.tolerance_config)}",
            fill=color,
            font=font(14, True),
        )


def create_board(name: str, step_path: str, dxf_path: str, shape, graph, cases, analyzer, output_dir: Path):
    safe_name = Path(dxf_path).stem
    model_path = output_dir / f"{safe_name}_model.png"
    stl_path = output_dir / f"{safe_name}_model.stl"
    nodes = [graph.get_node(case.source_metadata.get("matched_feature_id")) for case in cases]
    nodes = [node for node in nodes if node is not None]
    render_shape_with_features(shape, nodes, model_path, stl_path)
    original_dxf_path = output_dir / f"{safe_name}_original_dxf.png"
    dxf_document = ezdxf.readfile(dxf_path)
    export_png(dxf_document, dxf_document.modelspace(), str(original_dxf_path), dark_bg=True)

    board = Image.new("RGB", (3000, 1500), BACKGROUND)
    draw = ImageDraw.Draw(board)
    draw.text((45, 25), f"Tolerance extraction review | {name}", fill=WHITE, font=font(34, True))
    draw.text(
        (47, 72),
        f"STEP: {Path(step_path).name}   DXF: {Path(dxf_path).name}   canonical AUTO_VERIFIED: {len(cases)}",
        fill=MUTED,
        font=font(19),
    )
    headers = (
        ("Original 3D STEP + matched feature boxes", 45),
        ("Original DXF entities + tolerance anchors", 985),
        ("Extracted tolerance records", 2050),
    )
    for text_value, x in headers:
        draw.text((x, 125), text_value, fill=WHITE, font=font(22, True))

    draw.rounded_rectangle((35, 165, 950, 1435), 8, fill=PANEL, outline=GRID, width=2)
    model = Image.open(model_path)
    model.thumbnail((880, 700))
    board.paste(model, (52 + (880 - model.width) // 2, 190))
    draw.text((65, 925), "Colored boxes are generated from canonical feature geometry.", fill=MUTED, font=font(17))
    for index, node in enumerate(nodes):
        color = PALETTE[index % len(PALETTE)]
        y = 975 + index * 75
        draw.rectangle((70, y + 5, 92, y + 27), fill=color)
        draw.text((105, y), f"{index + 1}. {node.id}", fill=color, font=font(18, True))
        draw.text((105, y + 30), f"{node.feature_type} | {node.inferred_role}", fill=MUTED, font=font(15))

    draw.rounded_rectangle((970, 165, 2025, 850), 8, fill=PANEL, outline=GRID, width=2)
    original_dxf = Image.open(original_dxf_path).convert("RGB")
    original_dxf.thumbnail((1015, 645))
    board.paste(
        original_dxf,
        (990 + (1015 - original_dxf.width) // 2, 185 + (645 - original_dxf.height) // 2),
    )
    draw.text((995, 870), "Verified DXF attachment geometry for each tolerance", fill=WHITE, font=font(19, True))
    draw.rounded_rectangle((970, 905, 2025, 1435), 8, fill=PANEL, outline=GRID, width=2)
    draw_attachment_cells(draw, analyzer, cases, (990, 925, 2005, 1415))

    draw.rounded_rectangle((2035, 165, 2965, 1435), 8, fill=PANEL, outline=GRID, width=2)
    y = 195
    for index, case in enumerate(cases):
        color = PALETTE[index % len(PALETTE)]
        field_name, nominal = case_nominal(case)
        metadata = case.source_metadata
        verification = metadata.get("geometry_verification") or {}
        raw_text = str(metadata.get("raw_text") or "").replace("\n", " ")
        if len(raw_text) > 52:
            raw_text = raw_text[:49] + "..."
        card_height = 215
        draw.rounded_rectangle((2060, y, 2940, y + card_height), 7, fill=BACKGROUND, outline=color, width=3)
        draw.text((2082, y + 14), f"{index + 1}. {metadata.get('matched_feature_id')}.{field_name}", fill=color, font=font(21, True))
        draw.text((2082, y + 51), f"{metadata.get('dimension_category')}  nominal={nominal:g} mm", fill=WHITE, font=font(18))
        draw.text((2082, y + 82), f"Tolerance: {tolerance_text(case.tolerance_config)}", fill=WHITE, font=font(20, True))
        draw.text((2082, y + 116), f"DXF entity={metadata.get('entity_handle')}  status={case.verification_status}", fill=MUTED, font=font(16))
        draw.text((2082, y + 145), f"geometry passed={verification.get('passed')}  score={float(verification.get('score', 0.0) or 0.0):.3f}", fill=GREEN, font=font(16, True))
        draw.text((2082, y + 175), f"raw: {raw_text}", fill=MUTED, font=font(15))
        y += card_height + 20
    output = output_dir / f"{safe_name}_tolerance_review.png"
    board.save(output)
    return output


def create_contact_sheet(paths: Sequence[Path], output: Path):
    previews = []
    for path in paths:
        image = Image.open(path).convert("RGB")
        image.thumbnail((1450, 725))
        previews.append(image)
    sheet = Image.new("RGB", (3000, 120 + len(previews) * 760), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    draw.text((45, 28), "Canonical tolerance extraction | visual review", fill=WHITE, font=font(35, True))
    draw.text((47, 76), "Each row: original STEP, original DXF, extracted tolerance, canonical feature identity", fill=MUTED, font=font(18))
    for index, (path, preview) in enumerate(zip(paths, previews)):
        x = (3000 - preview.width) // 2
        y = 120 + index * 760
        sheet.paste(preview, (x, y))
        draw.text((55, y + 8), path.stem, fill=WHITE, font=font(18, True))
    sheet.save(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--drawings", nargs="*", default=[
        "1AL0W5000H-R03.dxf",
        "1FQWY6000H-R00.dxf",
        "1FQ6H6000H-R00.dxf",
    ])
    args = parser.parse_args()
    payload = json.loads(args.dataset.resolve().read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    source_by_drawing = {}
    for record in payload["records"]:
        source = record["source"]
        source_by_drawing.setdefault(source["drawing_file"], source)

    board_paths = []
    manifest = []
    for drawing_name in args.drawings:
        source = source_by_drawing.get(drawing_name)
        if not source:
            continue
        step_path, dxf_path = source["step_path"], source["dxf_path"]
        _source, payloads = _extract_dxf_worker(dxf_path)
        dimensions = [ExtractedDimension(**item) for item in payloads]
        dimensions, _suppressed = HistoricalDataIngestor._deduplicate_dimensions(dimensions)
        dimensions = [item for item in dimensions if HistoricalDataIngestor._tolerance_is_plausible(item)]
        ingestor = HistoricalDataIngestor()
        cases_with_keys, stats = ingestor._verify_pair(
            step_path,
            dxf_path,
            dimensions,
            pair_evidence={
                "pair_id": f"visual-review:{drawing_name}",
                "pair_method": "EXACT_FILENAME",
                "verification_checks": ["frozen_silver_source_pair"],
            },
        )
        verified_cases = [case for case, _keys in cases_with_keys]
        cases = verified_cases[:5]
        if not verified_cases:
            continue
        shape = ingestor._load_step_shape(step_path)
        graph = CanonicalFeatureGraphExtractor().build_graph(shape)
        analyzer = DxfStructure2DAnalyzer(ezdxf.readfile(dxf_path).modelspace())
        board_path = create_board(drawing_name, step_path, dxf_path, shape, graph, cases, analyzer, args.output)
        board_paths.append(board_path)
        manifest.append({
            "drawing": drawing_name,
            "step_path": step_path,
            "dxf_path": dxf_path,
            "board": board_path.name,
            "verified_case_count": len(cases_with_keys),
            "shown_case_count": len(cases),
            "feature_ids": [case.source_metadata.get("matched_feature_id") for case in cases],
            "verification_stats": {key: value for key, value in stats.items() if not key.startswith("_")},
            "cases": [case.to_dict() for case in verified_cases],
        })
    contact_sheet = args.output / "00_tolerance_visual_contact_sheet.png"
    create_contact_sheet(board_paths, contact_sheet)
    (args.output / "visual_manifest.json").write_text(
        json.dumps({
            "schema_version": 2,
            "algorithm": "CANONICAL_FEATURE_REPLAY",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "boards": manifest,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # Keep CLI output safe on Windows terminals that still use cp950.  The
    # persisted manifest above remains human-readable UTF-8.
    print(json.dumps({"contact_sheet": str(contact_sheet), "boards": manifest}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
