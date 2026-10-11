"""Run the latest tolerance extractor for every configured assembly component.

The output is an isolated DEBUG snapshot.  It records successful and zero-hit
runs alike and never writes to the production feature case base.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.ingest_historical_data import (
    ExtractedDimension,
    HistoricalDataIngestor,
    _extract_dxf_worker,
)


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    expected = dict(config.get("expected_dxf_by_component") or {})
    dxf_dir = Path(config["dxf_dir"])
    components = {
        component.name: component
        for step_path in config.get("step_paths") or []
        for component in extract_leaf_components(str(Path(step_path).resolve()))
        if component.name in expected
    }

    runs = []
    for component_name, drawing_name in expected.items():
        component = components.get(component_name)
        dxf_path = (dxf_dir / drawing_name).resolve()
        if component is None or not dxf_path.is_file():
            runs.append({
                "component_name": component_name,
                "drawing": drawing_name,
                "status": "SOURCE_MISSING",
                "plausible_dimension_count": 0,
                "verified_case_count": 0,
                "cases": [],
                "verification_stats": {"source_missing": 1},
            })
            continue

        _source, payloads = _extract_dxf_worker(str(dxf_path))
        dimensions = [ExtractedDimension(**item) for item in payloads]
        dimensions, suppressed = HistoricalDataIngestor._deduplicate_dimensions(dimensions)
        dimensions = [
            item for item in dimensions
            if HistoricalDataIngestor._tolerance_is_plausible(item)
        ]
        ingestor = HistoricalDataIngestor()
        cases_with_keys, stats = ingestor._verify_pair(
            component.step_path,
            str(dxf_path),
            dimensions,
            pair_evidence={
                "pair_id": f"latest-debug:{component_name}:{drawing_name}",
                "pair_method": "KNOWN_COMPONENT_DRAWING_DEBUG_REPLAY",
                "component_label_entry": component.label_entry,
                "verification_checks": [
                    "configured_component_source",
                    "configured_expected_drawing",
                ],
            },
            collect_diagnostics=True,
        )
        cases = [case for case, _keys in cases_with_keys]
        public_stats = {key: value for key, value in stats.items() if not key.startswith("_")}
        runs.append({
            "component_name": component_name,
            "component_label_entry": component.label_entry,
            "step_path": component.step_path,
            "drawing": drawing_name,
            "dxf_path": str(dxf_path),
            "status": "AUTO_VERIFIED" if cases else "NO_VERIFIED_TOLERANCE",
            "plausible_dimension_count": len(dimensions),
            "suppressed_duplicate_count": int(suppressed),
            "dimension_handles": [str(item.entity_handle or "") for item in dimensions],
            "verified_case_count": len(cases),
            "feature_ids": [
                case.source_metadata.get("matched_feature_id") for case in cases
            ],
            "cases": [case.to_dict() for case in cases],
            "verification_stats": public_stats,
            "dimension_diagnostics": stats.get("_dimension_diagnostics") or [],
            "projection_overlays": stats.get("_projection_overlays") or {},
        })

    return {
        "schema_version": 1,
        "algorithm": "CANONICAL_COMPONENT_TOLERANCE_REPLAY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(config_path.resolve()),
        "run_count": len(runs),
        "runs": runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = run(args.config.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "run_count": payload["run_count"],
        "runs": [
            {
                "component_name": item["component_name"],
                "drawing": item["drawing"],
                "status": item["status"],
                "plausible_dimension_count": item["plausible_dimension_count"],
                "verified_case_count": item["verified_case_count"],
            }
            for item in payload["runs"]
        ],
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
