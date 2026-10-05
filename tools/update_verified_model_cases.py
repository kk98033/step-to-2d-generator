"""Incrementally re-verify one model and atomically update the case base."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import ezdxf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.tolerance.case_base import FeatureCaseBase
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor
from auto_2d_drawing.tolerance.ingest_historical_data import (
    DEFAULT_MANIFEST_PATH,
    DEFAULT_REPORT_PATH,
    HistoricalDataIngestor,
    _tolerance_is_plausible_dimension,
)


DEFAULT_MODEL = "1AL0W5000H-R03"


def _select_pair(manifest: dict, model_name: str) -> dict:
    candidates = [
        pair for pair in manifest.get("verified_pairs", [])
        if Path(str(pair.get("dxf_path") or "")).stem.lower() == model_name.lower()
        and pair.get("eligible_for_auto_verification")
    ]
    exact = [pair for pair in candidates if pair.get("pair_method") == "EXACT_FILENAME"]
    selected = exact or candidates
    if len(selected) != 1:
        raise RuntimeError(f"Expected one verified pair for {model_name}, found {len(selected)}")
    return selected[0]


def update_model_cases(
    model_name: str,
    database_path: Path,
    manifest_path: Path,
    rebuild_report_path: Path,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pair = _select_pair(manifest, model_name)
    modelspace = ezdxf.readfile(pair["dxf_path"]).modelspace()
    dimensions = DxfToleranceExtractor().extract_from_modelspace(
        modelspace,
        os.path.basename(pair["dxf_path"]),
        include_rejected=True,
        native_dimensions_only=True,
    )
    dimensions, suppressed = HistoricalDataIngestor._deduplicate_dimensions(dimensions)
    plausible = [item for item in dimensions if _tolerance_is_plausible_dimension(item)]
    ingestor = HistoricalDataIngestor(case_base=FeatureCaseBase(db_path=str(database_path)))
    verified, statistics = ingestor._verify_pair(
        pair["step_path"],
        pair["dxf_path"],
        plausible,
        pair,
    )
    rejected = statistics.pop("_rejected_evidence", [])
    consumed_handles = {
        handle
        for _case, evidence_keys in verified
        for _path, handle in evidence_keys
        if handle
    }
    target_dxf = os.path.normcase(os.path.abspath(pair["dxf_path"]))
    kept = []
    removed = []
    for case in ingestor.case_base.cases:
        metadata = case.source_metadata or {}
        case_dxf = str(metadata.get("dxf_path") or "")
        same_dxf = bool(case_dxf) and os.path.normcase(os.path.abspath(case_dxf)) == target_dxf
        handle = str(metadata.get("entity_handle") or "")
        replace_raw = same_dxf and handle in consumed_handles
        replace_prior_link = same_dxf and case.case_id.startswith("HIST2_")
        if replace_raw or replace_prior_link:
            removed.append(case)
        else:
            kept.append(case)
    added = [case for case, _evidence_keys in verified]
    final_cases = kept + added
    final_cases.sort(key=lambda case: (case.evidence_source.upper(), case.case_id))
    case_ids = [case.case_id for case in final_cases]
    if len(case_ids) != len(set(case_ids)):
        raise RuntimeError("Incremental update would create duplicate case IDs")
    ingestor._atomic_write_json(str(database_path), [case.to_dict() for case in final_cases])

    update = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "pair_id": pair.get("pair_id"),
        "algorithm": "DXF_ATTACHMENT_ADAPTIVE_STEP_HLR_TOPOLOGY_V4",
        "plausible_dimension_count": len(plausible),
        "duplicate_dimensions_suppressed": suppressed,
        "removed_case_count": len(removed),
        "added_auto_verified_count": len(added),
        "consumed_entity_handles": sorted(consumed_handles),
        "rejected_candidate_count": len(rejected),
        "verification_statistics": statistics,
        "added_case_ids": [case.case_id for case in added],
    }
    if rebuild_report_path.exists():
        report = json.loads(rebuild_report_path.read_text(encoding="utf-8"))
        report.setdefault("incremental_topology_updates", []).append(update)
        status_counts = Counter(case.effective_verification_status() for case in final_cases)
        feature_counts = Counter(case.feature_type for case in final_cases)
        report.setdefault("verification", {})["counts"] = dict(status_counts)
        report.setdefault("database", {})["total_cases"] = len(final_cases)
        report["database"]["feature_counts"] = dict(feature_counts)
        report["last_incremental_update_utc"] = update["updated_at_utc"]
        ingestor._atomic_write_json(str(rebuild_report_path), report)
    return update


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--database", type=Path, default=Path(FeatureCaseBase.DEFAULT_DB_PATH))
    parser.add_argument("--manifest", type=Path, default=Path(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--rebuild-report", type=Path, default=Path(DEFAULT_REPORT_PATH))
    args = parser.parse_args()
    result = update_model_cases(args.model, args.database, args.manifest, args.rebuild_report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
