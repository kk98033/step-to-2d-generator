"""Run the experimental filename-blind projection pairing V2 audit."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.global_geometry_search import (
    GeometryDescriptor,
    GlobalGeometryPairSearcher,
)
from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
from auto_2d_drawing.tolerance.projection_registration_v2 import (
    AdaptiveProjectionCandidateGenerator,
    SimilarityTransform2D,
)
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2, DxfViewCandidate


BACKGROUND = "#171717"
PANEL = "#262626"
BLUE = "#60a5fa"
ORANGE = "#f97316"
GREEN = "#22c55e"
RED = "#ef4444"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"


def font(size: int):
    for path in (Path("C:/Windows/Fonts/consola.ttf"), Path("C:/Windows/Fonts/arial.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def axis_group(view: str) -> str:
    return {
        "front": "z", "back": "z", "top": "y", "bottom": "y",
        "left": "x", "right": "x",
    }.get(view, view)


def spatially_distinct(left, right) -> bool:
    a, b = left.bbox, right.bbox
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = ix * iy
    area_a = max(a[2] - a[0], 0.0) * max(a[3] - a[1], 0.0)
    area_b = max(b[2] - b[0], 0.0) * max(b[3] - b[1], 0.0)
    union = max(area_a + area_b - intersection, 1e-9)
    iou = intersection / union
    center_a = ((a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0)
    center_b = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
    diagonal = min(math.hypot(a[2] - a[0], a[3] - a[1]), math.hypot(b[2] - b[0], b[3] - b[1]))
    return iou < 0.25 and math.dist(center_a, center_b) > max(0.5, diagonal * 0.25)


def bbox_intersects(left, right, margin: float = 0.0) -> bool:
    return not (
        left[2] + margin < right[0]
        or right[2] + margin < left[0]
        or left[3] + margin < right[1]
        or right[3] + margin < left[1]
    )


def classify_hit(match, extractor) -> str:
    candidate = match["candidate"]
    margin = max(candidate.width, candidate.height) * 0.08 + 0.5
    if any(bbox_intersects(candidate.bbox, hatch, margin) for hatch in extractor.hatch_bboxes):
        return "SECTION_OR_CUTAWAY"
    source = float(match["registration"].get("source_coverage", 0.0) or 0.0)
    target = float(match["registration"].get("target_coverage", 0.0) or 0.0)
    if min(source, target) >= 0.78:
        return "FULL_ORTHOGRAPHIC"
    if max(source, target) >= 0.72 and min(source, target) >= 0.45:
        return "PARTIAL_VIEW"
    return "LOCAL_PROFILE"


def transform_from_registration(registration: dict) -> SimilarityTransform2D:
    payload = registration["transform"]
    return SimilarityTransform2D(
        scale=float(payload["scale"]),
        rotation_degrees=float(payload["rotation_degrees"]),
        translation=(float(payload["translation"][0]), float(payload["translation"][1])),
        mirrored=bool(payload.get("mirrored", False)),
    )


def adjusted_score(registration: dict, candidate) -> dict:
    raw = float(registration.get("score", 0.0) or 0.0)
    scale = max(float(registration.get("scale", 0.0) or 0.0), 1e-9)
    scale_quality = math.exp(-abs(math.log(scale)) / 0.35)
    complexity = min(1.0, math.log1p(candidate.primitive_count) / math.log(25.0))
    curvature = min(1.0, candidate.curved_primitive_count / 3.0)
    structure = 0.7 * complexity + 0.3 * curvature
    adjusted = raw * (0.65 + 0.20 * scale_quality + 0.15 * structure)
    return {
        "raw_score": round(raw, 6),
        "adjusted_score": round(adjusted, 6),
        "scale_quality": round(scale_quality, 6),
        "structure_quality": round(structure, 6),
    }


def render_shape(shape, png_path: Path, stl_path: Path):
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.StlAPI import StlAPI_Writer
    import vtk

    BRepMesh_IncrementalMesh(shape, 0.12, False, 0.30, True).Perform()
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
    image = Image.new("RGB", (900, 650), PANEL)
    draw = ImageDraw.Draw(image)
    if not points:
        image.save(png_path)
        return
    azimuth, elevation = math.radians(35), math.radians(28)
    ca, sa = math.cos(azimuth), math.sin(azimuth)
    ce, se = math.cos(elevation), math.sin(elevation)
    center = tuple(sum(point[axis] for point in points) / len(points) for axis in range(3))
    transformed = []
    for point in points:
        x, y, z = (point[index] - center[index] for index in range(3))
        x1, y1 = ca * x - sa * y, sa * x + ca * y
        transformed.append((x1, ce * y1 - se * z, se * y1 + ce * z))
    xs, ys = [point[0] for point in transformed], [point[1] for point in transformed]
    scale = min(780 / max(max(xs) - min(xs), 1e-9), 530 / max(max(ys) - min(ys), 1e-9))
    screen = [(450 + point[0] * scale, 340 - point[1] * scale) for point in transformed]
    order = sorted(range(len(triangles)), key=lambda i: sum(transformed[j][2] for j in triangles[i]) / 3.0)
    stride = max(1, len(order) // 50000)
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
        color = tuple(int(a * light + b * (1.0 - light)) for a, b in zip((75, 105, 135), (160, 165, 170)))
        draw.polygon([screen[index] for index in ids], fill=color, outline=(68, 78, 88))
    image.save(png_path)


def bounds_of_primitives(primitives):
    boxes = [primitive.bbox for primitive in primitives]
    return (
        min(box[0] for box in boxes), min(box[1] for box in boxes),
        max(box[2] for box in boxes), max(box[3] for box in boxes),
    )


def make_mapper(bounds, box):
    xmin, ymin, xmax, ymax = bounds
    left, top, right, bottom = box
    scale = min((right - left) / max(xmax - xmin, 1e-9), (bottom - top) / max(ymax - ymin, 1e-9)) * 0.94
    ox = left + ((right - left) - (xmax - xmin) * scale) / 2.0
    oy = top + ((bottom - top) - (ymax - ymin) * scale) / 2.0

    def mapper(point):
        return ox + (point[0] - xmin) * scale, bottom - (oy - top) - (point[1] - ymin) * scale

    return mapper


def render_full_dxf(extractor, matched_candidate, path: Path):
    image = Image.new("RGB", (1000, 700), PANEL)
    draw = ImageDraw.Draw(image)
    mapper = make_mapper(bounds_of_primitives(extractor.all_primitives), (35, 35, 965, 665))
    matched = set(matched_candidate.primitive_indexes)
    geometry_lookup = {id(item): index for index, item in enumerate(extractor.geometry_primitives)}
    for primitive in extractor.all_primitives:
        points = [mapper(point) for point in primitive.points]
        if len(points) < 2:
            continue
        geometry_index = geometry_lookup.get(id(primitive))
        color = BLUE if geometry_index in matched else "#737373"
        width = 3 if geometry_index in matched else 1
        draw.line(points, fill=color, width=width)
    box = matched_candidate.bbox
    p1, p2 = mapper((box[0], box[1])), mapper((box[2], box[3]))
    draw.rectangle((min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]), max(p1[1], p2[1])), outline=ORANGE, width=4)
    image.save(path)


def render_overlay(candidate, transformed_step, registration, path: Path):
    image = Image.new("RGB", (1000, 700), PANEL)
    draw = ImageDraw.Draw(image)
    all_points = candidate.points + transformed_step
    xs = [point[0] for point in all_points]
    ys = [point[1] for point in all_points]
    mapper = make_mapper((min(xs), min(ys), max(xs), max(ys)), (35, 35, 965, 665))
    for point in candidate.points:
        x, y = mapper(point)
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=BLUE)
    for point in transformed_step:
        x, y = mapper(point)
        draw.ellipse((x - 1.5, y - 1.5, x + 1.5, y + 1.5), fill=ORANGE)
    image.save(path)


def create_review_board(component_name, verdict, result, model_png, dxf_png, overlay_png, output):
    board = Image.new("RGB", (3000, 980), BACKGROUND)
    draw = ImageDraw.Draw(board)
    status = str(verdict).upper()
    status_color = {
        "CONFIRMED": GREEN,
        "REVIEW": ORANGE,
        "INCORRECT": RED,
    }.get(status, WHITE)
    draw.text((40, 24), f"{component_name} | projection hit: {status}", font=font(30), fill=status_color)
    draw.text(
        (40, 67),
        f"DXF={Path(result['dxf_path']).name}  STEP={result['step_view']}  DXF view={result['candidate'].view_id}  "
        f"raw={result['metrics']['raw_score']:.4f}  adjusted={result['file_score']:.4f}  scale={result['registration']['scale']:.4f}",
        font=font(19), fill=WHITE,
    )
    labels = ("Original STEP component", "Original DXF geometry + selected region", "Selected region overlay")
    images = (Image.open(model_png), Image.open(dxf_png), Image.open(overlay_png))
    for index, (label, image) in enumerate(zip(labels, images)):
        x = index * 1000
        draw.text((x + 28, 112), label, font=font(21), fill=WHITE)
        fitted = image.copy()
        fitted.thumbnail((960, 790))
        board.paste(fitted, (x + (1000 - fitted.width) // 2, 155 + (790 - fitted.height) // 2))
    board.save(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).resolve().read_text(encoding="utf-8"))
    output_dir = Path(config["output_dir_v2"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    dxf_dir = Path(config["dxf_dir"]).resolve()
    prefixes = tuple(config.get("dxf_prefixes") or ())
    dxf_paths = [
        path.resolve() for path in sorted(dxf_dir.glob("*.dxf"))
        if not prefixes or path.stem.startswith(prefixes)
    ]
    expected = dict(config["expected_dxf_by_component"])
    known_occurrences = {
        key: set(values)
        for key, values in dict(config.get("known_occurrence_dxfs_by_component") or {}).items()
    }
    components = [
        component
        for step_path in config["step_paths"]
        for component in extract_leaf_components(str(Path(step_path).resolve()))
        if component.name in expected
    ]

    helper = GlobalGeometryPairSearcher(maximum_record_points=256)
    extractors = {}
    candidates = []
    legacy_candidate_indexes = []
    for path in dxf_paths:
        extractor = DxfProjectionViewExtractorV2(str(path))
        extractors[str(path)] = extractor
        candidates.extend(extractor.view_candidates)
        analyzer = DxfStructure2DAnalyzer(extractor.document.modelspace())
        for cluster in analyzer.view_clusters:
            points = helper._reduce_points(helper._dxf_cluster_points(analyzer, cluster))
            if len(points) < 12:
                continue
            items = [analyzer.primitives[index] for index in cluster.primitive_indexes]
            candidate = DxfViewCandidate(
                view_id=f"legacy_{cluster.view_id}",
                source_path=str(path),
                source="legacy_insert_or_path_cluster",
                primitive_indexes=[],
                points=points,
                bbox=cluster.bbox,
                primitive_count=len(items),
                curved_primitive_count=sum(item.entity_type in {"ARC", "CIRCLE"} for item in items),
                layer_count=len({item.layer for item in items}),
            )
            if extractor.is_reserved_table_candidate(candidate.bbox):
                continue
            legacy_candidate_indexes.append(len(candidates))
            candidates.append(candidate)

    candidate_descriptors = np.asarray([GeometryDescriptor.build(item.points) for item in candidates], dtype=float)
    registration = GlobalGeometryPairSearcher().registration
    component_results = []
    review_paths = []
    for component in components:
        projections = AdaptiveProjectionCandidateGenerator(max_views=6).project_all(component.shape)
        matches = []
        for step_view, projection in projections.items():
            step_points = helper._reduce_points(helper._step_view_points(projection))
            descriptor = np.asarray(GeometryDescriptor.build(step_points), dtype=float)
            distances = np.linalg.norm(candidate_descriptors - descriptor[None, :], axis=1)
            shortlist_count = min(int(config.get("v2_shortlist_per_projection", 120)), len(candidates))
            shortlist = set(int(index) for index in np.argpartition(distances, shortlist_count - 1)[:shortlist_count])
            shortlist.update(legacy_candidate_indexes)
            for candidate_index in shortlist:
                candidate = candidates[int(candidate_index)]
                result = registration.register(candidate.points, step_points)
                metrics = adjusted_score(result, candidate)
                matches.append({
                    "component_name": component.name,
                    "dxf_path": candidate.source_path,
                    "step_view": step_view,
                    "step_points": step_points,
                    "candidate": candidate,
                    "registration": result,
                    "metrics": metrics,
                    "descriptor_distance": float(distances[int(candidate_index)]),
                })

        by_file = defaultdict(list)
        for match in matches:
            by_file[match["dxf_path"]].append(match)
        ranked_files = []
        for dxf_path, rows in by_file.items():
            rows.sort(key=lambda item: item["metrics"]["adjusted_score"], reverse=True)
            best = rows[0]
            second = next((
                row for row in rows[1:]
                if row["candidate"].view_id != best["candidate"].view_id
                and spatially_distinct(row["candidate"], best["candidate"])
                and axis_group(row["step_view"]) != axis_group(best["step_view"])
                and abs(math.log(max(row["registration"].get("scale", 1e-9), 1e-9) / max(best["registration"].get("scale", 1e-9), 1e-9))) <= 0.22
                and row["metrics"]["raw_score"] >= 0.55
            ), None)
            bonus = 0.06 * second["metrics"]["adjusted_score"] if second else 0.0
            best["file_score"] = round(best["metrics"]["adjusted_score"] + bonus, 6)
            best["multi_view_support"] = bool(second)
            ranked_files.append(best)
        ranked_files.sort(key=lambda item: item["file_score"], reverse=True)
        top = ranked_files[0]
        expected_name = expected[component.name]
        rank = next((index for index, row in enumerate(ranked_files, 1) if Path(row["dxf_path"]).name == expected_name), None)
        correct = Path(top["dxf_path"]).name == expected_name
        scale_min, scale_max = [float(value) for value in config.get("multi_hit_scale_range", [0.65, 1.55])]
        accepted_hits = [
            row for row in ranked_files
            if row["metrics"]["adjusted_score"] >= float(config.get("multi_hit_min_adjusted_score", 0.68))
            and row["metrics"]["raw_score"] >= float(config.get("multi_hit_min_raw_score", 0.72))
            and scale_min <= float(row["registration"].get("scale", 0.0) or 0.0) <= scale_max
            and float(row["registration"].get("source_coverage", 0.0) or 0.0) >= 0.55
            and float(row["registration"].get("target_coverage", 0.0) or 0.0) >= 0.55
        ][:int(config.get("multi_hit_max_per_component", 5))]
        if not accepted_hits:
            accepted_hits = [top]
        for hit in accepted_hits:
            extractor = extractors[str(Path(hit["dxf_path"]).resolve())]
            hit["hit_type"] = classify_hit(hit, extractor)

        component_dir = output_dir / component.name
        component_dir.mkdir(parents=True, exist_ok=True)
        model_png = component_dir / "01_original_step_component.png"
        model_stl = component_dir / "01_original_step_component.stl"
        dxf_png = component_dir / "02_original_dxf_and_hit_region.png"
        overlay_png = component_dir / "03_projection_overlay.png"
        board_png = component_dir / "00_review_board.png"
        render_shape(component.shape, model_png, model_stl)
        extractor = extractors[str(Path(top["dxf_path"]).resolve())]
        render_full_dxf(extractor, top["candidate"], dxf_png)
        transformed = transform_from_registration(top["registration"]).apply(top["step_points"])
        render_overlay(top["candidate"], transformed, top["registration"], overlay_png)
        top_verdict = "CONFIRMED" if correct else "REVIEW"
        create_review_board(component.name, top_verdict, top, model_png, dxf_png, overlay_png, board_png)
        review_paths.append(board_png)

        multi_hit_boards = []
        multi_hit_root = component_dir / "multi_hits"
        multi_hit_root.mkdir(exist_ok=True)
        occurrence_names = known_occurrences.get(component.name, set())
        for hit_index, hit in enumerate(accepted_hits, 1):
            hit_dir = multi_hit_root / f"hit_{hit_index:02d}_{Path(hit['dxf_path']).stem}"
            hit_dir.mkdir(exist_ok=True)
            hit_dxf_png = hit_dir / "02_original_dxf_and_hit_region.png"
            hit_overlay_png = hit_dir / "03_projection_overlay.png"
            hit_board_png = hit_dir / "00_review_board.png"
            extractor = extractors[str(Path(hit["dxf_path"]).resolve())]
            render_full_dxf(extractor, hit["candidate"], hit_dxf_png)
            transformed = transform_from_registration(hit["registration"]).apply(hit["step_points"])
            render_overlay(hit["candidate"], transformed, hit["registration"], hit_overlay_png)
            known_hit = Path(hit["dxf_path"]).name in occurrence_names
            verdict = "CONFIRMED" if known_hit else "REVIEW"
            create_review_board(
                f"{component.name} | HIT {hit_index:02d} | {hit['hit_type']}",
                verdict,
                hit,
                model_png,
                hit_dxf_png,
                hit_overlay_png,
                hit_board_png,
            )
            multi_hit_boards.append(hit_board_png)
        multi_contact = Image.new("RGB", (1500, 490 * len(multi_hit_boards)), BACKGROUND)
        for hit_index, hit_board in enumerate(multi_hit_boards):
            image = Image.open(hit_board)
            image.thumbnail((1500, 480))
            multi_contact.paste(image, (0, hit_index * 490))
        multi_contact_path = component_dir / "00_multi_hit_review.png"
        multi_contact.save(multi_contact_path)

        component_results.append({
            "component_name": component.name,
            "expected_dxf": expected_name,
            "top1_dxf": Path(top["dxf_path"]).name,
            "top1_correct": correct,
            "expected_rank": rank,
            "top1_file_score": top["file_score"],
            "raw_registration_score": top["metrics"]["raw_score"],
            "scale": top["registration"].get("scale"),
            "step_view": top["step_view"],
            "dxf_view": top["candidate"].view_id,
            "candidate_source": top["candidate"].source,
            "candidate_primitive_count": top["candidate"].primitive_count,
            "candidate_curved_primitive_count": top["candidate"].curved_primitive_count,
            "multi_view_support": top["multi_view_support"],
            "known_occurrence_dxfs": sorted(occurrence_names),
            "detected_hits": [
                {
                    "rank": hit_index,
                    "dxf": Path(hit["dxf_path"]).name,
                    "known_occurrence": Path(hit["dxf_path"]).name in occurrence_names,
                    "review_verdict": (
                        "CONFIRMED"
                        if Path(hit["dxf_path"]).name in occurrence_names
                        else "REVIEW"
                    ),
                    "hit_type": hit["hit_type"],
                    "score": hit["file_score"],
                    "raw_score": hit["metrics"]["raw_score"],
                    "scale": hit["registration"].get("scale"),
                    "source_coverage": hit["registration"].get("source_coverage"),
                    "target_coverage": hit["registration"].get("target_coverage"),
                    "step_view": hit["step_view"],
                    "dxf_view": hit["candidate"].view_id,
                    "review_board": str(multi_hit_boards[hit_index - 1].relative_to(output_dir)),
                }
                for hit_index, hit in enumerate(accepted_hits, 1)
            ],
            "known_occurrence_hits": sorted({
                Path(hit["dxf_path"]).name for hit in accepted_hits
            } & occurrence_names),
            "known_occurrence_misses": sorted(occurrence_names - {
                Path(hit["dxf_path"]).name for hit in accepted_hits
            }),
            "multi_hit_review": str(multi_contact_path.relative_to(output_dir)),
            "top5": [
                {
                    "rank": index,
                    "dxf": Path(row["dxf_path"]).name,
                    "score": row["file_score"],
                    "raw_score": row["metrics"]["raw_score"],
                    "scale": row["registration"].get("scale"),
                    "step_view": row["step_view"],
                    "dxf_view": row["candidate"].view_id,
                }
                for index, row in enumerate(ranked_files[:5], 1)
            ],
            "review_board": str(board_png.relative_to(output_dir)),
        })

    top1 = sum(item["top1_correct"] for item in component_results)
    top3 = sum((item["expected_rank"] or 9999) <= 3 for item in component_results)
    known_total = sum(len(item["known_occurrence_dxfs"]) for item in component_results)
    known_hits = sum(len(item["known_occurrence_hits"]) for item in component_results)
    components_with_known_hit = sum(bool(item["known_occurrence_hits"]) for item in component_results)
    report = {
        "method": "DXF_CURVES_MULTISCALE_PROJECTION_PAIRING_V2",
        "filename_used_in_scoring": False,
        "candidate_dxf_count": len(dxf_paths),
        "component_count": len(component_results),
        "view_candidate_count": len(candidates),
        "top1_correct": top1,
        "top1_accuracy": round(top1 / max(len(component_results), 1), 6),
        "top3_correct": top3,
        "top3_accuracy": round(top3 / max(len(component_results), 1), 6),
        "occurrence_evaluation": {
            "interpretation": "Known occurrences are a partial SILVER set; unlisted hits require engineer review and are not automatically false.",
            "components_with_at_least_one_known_hit": components_with_known_hit,
            "component_hit_rate": round(components_with_known_hit / max(len(component_results), 1), 6),
            "known_occurrence_count": known_total,
            "known_occurrence_hits": known_hits,
            "known_occurrence_recall": round(known_hits / max(known_total, 1), 6),
        },
        "components": component_results,
    }
    (output_dir / "projection_pairing_v2_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    contact = Image.new("RGB", (1500, 490 * len(review_paths)), BACKGROUND)
    for index, path in enumerate(review_paths):
        image = Image.open(path)
        image.thumbnail((1500, 480))
        contact.paste(image, (0, index * 490))
    contact.save(output_dir / "00_all_model_hit_review.png")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
