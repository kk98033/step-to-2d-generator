"""Visual audit of tolerance extraction and 2D-to-3D feature semantics."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase
from auto_2d_drawing.tolerance.composite_features import (
    CompositeAxialSpanBuilder, composite_descriptor, match_transverse_diameter,
)
from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
from auto_2d_drawing.tolerance.feature_graph import FeatureNode
from auto_2d_drawing.tolerance.ingest_historical_data import (
    ExtractedDimension, HistoricalDataIngestor, _extract_dxf_worker,
)
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2
from generate_tolerance_edge_review import (
    BACKGROUND, PANEL, WHITE, MUTED, _font, _format_tolerance, _focus_bounds,
    _recovered_endpoint_edges, _render_native_dxf, _safe,
)
from run_locked_projection_tolerance_experiment import _verify
from run_projection_pairing_v2 import bounds_of_primitives


GREEN = "#22c55e"
YELLOW = "#facc15"
RED = "#ef4444"
STATUS_RGB = {
    "VERIFIED": ((34, 197, 94), (74, 222, 128)),
    "SEMANTICALLY_RECOVERED": ((34, 197, 94), (74, 222, 128)),
    "COMPOSITE_RECOVERED": ((16, 185, 129), (45, 212, 191)),
    "CANDIDATE_ONLY": ((250, 204, 21), (251, 146, 60)),
    "UNRESOLVED": ((239, 68, 68), (248, 113, 113)),
}


def _overall_length_recovery(dim, graph, locked_registration):
    if str(dim.dimension_category).upper() != "LINEAR":
        return None
    points = dim.points or {}
    first, second = points.get("defpoint2"), points.get("defpoint3")
    if not first or not second:
        return None
    dx, dy = abs(float(second[0])-float(first[0])), abs(float(second[1])-float(first[1]))
    dominant = max(dx, dy)
    bbox = list(locked_registration.get("dxf_bbox") or [])
    for node in graph.nodes:
        if str(node.feature_type) != "overall_dimension":
            continue
        nominal = dict(node.nominal or {})
        length = float(nominal.get("length", 0.0) or 0.0)
        tolerance = max(0.02, length * 0.002)
        if abs(float(dim.nominal_value)-length) > tolerance or abs(dominant-length) > tolerance:
            continue
        if len(bbox) == 4:
            axis = 0 if dx >= dy else 1
            ends = sorted((float(first[axis]), float(second[axis])))
            bounds = sorted((float(bbox[axis]), float(bbox[axis+2])))
            if max(abs(ends[0]-bounds[0]), abs(ends[1]-bounds[1])) > max(0.08, length*0.01):
                continue
        return {
            "feature_id": node.id,
            "feature_type": node.feature_type,
            "feature_role": node.inferred_role,
            "nominal_field": "length",
            "nominal_value": length,
            "method": "PROJECTED_GLOBAL_EXTREMA_MATCH_OVERALL_LENGTH_V1",
            "confidence": 0.98,
            "endpoints": [list(first[:2]), list(second[:2])],
        }
    return None


def _overall_end_planes(dim, candidate_bbox):
    first, second = dim.points["defpoint2"], dim.points["defpoint3"]
    dx = abs(float(second[0])-float(first[0]))
    dy = abs(float(second[1])-float(first[1]))
    xmin, ymin, xmax, ymax = [float(value) for value in candidate_bbox]
    if dx >= dy:
        return [
            {"handle": "OVERALL_MIN_PLANE", "entity_type": "SEMANTIC_PLANE", "relation": "GLOBAL_EXTREMUM", "points": [[float(first[0]), ymin], [float(first[0]), ymax]]},
            {"handle": "OVERALL_MAX_PLANE", "entity_type": "SEMANTIC_PLANE", "relation": "GLOBAL_EXTREMUM", "points": [[float(second[0]), ymin], [float(second[0]), ymax]]},
        ]
    return [
        {"handle": "OVERALL_MIN_PLANE", "entity_type": "SEMANTIC_PLANE", "relation": "GLOBAL_EXTREMUM", "points": [[xmin, float(first[1])], [xmax, float(first[1])]]},
        {"handle": "OVERALL_MAX_PLANE", "entity_type": "SEMANTIC_PLANE", "relation": "GLOBAL_EXTREMUM", "points": [[xmin, float(second[1])], [xmax, float(second[1])]]},
    ]


def _compose_board(dim, result, full_path, detail_path, output):
    width, height = 3200, 1800
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, width-18, height-18), radius=16, fill=PANEL)
    color = {"VERIFIED": GREEN, "SEMANTICALLY_RECOVERED": GREEN, "COMPOSITE_RECOVERED": "#10b981", "CANDIDATE_ONLY": YELLOW, "UNRESOLVED": RED}[result["status"]]
    draw.text((42, 28), f"{_format_tolerance(dim)} | {result['status']}", font=_font(32), fill=color)
    draw.text((42, 72), f"DXF handle={dim.entity_handle}  2D={dim.dimension_category}  extraction=OK", font=_font(19), fill=WHITE)
    draw.text((42, 102), result["feature_label"], font=_font(20), fill=color)
    draw.text((42, 132), result["reason"], font=_font(17), fill=MUTED)
    draw.text((42, 170), "FULL ORIGINAL DXF + SEMANTIC OVERLAY", font=_font(22), fill=WHITE)
    draw.text((1910, 170), "LOCAL DIMENSION / FEATURE DETAIL", font=_font(22), fill=WHITE)
    boxes = ((35, 210, 1850, 1745), (1885, 210, 3165, 1745))
    for box in boxes:
        draw.rectangle(box, fill=BACKGROUND, outline="#404040", width=2)
    for path, box in ((full_path, boxes[0]), (detail_path, boxes[1])):
        with Image.open(path) as source:
            fitted = source.convert("RGB")
        fitted.thumbnail((box[2]-box[0], box[3]-box[1]), Image.Resampling.LANCZOS)
        image.paste(fitted, (box[0]+(box[2]-box[0]-fitted.width)//2, box[1]+(box[3]-box[1]-fitted.height)//2))
    image.save(output)


def run(config_path, pose_report_path, component_name, output_dir, handles=None):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pose_report = json.loads(pose_report_path.read_text(encoding="utf-8"))
    expected = dict(config.get("expected_dxf_by_component") or {})
    component = next(
        item
        for step_path in config.get("step_paths") or []
        for item in extract_leaf_components(str(Path(step_path).resolve()))
        if item.name == component_name
    )
    pose_row = next(item for item in pose_report["components"] if item["component_name"] == component_name)
    registration = pose_row["pose_search"]["locked_registration"]
    dxf_path = Path(config["dxf_dir"]).resolve() / expected[component_name]
    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    analyzer = DxfStructure2DAnalyzer(extractor.document.modelspace())
    sheet_bounds = bounds_of_primitives(extractor.all_primitives)
    _source, payloads = _extract_dxf_worker(str(dxf_path))
    dimensions = [ExtractedDimension(**item) for item in payloads]
    dimensions, _suppressed = HistoricalDataIngestor._deduplicate_dimensions(dimensions)
    dimensions = [item for item in dimensions if HistoricalDataIngestor._tolerance_is_plausible(item)]
    requested_handles = {str(value).upper() for value in (handles or ())}
    if requested_handles:
        dimensions = [
            item for item in dimensions
            if str(item.entity_handle or "").upper() in requested_handles
        ]
    ingestor = HistoricalDataIngestor()
    graph = ingestor.frg_extractor.build_graph(component.shape)
    composite_builder = CompositeAxialSpanBuilder()
    cases, stats = _verify(ingestor, component, dxf_path, dimensions, registration)
    cases_by_handle = {case.source_metadata.get("entity_handle"): case for case in cases}
    diagnostics = {item.get("entity_handle"): item for item in stats.get("_dimension_diagnostics") or []}
    output_dir.mkdir(parents=True, exist_ok=True)
    native_dir = output_dir / "native_renders"
    native_dir.mkdir(exist_ok=True)
    rows, boards, recovered_composites = [], [], []
    for index, dim in enumerate(dimensions, 1):
        association = analyzer.analyze_dimension(dim)
        edges = _recovered_endpoint_edges(extractor, dim, association) or list(association.get("dimension_target_edges") or [])
        case = cases_by_handle.get(dim.entity_handle)
        diagnostic = diagnostics.get(dim.entity_handle) or {}
        recovery = _overall_length_recovery(dim, graph, registration)
        endpoints = [
            (dim.points or {}).get("defpoint2"),
            (dim.points or {}).get("defpoint3"),
        ]
        composite_match = None
        transverse_diameter = None
        if str(dim.dimension_category).upper() == "LINEAR" and all(endpoints):
            transverse_diameter = match_transverse_diameter(
                graph, endpoints, dim.nominal_value, registration
            )
            composite_match = composite_builder.match_projected_dimension(
                graph, endpoints, dim.nominal_value, registration
            )
        if case is not None:
            feature_id = case.source_metadata.get("matched_feature_id")
            field = case.source_metadata.get("matched_nominal_field")
            result = {"status": "VERIFIED", "feature_label": f"3D feature: {feature_id}.{field}", "reason": "Existing projection/topology verifier passed."}
        elif recovery is not None:
            feature_id, field = recovery["feature_id"], recovery["nominal_field"]
            result = {"status": "SEMANTICALLY_RECOVERED", "feature_label": f"3D feature: {feature_id}.{field}", "reason": "Dimension endpoints equal the projected global extrema and the 3D overall length."}
            edges = _overall_end_planes(dim, registration["dxf_bbox"])
            association = dict(association)
            association["recovered_dimension_geometry"] = {"kind": "LINEAR_ENDPOINT_PAIR", "endpoints": recovery["endpoints"]}
        elif transverse_diameter is not None:
            node = transverse_diameter["node"]
            feature_id, field = node.id, transverse_diameter["nominal_field"]
            result = {
                "status": "SEMANTICALLY_RECOVERED",
                "feature_label": f"3D feature: {feature_id}.{field}",
                "reason": (
                    "DXF encoded a cross-axis diameter as LINEAR; endpoint geometry "
                    f"matches {feature_id}.{field}."
                ),
            }
        elif composite_match is not None:
            node = composite_match["node"]
            feature_id, field = node.id, "length"
            member_ids = list((node.source_info or {}).get("member_feature_ids") or [])
            result = {
                "status": "COMPOSITE_RECOVERED",
                "feature_label": f"3D composite: {feature_id}.length",
                "reason": "Projected endpoints match the contiguous 3D chain: " + " + ".join(member_ids),
            }
            edges = _overall_end_planes(dim, registration["dxf_bbox"])
            association = dict(association)
            association["recovered_dimension_geometry"] = {
                "kind": "COMPOSITE_AXIAL_ENDPOINT_PAIR",
                "endpoints": [list(endpoints[0][:2]), list(endpoints[1][:2])],
            }
            recovered_composites.append((dim, node, composite_match))
        elif diagnostic.get("candidates"):
            candidate = diagnostic["candidates"][0]
            feature_id, field = candidate.get("feature_id"), candidate.get("nominal_field")
            result = {"status": "CANDIDATE_ONLY", "feature_label": f"3D candidate: {feature_id}.{field}", "reason": "Nominal candidate exists, but projected geometry/topology verification failed."}
        else:
            feature_id = field = None
            result = {"status": "UNRESOLVED", "feature_label": "3D feature: unresolved", "reason": str(diagnostic.get("status") or "No verified 3D feature candidate.")}
        edge_rgb, span_rgb = STATUS_RGB[result["status"]]
        stem = f"{index:02d}_{_safe(dim.entity_handle)}"
        full_path = native_dir / f"{stem}_full.png"
        detail_path = native_dir / f"{stem}_detail.png"
        label = result["feature_label"]
        focus = _focus_bounds(dim, edges) or sheet_bounds
        _render_native_dxf(dxf_path, dim, association, edges, sheet_bounds, sheet_bounds, full_path, (1800, 1535), edge_rgb=edge_rgb, span_rgb=span_rgb, label=label)
        _render_native_dxf(dxf_path, dim, association, edges, sheet_bounds, focus, detail_path, (1280, 1535), edge_rgb=edge_rgb, span_rgb=span_rgb, label=label)
        board = output_dir / f"{stem}_semantic_review.png"
        _compose_board(dim, result, full_path, detail_path, board)
        boards.append(board)
        rows.append({
            "entity_handle": dim.entity_handle,
            "nominal_value": dim.nominal_value,
            "dimension_category": dim.dimension_category,
            "tolerance_config": dim.tolerance_config,
            "status": result["status"],
            "feature_id": feature_id,
            "nominal_field": field,
            "reason": result["reason"],
            "composite_members": (
                list((composite_match["node"].source_info or {}).get("member_feature_ids") or [])
                if composite_match is not None and result["status"] == "COMPOSITE_RECOVERED" else []
            ),
            "mapped_axial_span": (
                composite_match["mapped_axial_span"]
                if composite_match is not None and result["status"] == "COMPOSITE_RECOVERED" else None
            ),
            "review_image": board.name,
        })
    contact = Image.new("RGB", (1600, 900*len(boards)), BACKGROUND)
    for index, board in enumerate(boards):
        with Image.open(board) as source:
            preview = source.convert("RGB")
            preview.thumbnail((1600, 900), Image.Resampling.LANCZOS)
        contact.paste(preview, ((1600-preview.width)//2, index*900+(900-preview.height)//2))
    contact_path = output_dir / "00_feature_semantic_review_contact_sheet.png"
    contact.save(contact_path)
    retrieval_probe = None
    if recovered_composites:
        dim, node, match = recovered_composites[0]
        experimental_db = output_dir / "experimental_composite_case_base.json"
        case_base = FeatureCaseBase(str(experimental_db))
        stored_case = ToleranceCase(
            case_id=f"AUTO_COMPOSITE_{component_name}_{dim.entity_handle}",
            part_type=graph.part_type,
            feature_type=node.feature_type,
            inferred_role=node.inferred_role or "AXIAL_FEATURE_STACK",
            nominal_dimensions=node.nominal,
            neighbor_types=node.neighbor_types,
            boundary_position=node.boundary_position,
            tolerance_config=dim.tolerance_config,
            confidence=0.95,
            evidence_source=dxf_path.name,
            description="Projection-verified composite axial tolerance experiment.",
            verification_status="AUTO_VERIFIED",
            source_metadata={
                "dimension_category": "LINEAR",
                "drawing_file": dxf_path.name,
                "matched_feature_id": node.id,
                "matched_nominal_field": "length",
                "feature_identity_verified": True,
                "geometry_verification": {
                    "passed": True,
                    "method": match["method"],
                    "endpoint_error": match["endpoint_error"],
                },
                "composite_descriptor": composite_descriptor(node),
            },
        )
        case_base.add_case(stored_case)
        probe_length = round(float(node.nominal["length"]) * 1.01, 6)
        probe_node = FeatureNode(
            id="future_model_same_semantic_region",
            feature_type=node.feature_type,
            nominal={"length": probe_length},
            axial_span=[0.0, probe_length],
            center_axial=probe_length / 2.0,
            neighbor_types=list(node.neighbor_types),
            boundary_position=node.boundary_position,
            inferred_role=node.inferred_role,
            source_info={"composite_descriptor": composite_descriptor(node)},
        )
        probe_matches = case_base.search_similar_cases_detailed(
            probe_node,
            part_type=graph.part_type,
            product_family=FeatureCaseBase.infer_product_family(component_name),
            dimension_category="LINEAR",
        )
        retrieval_probe = {
            "experimental_database": experimental_db.name,
            "query_feature_id": probe_node.id,
            "query_length": probe_length,
            "expected_case_id": stored_case.case_id,
            "retrieved_case_id": probe_matches[0]["case"].case_id if probe_matches else None,
            "similarity": probe_matches[0]["similarity"] if probe_matches else 0.0,
            "score_breakdown": probe_matches[0]["score_breakdown"] if probe_matches else {},
            "passed": bool(probe_matches and probe_matches[0]["case"].case_id == stored_case.case_id),
        }
    report = {
        "schema_version": 1,
        "method": "EXISTING_TOLERANCE_VERIFIER_WITH_COMPOSITE_AXIAL_FEATURES_V2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "component_name": component_name,
        "drawing": dxf_path.name,
        "web_ui_updated": False,
        "summary": {
            "verified": sum(item["status"] == "VERIFIED" for item in rows),
            "semantically_recovered": sum(item["status"] == "SEMANTICALLY_RECOVERED" for item in rows),
            "composite_recovered": sum(item["status"] == "COMPOSITE_RECOVERED" for item in rows),
            "candidate_only": sum(item["status"] == "CANDIDATE_ONLY" for item in rows),
            "unresolved": sum(item["status"] == "UNRESOLVED" for item in rows),
        },
        "dimensions": rows,
        "composite_retrieval_probe": retrieval_probe,
        "contact_sheet": contact_path.name,
    }
    (output_dir / "feature_semantic_review_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--pose-report", required=True, type=Path)
    parser.add_argument("--component", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--handle", action="append", default=[])
    args = parser.parse_args()
    report = run(
        args.config.resolve(), args.pose_report.resolve(), args.component,
        args.output_dir.resolve(), handles=args.handle,
    )
    print(json.dumps({"summary": report["summary"], "dimensions": report["dimensions"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
