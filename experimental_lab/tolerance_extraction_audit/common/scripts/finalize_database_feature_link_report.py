"""Apply final trust policy to a completed feature-link checkpoint."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from run_database_feature_link_batch import (
    GlobalGeometryPairSearcher,
    _aggregate,
    _collect_inputs,
    _key,
    _render,
)


def run(
    config_path: Path,
    extraction_path: Path,
    report_path: Path,
    checkpoint_path: Path,
    specialized_report_path: Path | None = None,
    specialized_config_path: Path | None = None,
):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    _steps, drawings = _collect_inputs(config, extraction)
    pair_results = list(checkpoint.get("pair_results") or [])

    downgraded = sum(
        assignment.get("reason") == "GLOBAL_GEOMETRY_PAIR_WITHOUT_BREP_ENDPOINT_PROOF"
        for pair_result in pair_results
        for assignment in pair_result.get("assignments") or []
    )
    for pair_result in pair_results:
        if pair_result.get("pair_method") != GlobalGeometryPairSearcher.METHOD:
            continue
        for assignment in pair_result.get("assignments") or []:
            checks = (assignment.get("geometry_verification") or {}).get("checks") or {}
            if assignment.get("status") == "VERIFIED" and not checks.get(
                "dimension_endpoints_map_to_brep_topology", False
            ):
                assignment["status"] = "CANDIDATE_ONLY"
                assignment["reason"] = "GLOBAL_GEOMETRY_PAIR_WITHOUT_BREP_ENDPOINT_PROOF"
                downgraded += 1
        pair_result["verified_assignment_count"] = sum(
            item.get("status") == "VERIFIED" for item in pair_result.get("assignments") or []
        )
        pair_result["candidate_assignment_count"] = sum(
            item.get("status") == "CANDIDATE_ONLY" for item in pair_result.get("assignments") or []
        )

    tolerances = _aggregate(drawings, pair_results)
    specialized_applied = 0
    if specialized_report_path and specialized_config_path:
        specialized = json.loads(specialized_report_path.read_text(encoding="utf-8"))
        specialized_config = json.loads(specialized_config_path.read_text(encoding="utf-8"))
        expected = specialized_config.get("expected_dxf_by_component") or {}
        specialized_by_key = {
            (str(expected.get(row.get("component")) or "").casefold(), str(row.get("entity_handle") or "").upper()): row
            for row in specialized.get("explicit_tolerances") or []
            if expected.get(row.get("component"))
        }
        source_step = (specialized_config.get("step_paths") or [None])[0]
        for row in tolerances:
            source = specialized_by_key.get((str(row.get("drawing") or "").casefold(), str(row.get("entity_handle") or "").upper()))
            if source is None:
                continue
            status = str(source.get("status") or "UNRESOLVED").upper()
            row.update({
                "status": status,
                "feature_id": source.get("feature_id"),
                "feature_type": source.get("feature_class") or row.get("feature_type"),
                "feature_role": source.get("semantic_path"),
                "nominal_field": source.get("nominal_field"),
                "confidence": 0.98 if status == "VERIFIED" else 0.65,
                "reason": source.get("reason") or "CURRENT_FINAL_SPECIALIZED_ASSEMBLY_PIPELINE",
                "pair": {
                    "pair_method": "CURRENT_FINAL_SPECIALIZED_ASSEMBLY_PIPELINE",
                    "step_path": source_step,
                    "component_name": source.get("component"),
                    "component_label_entry": None,
                },
                "case_retrieval": dict(source.get("case_retrieval") or {
                    "eligible": status in {"VERIFIED", "CANDIDATE_ONLY"},
                    "tier": "TRUSTED" if status == "VERIFIED" else "REVIEW_REQUIRED",
                    "engineer_review_required": status != "VERIFIED",
                }),
                "specialized_current_final_source": str(specialized_report_path),
            })
            specialized_applied += 1
    paired_drawings = {_key(row["dxf_path"]) for row in pair_results}
    families = []
    for family in config.get("families") or []:
        rows = [row for row in tolerances if row["family"] == family]
        families.append({
            "family": family,
            "tolerance_count": len(rows),
            "verified": sum(row["status"] == "VERIFIED" for row in rows),
            "candidate_only": sum(row["status"] == "CANDIDATE_ONLY" for row in rows),
            "unresolved": sum(row["status"] == "UNRESOLVED" for row in rows),
            "searchable": sum(row["case_retrieval"]["eligible"] for row in rows),
            "paired_drawing_count": len({_key(row["dxf_path"]) for row in rows} & paired_drawings),
        })
    summary = dict(report["summary"])
    summary.update({
        "processed_pair_count": len(pair_results),
        "pair_error_count": len(checkpoint.get("pair_errors") or []),
        "tolerance_count": len(tolerances),
        "verified": sum(row["status"] == "VERIFIED" for row in tolerances),
        "candidate_only": sum(row["status"] == "CANDIDATE_ONLY" for row in tolerances),
        "unresolved": sum(row["status"] == "UNRESOLVED" for row in tolerances),
        "searchable": sum(row["case_retrieval"]["eligible"] for row in tolerances),
        "global_geometry_verified_downgraded_without_topology": downgraded,
        "specialized_current_final_overrides": specialized_applied,
    })
    report["summary"] = summary
    report["families"] = families
    report["pair_results"] = pair_results
    report["tolerances"] = tolerances
    report["trust_policy"] = {
        "global_geometry_pair_requires_brep_endpoint_proof_for_verified": True,
        "candidate_only_remains_searchable": True,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _render(report, report_path.with_name("database_feature_link_overview.png"))
    with report_path.with_name("database_feature_link_cases.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        fieldnames = [
            "family", "drawing", "entity_handle", "nominal_value",
            "dimension_category", "status", "feature_type", "feature_id",
            "feature_role", "nominal_field", "confidence", "pair_method",
            "component_name", "recommendation_tier", "engineer_review_required", "reason",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in tolerances:
            pair = row.get("pair") or {}
            policy = row.get("case_retrieval") or {}
            writer.writerow({
                "family": row.get("family"),
                "drawing": row.get("drawing"),
                "entity_handle": row.get("entity_handle"),
                "nominal_value": row.get("nominal_value"),
                "dimension_category": row.get("dimension_category"),
                "status": row.get("status"),
                "feature_type": row.get("feature_type"),
                "feature_id": row.get("feature_id"),
                "feature_role": row.get("feature_role"),
                "nominal_field": row.get("nominal_field"),
                "confidence": row.get("confidence"),
                "pair_method": pair.get("pair_method"),
                "component_name": pair.get("component_name"),
                "recommendation_tier": policy.get("tier"),
                "engineer_review_required": policy.get("engineer_review_required"),
                "reason": row.get("reason"),
            })
    checkpoint_path.write_text(json.dumps({
        **checkpoint,
        "pair_results": pair_results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--extraction-report", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--specialized-report", type=Path)
    parser.add_argument("--specialized-config", type=Path)
    args = parser.parse_args()
    summary = run(
        args.config.resolve(), args.extraction_report.resolve(),
        args.report.resolve(), args.checkpoint.resolve(),
        args.specialized_report.resolve() if args.specialized_report else None,
        args.specialized_config.resolve() if args.specialized_config else None,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
