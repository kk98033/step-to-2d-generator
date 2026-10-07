"""End-to-end engineer personalization acceptance using a real CAD model."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.run_real_model_acceptance import (
    DEFAULT_MANIFEST,
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_ROOT,
    run_acceptance,
)
from web_app.backend.engineer_personalization import personalize_recommendations
from web_app.backend.identity_store import IdentityStore


def run_engineer_acceptance(
    model_name: str = DEFAULT_MODEL,
    manifest_path: Path = DEFAULT_MANIFEST,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    render: bool = True,
) -> Dict[str, Any]:
    real_report = run_acceptance(model_name, manifest_path, output_root, render=render)
    if not str(real_report.get("status", "")).startswith("PASSED"):
        raise RuntimeError("Real-model CAD acceptance failed")

    recommended = {
        rule_id: value
        for rule_id, value in (real_report.get("recommendations") or {}).items()
        if value.get("decision_status") == "RECOMMENDED"
    }
    if not recommended:
        raise RuntimeError("Real-model acceptance produced no adoptable tolerance")

    with tempfile.TemporaryDirectory() as tempdir:
        store = IdentityStore(f"sqlite:///{(Path(tempdir) / 'identity.db').as_posix()}")
        try:
            admin = store.authenticate("admin", "ForceconAdmin!2026")
            engineer_row = store.create_user({
                "username": "acceptance-engineer",
                "display_name": "Acceptance Engineer",
                "role": "ENGINEER",
                "password": "Acceptance-Engineer-2026",
            }, admin.id)
            engineer = store.authenticate("acceptance-engineer", "Acceptance-Engineer-2026")
            other_row = store.create_user({
                "username": "isolated-engineer",
                "display_name": "Isolated Engineer",
                "role": "ENGINEER",
                "password": "Isolated-Engineer-2026",
            }, admin.id)
            other = store.authenticate("isolated-engineer", "Isolated-Engineer-2026")

            store.update_preferences(engineer.id, {
                "recommendation_mode": "PERSONAL_FIRST",
                "personal_case_weight": 0.50,
                "personal_case_min_similarity": 0.80,
                "dimension_placement": {
                    "preferred_view": "front",
                    "side": "BOTTOM",
                    "baseline": "LEFT",
                    "offset": 12.0,
                },
            })
            store.claim_model(model_name, engineer.id, f"{model_name}.stp")

            feature_records = []
            for rule_id, item in recommended.items():
                feature_records.append({
                    "rule_id": rule_id,
                    "category": item.get("feature_type"),
                    "inferred_role": item.get("inferred_role"),
                    "nominal_value": item.get("nominal_value"),
                    "tolerance_config": item.get("tolerance_config") or {
                        "mode": item.get("recommended_mode"),
                        "fit_class": item.get("fit_class"),
                        "upper_dev": item.get("upper_dev"),
                        "lower_dev": item.get("lower_dev"),
                    },
                    "preferred_view": "front",
                    "side": "BOTTOM",
                    "baseline": "LEFT",
                    "offset": 12.0,
                })

            drawing_paths = real_report.get("generated_drawings") or {}
            output_files = {
                f"{key}_url": str(value)
                for key, value in drawing_paths.items()
                if key in {"dxf", "pdf", "png", "svg"}
            }
            learning = store.record_artifact_and_cases(
                user=engineer,
                model_id=model_name,
                part_id=model_name,
                title=f"{model_name} acceptance annotation",
                part_type="FAN_HOUSING",
                product_family="AL0W",
                output_files=output_files,
                feature_records=feature_records,
            )

            query_rules = [{
                "rule_id": item["rule_id"],
                "category": item["category"],
                "inferred_role": item.get("inferred_role"),
                "nominal_value": item["nominal_value"],
            } for item in feature_records]
            base_result = {"recommendations": {
                item["rule_id"]: {
                    "rule_id": item["rule_id"],
                    "recommended_mode": "NONE",
                    "evidence_cases": [],
                }
                for item in query_rules
            }}
            personalized = personalize_recommendations(
                store, engineer, query_rules, base_result,
                part_type="FAN_HOUSING", product_family="AL0W",
            )
            isolated = personalize_recommendations(
                store,
                other,
                query_rules,
                {"recommendations": {
                    item["rule_id"]: {"rule_id": item["rule_id"], "evidence_cases": []}
                    for item in query_rules
                }},
                part_type="FAN_HOUSING",
                product_family="AL0W",
            )

            adopted = sum(
                bool(item.get("personalization", {}).get("adopted"))
                for item in personalized["recommendations"].values()
            )
            gates = {
                "real_model_acceptance_passed": True,
                "historical_dxf_reference_valid": real_report["gates"]["adopted_recommendation_cites_existing_original_dxf"],
                "private_cases_learned": learning["learned_case_count"] == len(feature_records),
                "same_engineer_personalized": adopted == len(feature_records),
                "other_engineer_isolated": isolated["engineer_personalization"]["personal_case_count"] == 0,
                "administrator_can_read_artifact": len(store.list_artifacts(admin, engineer.id)) == 1,
                "other_engineer_cannot_read_artifact": len(store.list_artifacts(other)) == 0,
            }
            report = {
                "schema_version": 1,
                "model": model_name,
                "status": "PASSED" if all(gates.values()) else "FAILED",
                "gates": gates,
                "company_recommendation_count": len(recommended),
                "learned_private_case_count": learning["learned_case_count"],
                "personalized_recommendation_count": adopted,
                "original_dxf_reference_count": real_report["recommendation_source_validation"]["valid_decision_reference_count"],
                "source_real_acceptance_report": real_report.get("report_path"),
            }
        finally:
            store.close()

    output_dir = output_root / model_name
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "engineer_account_acceptance.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--skip-render", action="store_true")
    args = parser.parse_args()
    report = run_engineer_acceptance(
        model_name=args.model,
        manifest_path=args.manifest,
        output_root=args.output_root,
        render=not args.skip_render,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
