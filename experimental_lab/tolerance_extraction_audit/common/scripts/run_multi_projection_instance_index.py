"""Build a per-dimension multi-instance projection index for one drawing."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.tolerance import ingest_historical_data as ingest_module
from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.ingest_historical_data import (
    ExtractedDimension,
    HistoricalDataIngestor,
    _extract_dxf_worker,
)
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2
from run_view_instance_projection_experiment import experimental_verifier


def _instance_key(registration):
    transform = dict(registration.get("transform") or {})
    payload = {
        "bbox": [round(float(value), 2) for value in registration.get("dxf_bbox") or ()],
        "step_view": registration.get("step_view"),
        "rotation": round(float(transform.get("rotation_degrees", 0.0) or 0.0), 2),
        "mirrored": bool(transform.get("mirrored", False)),
    }
    return "projection_instance_" + hashlib.sha1(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]


def _drawing_view_key(bbox):
    payload = [round(float(value), 1) for value in bbox or ()]
    return "drawing_view_instance_" + hashlib.sha1(
        json.dumps(payload).encode("utf-8")
    ).hexdigest()[:12]


def _select_candidate(diagnostic):
    candidates = [
        item for item in diagnostic.get("candidates") or []
        if bool((item.get("geometry_verification") or {}).get("passed"))
    ]
    if not candidates:
        return None, "NO_GEOMETRY_VERIFIED_CANDIDATE"
    topology = [
        item for item in candidates
        if bool(
            (item.get("geometry_verification") or {})
            .get("checks", {})
            .get("dimension_endpoints_map_to_brep_topology")
        )
    ]
    if len(topology) == 1:
        return topology[0], "UNIQUE_BREP_TOPOLOGY_CANDIDATE"
    ranked = sorted(
        candidates,
        key=lambda item: float((item.get("geometry_verification") or {}).get("score", 0.0) or 0.0),
        reverse=True,
    )
    if len(ranked) == 1:
        return ranked[0], "UNIQUE_GEOMETRY_CANDIDATE"
    margin = (
        float(ranked[0]["geometry_verification"].get("score", 0.0) or 0.0)
        - float(ranked[1]["geometry_verification"].get("score", 0.0) or 0.0)
    )
    if margin >= 0.05:
        return ranked[0], "GEOMETRY_SCORE_MARGIN"
    return ranked[0], "AMBIGUOUS_FEATURE_SHARED_PROJECTION"


def run(step: Path, dxf: Path, component_name: str, handles, pose_report: Path, output: Path):
    component = next(
        item for item in extract_leaf_components(str(step))
        if item.name == component_name
    )
    pose_payload = json.loads(pose_report.read_text(encoding="utf-8"))
    pose = next(
        item["pose_search"]["locked_registration"]
        for item in pose_payload["components"]
        if item["component_name"] == component_name
    )
    _source, payloads = _extract_dxf_worker(str(dxf))
    dimensions = [
        ExtractedDimension(**item) for item in payloads
        if (not handles or str(item.get("entity_handle")) in handles)
        and HistoricalDataIngestor._tolerance_is_plausible(ExtractedDimension(**item))
    ]
    extractor = DxfProjectionViewExtractorV2(str(dxf))
    evidence = {
        "pair_id": f"multi-instance:{component_name}:{dxf.name}",
        "pair_method": "DIMENSION_LOCAL_MULTI_PROJECTION_INSTANCE_V1",
        "component_label_entry": component.label_entry,
        "component_name": component.name,
        "matched_dxf_view_ids": [pose["dxf_view_id"]],
        "matched_step_view": pose["step_view"],
        "locked_view_registration": pose,
        "allow_secondary_view_search": True,
        "secondary_dxf_regions": [
            {
                "view_id": candidate.view_id,
                "bbox": list(candidate.bbox),
                "points": [list(point) for point in candidate.points],
                "primitive_count": candidate.primitive_count,
                "source": candidate.source,
            }
            for candidate in extractor.view_candidates
        ],
    }
    ingestor = HistoricalDataIngestor()
    with experimental_verifier():
        _cases, stats = ingestor._verify_pair(
            component.step_path,
            str(dxf),
            dimensions,
            pair_evidence=evidence,
            collect_diagnostics=True,
        )
    instances = {}
    drawing_views = {}
    bindings = []
    for diagnostic in stats.get("_dimension_diagnostics") or []:
        selected, resolution = _select_candidate(diagnostic)
        if selected is None:
            bindings.append({
                "entity_handle": diagnostic.get("entity_handle"),
                "status": resolution,
                "instance_id": None,
            })
            continue
        verification = selected["geometry_verification"]
        registration = dict(verification.get("view_registration") or {})
        instance_id = _instance_key(registration)
        drawing_view_id = _drawing_view_key(registration.get("dxf_bbox"))
        drawing_view = drawing_views.setdefault(drawing_view_id, {
            "drawing_view_instance_id": drawing_view_id,
            "dxf_bbox": registration.get("dxf_bbox"),
            "projection_hypothesis_ids": [],
            "dimension_handles": [],
        })
        instance = instances.setdefault(instance_id, {
            "instance_id": instance_id,
            "dxf_view_id": registration.get("dxf_view_id"),
            "dxf_bbox": registration.get("dxf_bbox"),
            "step_view": registration.get("step_view"),
            "projection": registration.get("projection"),
            "transform": registration.get("transform"),
            "contour_score": registration.get("contour_score"),
            "extent_score": registration.get("extent_score"),
            "source_coverage": registration.get("source_coverage"),
            "target_coverage": registration.get("target_coverage"),
            "equivalent_view_instance_recovery": registration.get("equivalent_view_instance_recovery"),
            "dimension_handles": [],
        })
        instance["dimension_handles"].append(diagnostic.get("entity_handle"))
        if instance_id not in drawing_view["projection_hypothesis_ids"]:
            drawing_view["projection_hypothesis_ids"].append(instance_id)
        drawing_view["dimension_handles"].append(diagnostic.get("entity_handle"))
        bindings.append({
            "entity_handle": diagnostic.get("entity_handle"),
            "dimension_category": diagnostic.get("dimension_category"),
            "nominal": diagnostic.get("nominal_value"),
            "status": resolution,
            "instance_id": instance_id,
            "drawing_view_instance_id": drawing_view_id,
            "feature_id": selected.get("feature_id"),
            "feature_type": selected.get("feature_type"),
            "geometry_score": verification.get("score"),
            "localization_score": (verification.get("feature_localization") or {}).get("score"),
            "topology_passed": (verification.get("checks") or {}).get("dimension_endpoints_map_to_brep_topology"),
            "registered_step_projection_id": diagnostic.get("registered_step_projection_id"),
        })
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_updated": False,
        "method": "DIMENSION_LOCAL_MULTI_PROJECTION_INSTANCE_INDEX_V1",
        "component": component_name,
        "drawing": str(dxf),
        "summary": {
            "dimension_count": len(dimensions),
            "drawing_view_instance_count": len(drawing_views),
            "projection_hypothesis_count": len(instances),
            "bound_dimension_count": sum(item.get("instance_id") is not None for item in bindings),
            "unique_topology_bindings": sum(item.get("status") == "UNIQUE_BREP_TOPOLOGY_CANDIDATE" for item in bindings),
        },
        "drawing_view_instances": list(drawing_views.values()),
        "projection_instances": list(instances.values()),
        "dimension_bindings": bindings,
        "projection_overlays": stats.get("_projection_overlays") or {},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--component", required=True)
    parser.add_argument("--handles", nargs="*", default=[])
    parser.add_argument("--pose-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run(
        args.step.resolve(), args.dxf.resolve(), args.component, set(args.handles),
        args.pose_report.resolve(), args.output.resolve(),
    )
    print(json.dumps({
        "summary": report["summary"],
        "projection_instances": report["projection_instances"],
        "dimension_bindings": report["dimension_bindings"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
