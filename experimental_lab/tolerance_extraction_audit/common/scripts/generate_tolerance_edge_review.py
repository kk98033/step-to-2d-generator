"""Render extracted tolerance-to-DXF-edge traces on the original drawing."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import ezdxf
from PIL import Image, ImageDraw, ImageFont
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing import layout as drawing_layout
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.math import BoundingBox2d
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg
import pypdfium2 as pdfium

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
from auto_2d_drawing.tolerance.ingest_historical_data import (
    ExtractedDimension, HistoricalDataIngestor, _extract_dxf_worker,
)
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2
from run_projection_pairing_v2 import bounds_of_primitives, make_mapper


BACKGROUND = "#171717"
PANEL = "#262626"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
CYAN = "#22d3ee"
MAGENTA = "#ff2bd6"
YELLOW = "#facc15"
RED = "#ef4444"


def _font(size):
    for path in (Path("C:/Windows/Fonts/consola.ttf"), Path("C:/Windows/Fonts/arial.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _safe(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_") or "item"


def _format_tolerance(dim):
    cfg = dict(dim.tolerance_config or {})
    mode = str(cfg.get("mode") or "").upper()
    nominal = f"{float(dim.nominal_value):g} mm"
    if mode == "FIT" and cfg.get("fit_class"):
        return f"{nominal} {cfg['fit_class']}"
    upper = cfg.get("upper_dev")
    lower = cfg.get("lower_dev")
    if upper is not None or lower is not None:
        return f"{nominal} (+{float(upper or 0):.3f} / {float(lower or 0):+.3f})"
    symmetric = cfg.get("value", cfg.get("tolerance"))
    if symmetric is not None:
        return f"{nominal} ±{float(symmetric):g}"
    return f"{nominal} | {dim.raw_text}"


def _dimension_anchor(dim):
    for key in ("text_mid", "text_pos", "defpoint", "defpoint2", "defpoint3"):
        point = (dim.points or {}).get(key)
        if point and len(point) >= 2:
            return float(point[0]), float(point[1])
    return None


def _edge_points(edge):
    points = edge.get("points") or []
    result = [
        (float(point[0]), float(point[1]))
        for point in points
        if len(point) >= 2
    ]
    if result:
        return result
    # Native DXF CIRCLE/ARC associations intentionally store their analytic
    # center and radius instead of a polyline.  The semantic review renderer
    # previously skipped those edges entirely, making a correct diameter
    # match look like only a colored text anchor.  Sample the analytic circle
    # for the review overlay; this does not alter extraction or verification.
    center = edge.get("center")
    radius = float(edge.get("radius", 0.0) or 0.0)
    if (
        isinstance(center, (list, tuple))
        and len(center) >= 2
        and radius > 0.0
        and str(edge.get("entity_type") or "").upper() in {"CIRCLE", "ARC"}
    ):
        count = 96
        return [
            (
                float(center[0]) + radius * math.cos(2.0 * math.pi * index / count),
                float(center[1]) + radius * math.sin(2.0 * math.pi * index / count),
            )
            for index in range(count + 1)
        ]
    return []


def _point_to_path_distance(point, points):
    if not points:
        return float("inf")
    if len(points) == 1:
        return math.dist(point, points[0])
    best = float("inf")
    px, py = point
    for left, right in zip(points, points[1:]):
        ax, ay = left
        bx, by = right
        dx, dy = bx - ax, by - ay
        denominator = dx * dx + dy * dy
        if denominator <= 1e-12:
            distance = math.dist(point, left)
        else:
            t = max(0.0, min(1.0, ((px-ax)*dx + (py-ay)*dy) / denominator))
            distance = math.hypot(px - (ax + t*dx), py - (ay + t*dy))
        best = min(best, distance)
    return best


def _recovered_endpoint_edges(extractor, dim, association):
    """Follow recovered diameter endpoints back to the two product profiles."""
    recovered = dict(association.get("recovered_dimension_geometry") or {})
    endpoints = recovered.get("endpoints") or []
    if recovered.get("kind") != "DIAMETER_ENDPOINT_PAIR" or len(endpoints) != 2:
        return []
    tolerance = max(0.10, min(0.50, abs(float(dim.nominal_value or 0.0)) * 0.04))
    result = []
    used = set()
    for endpoint in endpoints:
        point = (float(endpoint[0]), float(endpoint[1]))
        ranked = sorted(
            (
                (_point_to_path_distance(point, primitive.points), primitive)
                for primitive in extractor.geometry_primitives
                if len(primitive.points) >= 2
            ),
            key=lambda row: row[0],
        )
        if not ranked or ranked[0][0] > tolerance:
            continue
        distance, primitive = ranked[0]
        if primitive.primitive_id in used:
            continue
        used.add(primitive.primitive_id)
        result.append({
            "handle": primitive.primitive_id,
            "entity_type": primitive.entity_type,
            "layer": primitive.layer,
            "relation": "RECOVERED_DIAMETER_ENDPOINT_PROFILE",
            "distance": round(float(distance), 6),
            "definition_points": [{"name": "recovered_endpoint", "point": list(point)}],
            "points": [[round(float(x), 5), round(float(y), 5)] for x, y in primitive.points],
        })
    return result


def _edge_bounds(edges, anchor):
    points = [point for edge in edges for point in _edge_points(edge)]
    if anchor:
        points.append(anchor)
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    span = max(max(xs)-min(xs), max(ys)-min(ys), 1.0)
    margin = span * 0.55
    return min(xs)-margin, min(ys)-margin, max(xs)+margin, max(ys)+margin


def _focus_bounds(dim, edges):
    points = [point for edge in edges for point in _edge_points(edge)]
    for value in (dim.points or {}).values():
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            x, y = float(value[0]), float(value[1])
            if abs(x) > 1e-9 or abs(y) > 1e-9:
                points.append((x, y))
    if not points:
        return None
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    width, height = max(xs)-min(xs), max(ys)-min(ys)
    span = max(width, height, abs(float(dim.nominal_value or 0.0)), 1.0)
    margin = span * 0.35
    return min(xs)-margin, min(ys)-margin, max(xs)+margin, max(ys)+margin


def _rgb(entity, value):
    entity.rgb = value
    return entity


def _add_overlay_entities(
    doc, dim, association, edges, sheet_bounds,
    edge_rgb=(255, 43, 214), span_rgb=(34, 211, 238), label=None,
):
    msp = doc.modelspace()
    for name, color in (
        ("AUDIT_TARGET_EDGE", edge_rgb),
        ("AUDIT_MEASURED_SPAN", span_rgb),
        ("AUDIT_CONTACT", (250, 204, 21)),
    ):
        if name not in doc.layers:
            doc.layers.add(name=name, color=7)
    diagonal = math.hypot(
        sheet_bounds[2]-sheet_bounds[0], sheet_bounds[3]-sheet_bounds[1]
    )
    marker_radius = max(diagonal * 0.0015, 0.15)
    for edge in edges:
        points = _edge_points(edge)
        if len(points) < 2:
            continue
        outline = msp.add_lwpolyline(
            points,
            dxfattribs={"layer": "AUDIT_TARGET_EDGE", "lineweight": 200},
        )
        _rgb(outline, (5, 5, 5))
        entity = msp.add_lwpolyline(
            points,
            dxfattribs={"layer": "AUDIT_TARGET_EDGE", "lineweight": 120},
        )
        _rgb(entity, edge_rgb)
        for point in (points[0], points[-1]):
            circle = msp.add_circle(
                point, marker_radius,
                dxfattribs={"layer": "AUDIT_CONTACT", "lineweight": 70},
            )
            _rgb(circle, (250, 204, 21))
    recovered = dict(association.get("recovered_dimension_geometry") or {})
    endpoints = recovered.get("endpoints") or []
    if recovered.get("kind") in {"DIAMETER_ENDPOINT_PAIR", "LINEAR_ENDPOINT_PAIR"} and len(endpoints) == 2:
        start = (float(endpoints[0][0]), float(endpoints[0][1]))
        end = (float(endpoints[1][0]), float(endpoints[1][1]))
        length = max(math.dist(start, end), 1e-9)
        segment_count = 9
        for index in range(segment_count):
            if index % 2:
                continue
            a, b = index / segment_count, (index + 1) / segment_count
            p1 = (start[0]+(end[0]-start[0])*a, start[1]+(end[1]-start[1])*a)
            p2 = (start[0]+(end[0]-start[0])*b, start[1]+(end[1]-start[1])*b)
            line = msp.add_line(
                p1, p2,
                dxfattribs={"layer": "AUDIT_MEASURED_SPAN", "lineweight": 70},
            )
            _rgb(line, span_rgb)
    if label:
        anchor = _dimension_anchor(dim)
        if anchor:
            marker = msp.add_circle(
                anchor, marker_radius * 2.2,
                dxfattribs={"layer": "AUDIT_CONTACT", "lineweight": 100},
            )
            _rgb(marker, span_rgb)
            text = msp.add_mtext(
                str(label),
                dxfattribs={"char_height": marker_radius * 1.2, "lineweight": 70},
            )
            _rgb(text, span_rgb)
            text.set_location((anchor[0] + marker_radius * 2.7, anchor[1] + marker_radius * 1.5))


def _render_native_dxf(
    dxf_path, dim, association, edges, sheet_bounds, render_bounds, output, size,
    edge_rgb=(255, 43, 214), span_rgb=(34, 211, 238), label=None,
):
    """Render original annotations and the audit overlay through ezdxf."""
    doc = ezdxf.readfile(str(dxf_path))
    _add_overlay_entities(
        doc, dim, association, edges, sheet_bounds,
        edge_rgb=edge_rgb, span_rgb=span_rgb, label=label,
    )
    msp = doc.modelspace()
    context = RenderContext(doc)
    context.current_layout_properties.set_colors(BACKGROUND)
    backend = SVGBackend()
    Frontend(context, backend).draw_layout(msp, finalize=True)
    xmin, ymin, xmax, ymax = render_bounds
    width, height = max(xmax-xmin, 1e-9), max(ymax-ymin, 1e-9)
    padding = 0.04
    render_box = BoundingBox2d([
        (xmin-width*padding, ymin-height*padding),
        (xmax+width*padding, ymax+height*padding),
    ])
    page = drawing_layout.Page.from_dxf_layout(msp)
    svg_text = backend.get_string(page, render_box=render_box)
    svg_path = output.with_suffix(".svg")
    pdf_path = output.with_suffix(".pdf")
    svg_path.write_text(svg_text, encoding="utf-8")
    drawing = svg2rlg(str(svg_path))
    if drawing is None:
        raise RuntimeError(f"SVG conversion failed: {svg_path}")
    renderPDF.drawToFile(drawing, str(pdf_path))
    pdf = pdfium.PdfDocument(str(pdf_path))
    pdf_width, pdf_height = pdf[0].get_size()
    scale = max(size[0] / max(pdf_width, 1.0), size[1] / max(pdf_height, 1.0))
    rendered = pdf[0].render(scale=scale).to_pil().convert("RGB")
    rendered.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, BACKGROUND)
    canvas.paste(rendered, ((size[0]-rendered.width)//2, (size[1]-rendered.height)//2))
    canvas.save(output)


def _draw_geometry(draw, primitives, mapper, color="#737373", width=1):
    for primitive in primitives:
        points = [mapper(point) for point in primitive.points]
        if len(points) >= 2:
            draw.line(points, fill=color, width=width)


def _draw_edges(draw, edges, mapper):
    for edge in edges:
        points = [mapper(point) for point in _edge_points(edge)]
        if len(points) < 2:
            continue
        draw.line(points, fill="#000000", width=13)
        draw.line(points, fill=MAGENTA, width=8)
        for point in (points[0], points[-1]):
            draw.ellipse((point[0]-7, point[1]-7, point[0]+7, point[1]+7), fill=YELLOW, outline="#000000", width=2)


def _draw_measurement_span(draw, association, mapper):
    recovered = dict(association.get("recovered_dimension_geometry") or {})
    endpoints = recovered.get("endpoints") or []
    if recovered.get("kind") != "DIAMETER_ENDPOINT_PAIR" or len(endpoints) != 2:
        return
    left, right = mapper(endpoints[0]), mapper(endpoints[1])
    length = max(math.dist(left, right), 1.0)
    segments = max(1, int(length // 18))
    for index in range(segments):
        if index % 2:
            continue
        a = index / segments
        b = min(1.0, (index + 1) / segments)
        draw.line((
            left[0] + (right[0]-left[0])*a, left[1] + (right[1]-left[1])*a,
            left[0] + (right[0]-left[0])*b, left[1] + (right[1]-left[1])*b,
        ), fill=CYAN, width=5)


def _render(dxf_path, extractor, dim, association, edges, output):
    width, height = 3200, 1800
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((20, 20, width-20, height-20), radius=16, fill=PANEL)
    anchor = _dimension_anchor(dim)
    status = str(association.get("association_status") or "NO_ATTACHMENT")
    status_color = MAGENTA if edges else RED
    draw.text((48, 38), _format_tolerance(dim), font=_font(32), fill=WHITE)
    draw.text(
        (48, 84),
        f"handle={dim.entity_handle or '-'}  category={dim.dimension_category}  "
        f"edge-link={status}  confidence={float(association.get('association_confidence', 0.0)):.2f}",
        font=_font(19), fill=status_color,
    )
    draw.text((48, 128), "ORIGINAL DXF WITH ALL DIMENSIONS + TARGET EDGE", font=_font(22), fill=WHITE)
    draw.text((1910, 128), "NATIVE DXF LOCAL DETAIL", font=_font(22), fill=WHITE)
    full_box = (45, 170, 1845, 1735)
    zoom_box = (1885, 170, 3155, 1735)
    draw.rectangle(full_box, fill=BACKGROUND, outline="#404040", width=2)
    draw.rectangle(zoom_box, fill=BACKGROUND, outline="#404040", width=2)
    sheet_bounds = bounds_of_primitives(extractor.all_primitives)
    local_bounds = _focus_bounds(dim, edges) or sheet_bounds
    native_dir = output.parent / "native_renders"
    native_dir.mkdir(exist_ok=True)
    full_native = native_dir / f"{output.stem}_full.png"
    local_native = native_dir / f"{output.stem}_detail.png"
    _render_native_dxf(dxf_path, dim, association, edges, sheet_bounds, sheet_bounds, full_native, (1800, 1565))
    _render_native_dxf(dxf_path, dim, association, edges, sheet_bounds, local_bounds, local_native, (1270, 1565))
    with Image.open(full_native) as full_source, Image.open(local_native) as local_source:
        full_image = full_source.convert("RGB")
        local_image = local_source.convert("RGB")
    full_image.thumbnail((full_box[2]-full_box[0], full_box[3]-full_box[1]), Image.Resampling.LANCZOS)
    local_image.thumbnail((zoom_box[2]-zoom_box[0], zoom_box[3]-zoom_box[1]), Image.Resampling.LANCZOS)
    image.paste(full_image, (full_box[0]+(full_box[2]-full_box[0]-full_image.width)//2, full_box[1]+(full_box[3]-full_box[1]-full_image.height)//2))
    image.paste(local_image, (zoom_box[0]+(zoom_box[2]-zoom_box[0]-local_image.width)//2, zoom_box[1]+(zoom_box[3]-zoom_box[1]-local_image.height)//2))
    draw.rectangle((55, 1680, 77, 1702), fill=MAGENTA)
    draw.text((88, 1677), "target product profile", font=_font(18), fill=WHITE)
    draw.rectangle((390, 1680, 412, 1702), fill=CYAN)
    draw.text((423, 1677), "recovered measured span", font=_font(18), fill=WHITE)
    image.save(output)


def _contact_sheet(image_paths, output):
    cell_width, cell_height, columns = 600, 320, 2
    rows = max(1, math.ceil(len(image_paths) / columns))
    sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), BACKGROUND)
    for index, path in enumerate(image_paths):
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((cell_width, cell_height), Image.Resampling.LANCZOS)
        x = (index % columns) * cell_width + (cell_width-image.width)//2
        y = (index // columns) * cell_height + (cell_height-image.height)//2
        sheet.paste(image, (x, y))
    sheet.save(output)


def run(config_path: Path, output_dir: Path, component_names=None):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    dxf_dir = Path(config["dxf_dir"]).resolve()
    expected = dict(config.get("expected_dxf_by_component") or {})
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for component_name, drawing_name in expected.items():
        if component_names and component_name not in component_names:
            continue
        dxf_path = dxf_dir / drawing_name
        extractor = DxfProjectionViewExtractorV2(str(dxf_path))
        analyzer = DxfStructure2DAnalyzer(extractor.document.modelspace())
        _source, payloads = _extract_dxf_worker(str(dxf_path))
        dimensions = [ExtractedDimension(**item) for item in payloads]
        dimensions, suppressed = HistoricalDataIngestor._deduplicate_dimensions(dimensions)
        dimensions = [item for item in dimensions if HistoricalDataIngestor._tolerance_is_plausible(item)]
        component_dir = output_dir / _safe(component_name)
        component_dir.mkdir(exist_ok=True)
        items, image_paths = [], []
        for index, dim in enumerate(dimensions, 1):
            association = analyzer.analyze_dimension(dim)
            recovered_edges = _recovered_endpoint_edges(extractor, dim, association)
            edges = recovered_edges or list(association.get("dimension_target_edges") or [])
            image_path = component_dir / f"{index:03d}_{_safe(dim.entity_handle)}_target_edge.png"
            native_dir = component_dir / "native_renders"
            full_native = native_dir / f"{image_path.stem}_full.png"
            detail_native = native_dir / f"{image_path.stem}_detail.png"
            if not (image_path.is_file() and full_native.is_file() and detail_native.is_file()):
                _render(dxf_path, extractor, dim, association, edges, image_path)
            image_paths.append(image_path)
            items.append({
                "index": index,
                "entity_handle": dim.entity_handle,
                "dimension_category": dim.dimension_category,
                "nominal_value": dim.nominal_value,
                "raw_text": dim.raw_text,
                "tolerance_config": dim.tolerance_config,
                "association_status": association.get("association_status"),
                "association_confidence": association.get("association_confidence"),
                "target_edge_source": (
                    "RECOVERED_DIAMETER_ENDPOINT_PROFILES"
                    if recovered_edges else "DIMENSION_DEFINITION_POINT_TRACE"
                ),
                "target_edges": edges,
                "review_image": str(image_path.relative_to(output_dir)),
            })
        contact_path = component_dir / "00_tolerance_edge_contact_sheet.png"
        _contact_sheet(image_paths, contact_path)
        rows.append({
            "component_name": component_name,
            "drawing": drawing_name,
            "plausible_dimension_count": len(dimensions),
            "suppressed_duplicate_count": int(suppressed),
            "dimensions_with_target_edge": sum(bool(item["target_edges"]) for item in items),
            "dimensions_without_target_edge": sum(not item["target_edges"] for item in items),
            "contact_sheet": str(contact_path.relative_to(output_dir)),
            "items": items,
        })
    report = {
        "schema_version": 1,
        "method": "LEGACY_TOLERANCE_EXTRACTION_WITH_NATIVE_DXF_TARGET_EDGE_REVIEW_V2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "web_ui_updated": False,
        "rendering": {
            "base_image": "native ezdxf rendering with DIMENSION/TEXT/MTEXT preserved",
            "magenta": "edge inferred from the dimension definition points",
            "cyan": "dimension text/anchor",
            "yellow": "edge endpoints",
        },
        "components": rows,
    }
    (output_dir / "tolerance_edge_review_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "# Tolerance edge review\n\n"
        "Each image uses the native DXF renderer so DIMENSION, TEXT, MTEXT, arrows and "
        "extension lines remain visible. The left panel is the complete drawing and the "
        "right panel is a native-rendered local detail. Magenta is the measured edge "
        "traced from the extracted DIMENSION definition points; cyan is the annotation "
        "anchor. No web or database update is performed.\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--components", nargs="*")
    args = parser.parse_args()
    report = run(args.config.resolve(), args.output_dir.resolve(), set(args.components or []))
    print(json.dumps({
        "method": report["method"],
        "components": [
            {key: row[key] for key in (
                "component_name", "drawing", "plausible_dimension_count",
                "dimensions_with_target_edge", "dimensions_without_target_edge",
            )}
            for row in report["components"]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
