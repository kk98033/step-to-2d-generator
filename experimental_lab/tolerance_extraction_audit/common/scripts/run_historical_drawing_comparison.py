"""Compare STEP projections and experiment-09 layouts with historical DXF views."""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

import ezdxf
import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing import layout as drawing_layout
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.math import BoundingBox2d
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.tolerance.global_geometry_search import (  # noqa: E402
    GeometryDescriptor, GlobalGeometryPairSearcher,
)
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2  # noqa: E402
from historical_view_comparison import expanded_crop_bounds, select_distinct_view_matches  # noqa: E402
from run_classification_tree_audit import (  # noqa: E402
    CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, WHITE, _edge_points, _font, _mapper,
)
from run_projection_pairing_v2 import adjusted_score, render_shape  # noqa: E402
from run_view_plan_experiment import _standard_view_data  # noqa: E402


METHOD = "HISTORICAL_DRAWING_VIEW_COMPARISON_V2"
BACKGROUND = "#111111"
MODEL = "#d4d4d4"
HIDDEN = "#525252"


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _render_native_region(dxf_path: Path, bounds: Sequence[float], output: Path, size: tuple[int, int]) -> None:
    """Rasterize original DXF entities, including dimensions and text."""
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    context = RenderContext(doc)
    context.current_layout_properties.set_colors(BACKGROUND)
    backend = SVGBackend()
    Frontend(context, backend).draw_layout(msp, finalize=True)
    xmin, ymin, xmax, ymax = (float(value) for value in bounds)
    width, height = max(xmax - xmin, 1e-9), max(ymax - ymin, 1e-9)
    render_box = BoundingBox2d([
        (xmin - width * 0.025, ymin - height * 0.025),
        (xmax + width * 0.025, ymax + height * 0.025),
    ])
    svg_text = backend.get_string(drawing_layout.Page.from_dxf_layout(msp), render_box=render_box)
    with tempfile.TemporaryDirectory(prefix="historical_dxf_review_") as temp_dir:
        svg_path = Path(temp_dir) / "drawing.svg"
        pdf_path = Path(temp_dir) / "drawing.pdf"
        svg_path.write_text(svg_text, encoding="utf-8")
        drawing = svg2rlg(str(svg_path))
        if drawing is None:
            raise RuntimeError(f"SVG conversion failed for {dxf_path}")
        renderPDF.drawToFile(drawing, str(pdf_path))
        pdf = pdfium.PdfDocument(str(pdf_path))
        page = pdf[0]
        pdf_width, pdf_height = page.get_size()
        scale = max(size[0] / max(pdf_width, 1.0), size[1] / max(pdf_height, 1.0))
        bitmap = page.render(scale=scale)
        rendered = bitmap.to_pil().convert("RGB").copy()
        bitmap.close()
        page.close()
        pdf.close()
    rendered.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, BACKGROUND)
    canvas.paste(rendered, ((size[0] - rendered.width) // 2, (size[1] - rendered.height) // 2))
    canvas.save(output)


def _render_step_projection(view: dict[str, Any], output: Path) -> None:
    canvas = Image.new("RGB", (1350, 1250), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    mapper = _mapper(view["bbox"], (55, 55, 1295, 1195))
    for edge in view.get("hidden") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill=HIDDEN, width=1)
    for edge in view.get("visible") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill=MODEL, width=3)
    canvas.save(output)


def _comparison_score(registration: dict[str, Any], candidate: Any) -> float:
    adjusted = adjusted_score(registration, candidate)
    raw = float(adjusted["raw_score"])
    source = float(registration.get("source_coverage", 0.0) or 0.0)
    target = float(registration.get("target_coverage", 0.0) or 0.0)
    coverage = math.sqrt(max(source * target, 0.0))
    return round(0.72 * float(adjusted["adjusted_score"]) + 0.18 * raw + 0.10 * coverage, 6)


def _point_aspect(points: Sequence[Sequence[float]]) -> float:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    return min(width, height) / max(width, height, 1e-9)


def _view_groups(descriptors: dict[str, np.ndarray], aspects: dict[str, float]) -> dict[str, str]:
    groups: dict[str, str] = {}
    representatives: list[str] = []
    for view_name, descriptor in descriptors.items():
        equivalent = next((
            other for other in representatives
            if float(np.linalg.norm(descriptor - descriptors[other])) <= 0.20
            and abs(aspects[view_name] - aspects[other]) <= 0.04
        ), None)
        if equivalent is None:
            representatives.append(view_name)
            groups[view_name] = view_name
        else:
            groups[view_name] = groups[equivalent]
    return groups


def _match_views(
    extractor: DxfProjectionViewExtractorV2,
    views: dict[str, dict[str, Any]],
    view_names: Sequence[str],
    maximum_views: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = extractor.view_candidates
    if not candidates:
        return [], []
    helper = GlobalGeometryPairSearcher(maximum_record_points=256)
    descriptors = np.asarray([GeometryDescriptor.build(item.points) for item in candidates], dtype=float)
    step_points_by_view = {
        view_name: helper._reduce_points(helper._step_view_points(views[view_name]))
        for view_name in view_names
    }
    step_descriptors = {
        view_name: np.asarray(GeometryDescriptor.build(points), dtype=float)
        for view_name, points in step_points_by_view.items()
    }
    step_aspects = {view_name: _point_aspect(points) for view_name, points in step_points_by_view.items()}
    step_groups = _view_groups(step_descriptors, step_aspects)
    sheet_width = max(float(extractor.sheet_bbox[2]) - float(extractor.sheet_bbox[0]), 1e-9)
    sheet_height = max(float(extractor.sheet_bbox[3]) - float(extractor.sheet_bbox[1]), 1e-9)
    sheet_area = sheet_width * sheet_height
    rows: list[dict[str, Any]] = []
    for view_name in view_names:
        step_points = step_points_by_view[view_name]
        descriptor = step_descriptors[view_name]
        distances = np.linalg.norm(descriptors - descriptor[None, :], axis=1)
        shortlist_count = min(48, len(candidates))
        indexes = np.argpartition(distances, shortlist_count - 1)[:shortlist_count]
        for candidate_index in indexes:
            candidate = candidates[int(candidate_index)]
            registration = helper.registration.register(candidate.points, step_points)
            if not registration.get("transform"):
                continue
            candidate_aspect = _point_aspect(candidate.points)
            aspect_similarity = min(candidate_aspect, step_aspects[view_name]) / max(candidate_aspect, step_aspects[view_name], 1e-9)
            candidate_area = max(candidate.width, 0.0) * max(candidate.height, 0.0)
            base_score = _comparison_score(registration, candidate)
            rows.append({
                "step_view": view_name,
                "step_view_group": step_groups[view_name],
                "candidate_index": int(candidate_index),
                "candidate_id": candidate.view_id,
                "candidate_bbox": [float(value) for value in candidate.bbox],
                "candidate_source": candidate.source,
                "primitive_count": int(candidate.primitive_count),
                "curved_primitive_count": int(candidate.curved_primitive_count),
                "comparison_score": round(base_score * (0.55 + 0.45 * aspect_similarity), 6),
                "raw_score": float(registration.get("score", 0.0) or 0.0),
                "source_coverage": float(registration.get("source_coverage", 0.0) or 0.0),
                "target_coverage": float(registration.get("target_coverage", 0.0) or 0.0),
                "scale": float(registration.get("scale", 0.0) or 0.0),
                "rotation_degrees": float(registration["transform"].get("rotation_degrees", 0.0)),
                "mirrored": bool(registration["transform"].get("mirrored", False)),
                "descriptor_distance": float(distances[int(candidate_index)]),
                "step_aspect": step_aspects[view_name],
                "candidate_aspect": candidate_aspect,
                "aspect_similarity": aspect_similarity,
                "sheet_area_fraction": candidate_area / sheet_area,
            })
    return select_distinct_view_matches(rows, maximum_views=maximum_views), rows


def _fit_image(canvas: Image.Image, path: Path, box: Sequence[int]) -> None:
    with Image.open(path) as source:
        image = source.convert("RGB")
    width, height = int(box[2] - box[0]), int(box[3] - box[1])
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas.paste(image, (int(box[0] + (width - image.width) / 2), int(box[1] + (height - image.height) / 2)))


def _render_comparison_board(
    output: Path,
    case_name: str,
    match: dict[str, Any],
    model_image: Path,
    projection_image: Path,
    historical_crop: Path,
    layout_image: Path | None,
) -> None:
    canvas = Image.new("RGB", (5600, 1850), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((45, 25), f"Historical Drawing Comparison | {case_name} | {match['step_view'].upper()}", font=_font(36, True), fill=WHITE)
    draw.text(
        (45, 78),
        f"vector registration score={match['comparison_score']:.3f} | rotation={match['rotation_degrees']:.1f}° | mirrored={match['mirrored']} | candidate={match['candidate_id']}",
        font=_font(18), fill=GREEN if match["comparison_score"] >= 0.60 else ORANGE,
    )
    labels = ("ORIGINAL STEP PART", "STEP ORTHOGRAPHIC PROJECTION", "ENGINEER DRAWING VIEW CROP", "09 ALL-INTENT LAYOUT")
    images = (model_image, projection_image, historical_crop, layout_image)
    panel_width = 1370
    for index, (label, image_path) in enumerate(zip(labels, images)):
        left = 35 + index * 1390
        box = (left, 145, left + panel_width, 1740)
        draw.rectangle(box, fill=BACKGROUND, outline=GRID, width=2)
        draw.text((left + 18, 160), label, font=_font(19, True), fill=CYAN if index < 2 else WHITE)
        if image_path and image_path.exists():
            _fit_image(canvas, image_path, (left + 22, 215, left + panel_width - 22, 1695))
        else:
            draw.text((left + 25, 250), "No corresponding 09 layout image", font=_font(17), fill=MUTED)
    draw.text((45, 1790), "The historical crop preserves native annotations; it is evidence for visual review, not automatic ground truth.", font=_font(17), fill=MUTED)
    canvas.save(output)


def _render_case_overview(output: Path, case_name: str, full_drawing: Path, boards: Sequence[Path]) -> None:
    height = 1250 + len(boards) * 720
    canvas = Image.new("RGB", (3000, height), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((40, 25), f"{case_name} | original engineer sheet + detected views", font=_font(34, True), fill=WHITE)
    draw.text((40, 78), f"Detected view count: {len(boards)} (not forced to three views)", font=_font(19), fill=CYAN)
    _fit_image(canvas, full_drawing, (40, 125, 2960, 1160))
    for index, board in enumerate(boards):
        with Image.open(board) as source:
            image = source.convert("RGB")
        image.thumbnail((2920, 680), Image.Resampling.LANCZOS)
        canvas.paste(image, (40, 1210 + index * 720))
    canvas.save(output)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for case_index, case in enumerate(config.get("cases") or [], start=2):
        name = str(case["name"])
        step_path = _resolve_path(str(case["step_path"]))
        dxf_path = _resolve_path(str(case["historical_dxf"]))
        case_dir = output_dir / f"{case_index:02d}_{name}_historical_comparison"
        case_dir.mkdir(parents=True, exist_ok=True)

        shape = load_step(str(step_path))
        views = _standard_view_data(ViewProjector(), shape)
        extractor = DxfProjectionViewExtractorV2(str(dxf_path))
        matches, all_rows = _match_views(
            extractor, views, tuple(case.get("view_names") or ("front", "top", "right")),
            int(case.get("maximum_views") or 3),
        )

        model_image = case_dir / "01_original_step_part.png"
        render_shape(shape, model_image, case_dir / "01_original_step_part.stl")
        full_drawing = case_dir / "02_original_engineer_drawing.png"
        _render_native_region(dxf_path, extractor.sheet_bbox, full_drawing, (2600, 1700))
        layout_dir = _resolve_path(str(case["layout_directory"]))
        boards = []
        public_matches = []
        for match_index, match in enumerate(matches, start=1):
            view_name = str(match["step_view"])
            projection_image = case_dir / f"03_{match_index:02d}_{view_name}_step_projection.png"
            historical_crop = case_dir / f"04_{match_index:02d}_{view_name}_engineer_view_crop.png"
            board = case_dir / f"05_{match_index:02d}_{view_name}_comparison.png"
            _render_step_projection(views[view_name], projection_image)
            crop_bounds = expanded_crop_bounds(match["candidate_bbox"], extractor.sheet_bbox, float(case.get("crop_margin_fraction") or 0.22))
            _render_native_region(dxf_path, crop_bounds, historical_crop, (1600, 1400))
            layout_images = sorted(layout_dir.glob(f"*_{view_name}_full_layout.png"))
            layout_image = layout_images[0] if layout_images else None
            _render_comparison_board(board, name, match, model_image, projection_image, historical_crop, layout_image)
            boards.append(board)
            public_matches.append({**match, "crop_bounds": crop_bounds, "comparison_image": board.relative_to(output_dir).as_posix()})

        overview = case_dir / "00_original_sheet_and_detected_views.png"
        _render_case_overview(overview, name, full_drawing, boards)
        report = {
            "name": name,
            "step_path": str(step_path),
            "historical_dxf": str(dxf_path),
            "candidate_count": len(extractor.view_candidates),
            "evaluated_registration_count": len(all_rows),
            "detected_view_count": len(matches),
            "matches": public_matches,
            "original_drawing_image": full_drawing.relative_to(output_dir).as_posix(),
            "overview_image": overview.relative_to(output_dir).as_posix(),
        }
        reports.append(report)
        (case_dir / "comparison_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "comparison_interpretation": "VISUAL_EVIDENCE_NOT_GROUND_TRUTH",
        "cases": reports,
        "metrics": {
            "case_count": len(reports),
            "detected_view_count": sum(item["detected_view_count"] for item in reports),
            "single_view_case_count": sum(item["detected_view_count"] == 1 for item in reports),
        },
    }
    (output_dir / "06_historical_comparison_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.output_dir)
    print(json.dumps(report["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
