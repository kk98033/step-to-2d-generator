"""Run the generic local-view recovery against one auditable tolerance."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
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
from view_instance_projection_verifier import ViewInstanceProjectionGeometryVerifier


@contextmanager
def experimental_verifier():
    original = ingest_module.ProjectionGeometryVerifier
    ingest_module.ProjectionGeometryVerifier = ViewInstanceProjectionGeometryVerifier
    try:
        yield
    finally:
        ingest_module.ProjectionGeometryVerifier = original


def run(step: Path, dxf: Path, component_name: str, handle: str, pose_report: Path, output: Path):
    component = next(
        item for item in extract_leaf_components(str(step))
        if item.name == component_name
    )
    report = json.loads(pose_report.read_text(encoding="utf-8"))
    pose = next(
        item["pose_search"]["locked_registration"]
        for item in report["components"]
        if item["component_name"] == component_name
    )
    _source, payloads = _extract_dxf_worker(str(dxf))
    dimension = next(
        ExtractedDimension(**item) for item in payloads
        if str(item.get("entity_handle")) == handle
    )
    view_extractor = DxfProjectionViewExtractorV2(str(dxf))
    evidence = {
        "pair_id": f"view-instance:{component_name}:{dxf.name}:{handle}",
        "pair_method": "DIMENSION_LOCAL_EQUIVALENT_VIEW_INSTANCE_V1",
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
            for candidate in view_extractor.view_candidates
        ],
    }
    ingestor = HistoricalDataIngestor()
    with experimental_verifier():
        cases_with_keys, stats = ingestor._verify_pair(
            component.step_path,
            str(dxf),
            [dimension],
            pair_evidence=evidence,
            collect_diagnostics=True,
        )
    cases = [case for case, _keys in cases_with_keys]
    diagnostics = stats.get("_dimension_diagnostics") or []
    topology_resolution = None
    if diagnostics:
        candidates = diagnostics[0].get("candidates") or []
        passed = [
            item for item in candidates
            if bool((item.get("geometry_verification") or {}).get("passed"))
        ]
        topology_passed = [
            item for item in passed
            if bool(
                (item.get("geometry_verification") or {})
                .get("checks", {})
                .get("dimension_endpoints_map_to_brep_topology")
            )
        ]
        if len(passed) > 1 and len(topology_passed) == 1:
            winner = topology_passed[0]
            topology_resolution = {
                "status": "UNIQUE_BREP_TOPOLOGY_CANDIDATE",
                "feature_id": winner.get("feature_id"),
                "feature_type": winner.get("feature_type"),
                "nominal_field": winner.get("nominal_field"),
                "geometry_score": (winner.get("geometry_verification") or {}).get("score"),
                "candidate_count_before": len(passed),
                "candidate_count_after": 1,
                "database_ingestion_allowed": False,
                "semantic_role_pending": True,
            }
    result = {
        "schema_version": 1,
        "method": ViewInstanceProjectionGeometryVerifier.METHOD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_updated": False,
        "component": component_name,
        "dxf": str(dxf),
        "dimension_handle": handle,
        "nominal": dimension.nominal_value,
        "tolerance_config": dict(dimension.tolerance_config or {}),
        "verified_case_count": len(cases),
        "verified_feature_ids": [case.source_metadata.get("matched_feature_id") for case in cases],
        "experimental_topology_resolution": topology_resolution,
        "stats": {key: value for key, value in stats.items() if not key.startswith("_")},
        "diagnostics": diagnostics,
        "projection_overlays": stats.get("_projection_overlays") or {},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--component", required=True)
    parser.add_argument("--handle", required=True)
    parser.add_argument("--pose-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(
        args.step.resolve(), args.dxf.resolve(), args.component, args.handle,
        args.pose_report.resolve(), args.output.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
