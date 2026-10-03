"""Annotate rejected numeric STEP/DXF matches without rebuilding all DXFs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import ezdxf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.tolerance.case_base import FeatureCaseBase
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor
from auto_2d_drawing.tolerance.ingest_historical_data import HistoricalDataIngestor


DEFAULT_DB = PROJECT_ROOT / "auto_2d_drawing" / "tolerance" / "data" / "feature_case_base.json"
DEFAULT_MANIFEST = PROJECT_ROOT / "auto_2d_drawing" / "tolerance" / "data" / "historical_pair_manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    case_base = FeatureCaseBase(str(args.case_db))
    ingestor = HistoricalDataIngestor(case_base)
    extractor = DxfToleranceExtractor()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    raw_by_evidence = {}
    for case in case_base.cases:
        if case.effective_verification_status() != "AUTO_EXTRACTED":
            continue
        metadata = case.source_metadata or {}
        dxf_path = str(metadata.get("dxf_path") or "")
        entity_handle = str(metadata.get("entity_handle") or "")
        if dxf_path and entity_handle:
            raw_by_evidence[(os.path.normcase(os.path.abspath(dxf_path)), entity_handle)] = case

    annotated = 0
    for index, pair in enumerate(manifest.get("verified_pairs", []), start=1):
        try:
            modelspace = ezdxf.readfile(pair["dxf_path"]).modelspace()
            dimensions = extractor.extract_from_modelspace(
                modelspace,
                os.path.basename(pair["dxf_path"]),
                include_rejected=True,
                native_dimensions_only=True,
            )
            dimensions, _ = ingestor._deduplicate_dimensions([
                item for item in dimensions if ingestor._tolerance_is_plausible(item)
            ])
            _verified, stats = ingestor._verify_pair(
                pair["step_path"], pair["dxf_path"], dimensions, pair_evidence=pair
            )
            for rejected in stats.get("_rejected_evidence", []):
                payload = dict(rejected)
                evidence_key = tuple(payload.pop("evidence_key"))
                case = raw_by_evidence.get(evidence_key)
                if case is None:
                    continue
                geometry = payload.pop("geometry_verification")
                case.source_metadata["verification_candidate"] = payload
                case.source_metadata["geometry_verification"] = geometry
                case.source_metadata["feature_identity_verified"] = False
                annotated += 1
        except Exception as exc:
            print(f"pair {index} failed: {pair.get('pair_id')}: {exc}", flush=True)
        else:
            print(f"pair {index}: {pair.get('pair_id')}", flush=True)

    ingestor._atomic_write_json(str(args.case_db), [case.to_dict() for case in case_base.cases])
    print(json.dumps({"database": str(args.case_db), "annotated_rejections": annotated}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
