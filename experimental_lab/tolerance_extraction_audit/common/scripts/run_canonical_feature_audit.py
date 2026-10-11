"""Compare historical tolerance linkage before/after canonical feature IDs."""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from auto_2d_drawing.canonical_features import extract_canonical_features
from auto_2d_drawing.tolerance.feature_graph import FeatureGraphExtractor
from auto_2d_drawing.tolerance.ingest_historical_data import (
    ExtractedDimension,
    HistoricalDataIngestor,
    _extract_dxf_worker,
)


def _tolerance_signature(config: Dict[str, Any]) -> str:
    return json.dumps(config or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _expected_key(record: Dict[str, Any]) -> Tuple[str, float, str]:
    return (
        str(record["expected"]["dimension_category"]),
        round(float(record["expected"]["nominal_value"]), 6),
        _tolerance_signature(record["expected"]["tolerance_config"]),
    )


def _case_key(case) -> Tuple[str, float, str]:
    metadata = case.source_metadata or {}
    field_name = str(metadata.get("matched_nominal_field") or "")
    nominal_value = float((case.nominal_dimensions or {}).get(field_name, 0.0) or 0.0)
    return (
        str(metadata.get("dimension_category") or ""),
        round(nominal_value, 6),
        _tolerance_signature(case.tolerance_config),
    )


def _score(expected: Counter, predicted: Counter) -> Dict[str, Any]:
    true_positive = sum((expected & predicted).values())
    predicted_count = sum(predicted.values())
    expected_count = sum(expected.values())
    return {
        "expected": expected_count,
        "predicted": predicted_count,
        "exact_matches": true_positive,
        "precision": round(true_positive / predicted_count, 6) if predicted_count else 0.0,
        "recall": round(true_positive / expected_count, 6) if expected_count else 0.0,
    }


def run(dataset_path: Path) -> Dict[str, Any]:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    records = payload["records"]
    grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        source = record["source"]
        grouped[(
            source["step_path"],
            source["dxf_path"],
            str(source.get("component_label_entry") or ""),
        )].append(record)

    modes = {
        "legacy": FeatureGraphExtractor,
        "canonical": None,
    }
    totals = {
        name: {
            "expected": Counter(),
            "predicted": Counter(),
            "linked_case_count": 0,
            "ui_identity_match_count": 0,
            "stats": Counter(),
        }
        for name in modes
    }
    pair_results = []

    for (step_path, dxf_path, component_label), expected_rows in grouped.items():
        _source, dimension_payloads = _extract_dxf_worker(dxf_path)
        dimensions = [ExtractedDimension(**item) for item in dimension_payloads]
        dimensions, _suppressed = HistoricalDataIngestor._deduplicate_dimensions(dimensions)
        dimensions = [
            item for item in dimensions
            if HistoricalDataIngestor._tolerance_is_plausible(item)
        ]
        expected = Counter(_expected_key(record) for record in expected_rows)
        pair_result = {
            "step": os.path.basename(step_path),
            "dxf": os.path.basename(dxf_path),
            "expected_cases": sum(expected.values()),
            "plausible_dimensions": len(dimensions),
            "modes": {},
        }

        for mode_name, extractor_type in modes.items():
            ingestor = HistoricalDataIngestor()
            if extractor_type is not None:
                ingestor.frg_extractor = extractor_type()
            cases, stats = ingestor._verify_pair(
                step_path,
                dxf_path,
                dimensions,
                pair_evidence={
                    "pair_id": f"canonical-audit:{os.path.basename(dxf_path)}",
                    "pair_method": "EXACT_FILENAME",
                    "component_label_entry": component_label,
                    "verification_checks": ["frozen_silver_source_pair"],
                },
            )
            stats.pop("_rejected_evidence", None)
            predicted = Counter(_case_key(case) for case, _keys in cases)
            shape = ingestor._load_step_shape(step_path) if not component_label else None
            ui_ids = set()
            if shape is not None:
                ui_ids = {record["id"] for record in extract_canonical_features(shape).records}
            identity_matches = sum(1 for case, _keys in cases if case.source_metadata.get("matched_feature_id") in ui_ids)

            totals[mode_name]["expected"].update(expected)
            totals[mode_name]["predicted"].update(predicted)
            totals[mode_name]["linked_case_count"] += len(cases)
            totals[mode_name]["ui_identity_match_count"] += identity_matches
            totals[mode_name]["stats"].update(stats)
            pair_result["modes"][mode_name] = {
                **_score(expected, predicted),
                "ui_identity_matches": identity_matches,
                "linked_feature_ids": [
                    case.source_metadata.get("matched_feature_id") for case, _keys in cases
                ],
                "verification_stats": stats,
            }
        pair_results.append(pair_result)

    summary = {}
    for mode_name, total in totals.items():
        linked_count = int(total["linked_case_count"])
        summary[mode_name] = {
            **_score(total["expected"], total["predicted"]),
            "linked_case_count": linked_count,
            "ui_identity_match_count": int(total["ui_identity_match_count"]),
            "ui_identity_accuracy": round(total["ui_identity_match_count"] / linked_count, 6) if linked_count else 0.0,
            "verification_stats": dict(total["stats"]),
        }
    return {
        "schema_version": 1,
        "dataset": str(dataset_path),
        "silver_case_count": len(records),
        "source_pair_count": len(grouped),
        "summary": summary,
        "pairs": pair_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
