"""Run the real-model STEP-to-2D and tolerance-evidence acceptance test.

The default fixture is the company model ``1AL0W5000H-R03``.  The test does
not use a synthetic CAD shape: it renders the real STEP file, verifies its
historical DXF dimensions against B-Rep topology, runs tolerance
recommendation, and checks every cited case against the original DXF path
stored in the case database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import ezdxf
from ezdxf import bbox as dxf_bbox

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.batch_generate import generate_single
from auto_2d_drawing.smart_annotation_engine import SmartAnnotationEngine
from auto_2d_drawing.step_reader import load_step
from auto_2d_drawing.tolerance.case_base import FeatureCaseBase
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor
from auto_2d_drawing.tolerance.ingest_historical_data import (
    HistoricalDataIngestor,
    _tolerance_is_plausible_dimension,
)
from auto_2d_drawing.tolerance.tolerance_decision_service import ToleranceDecisionService
from auto_2d_drawing.view_projector import ViewProjector


DEFAULT_MODEL = "1AL0W5000H-R03"
DEFAULT_MANIFEST = PROJECT_ROOT / "auto_2d_drawing" / "tolerance" / "data" / "historical_pair_manifest.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "auto_2d_drawing" / "output" / "acceptance"


def _find_pair(model_name: str, manifest_path: Path) -> Dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = model_name.lower()
    pairs = [
        item for item in manifest.get("verified_pairs", [])
        if Path(str(item.get("dxf_path") or "")).stem.lower() == expected
        and bool(item.get("eligible_for_auto_verification"))
    ]
    exact = [item for item in pairs if item.get("pair_method") == "EXACT_FILENAME"]
    selected = exact or pairs
    if len(selected) != 1:
        raise RuntimeError(f"Expected one verified STEP/DXF pair for {model_name}, found {len(selected)}")
    return selected[0]


def _verify_historical_geometry(pair: Dict[str, Any]) -> Dict[str, Any]:
    modelspace = ezdxf.readfile(pair["dxf_path"]).modelspace()
    dimensions = DxfToleranceExtractor().extract_from_modelspace(
        modelspace,
        os.path.basename(pair["dxf_path"]),
        include_rejected=True,
        native_dimensions_only=True,
    )
    plausible = [item for item in dimensions if _tolerance_is_plausible_dimension(item)]
    verified, statistics = HistoricalDataIngestor()._verify_pair(
        pair["step_path"],
        pair["dxf_path"],
        plausible,
        pair,
    )
    cases = []
    for case, _evidence_keys in verified:
        geometry = dict((case.source_metadata or {}).get("geometry_verification") or {})
        topology = dict(geometry.get("topology_mapping") or {})
        cases.append({
            "case_id": case.case_id,
            "feature_type": case.feature_type,
            "nominal_dimensions": case.nominal_dimensions,
            "tolerance_config": case.tolerance_config,
            "entity_handle": (case.source_metadata or {}).get("entity_handle"),
            "geometry_score": geometry.get("score"),
            "topology_status": topology.get("status"),
            "logical_surface_id": topology.get("logical_surface_id"),
            "topological_face_status": topology.get("topological_face_status"),
            "edge_mapping_status": topology.get("edge_mapping_status"),
        })
    return {
        "source_dimension_count": len(plausible),
        "verified_historical_tolerance_count": len(cases),
        "cases": cases,
        "statistics": {key: value for key, value in statistics.items() if not key.startswith("_")},
    }


def _validate_recommendation_sources(
    recommendations: Dict[str, Dict[str, Any]],
    case_base: FeatureCaseBase,
) -> Dict[str, Any]:
    case_index = {case.case_id: case for case in case_base.cases}
    references: List[Dict[str, Any]] = []
    for rule_id, recommendation in recommendations.items():
        for evidence in recommendation.get("evidence_cases") or []:
            case_id = str(evidence.get("case_id") or "")
            case = case_index.get(case_id)
            metadata = dict((case.source_metadata if case else {}) or {})
            dxf_path = str(metadata.get("dxf_path") or "")
            exists = bool(dxf_path and os.path.isfile(dxf_path))
            references.append({
                "rule_id": rule_id,
                "case_id": case_id,
                "used_for_decision": bool(evidence.get("used_for_decision")),
                "evidence_role": evidence.get("evidence_role"),
                "source_model": evidence.get("source_model"),
                "verification_status": evidence.get("verification_status"),
                "dxf_path": dxf_path or None,
                "original_dxf_exists": exists,
                "database_case_found": case is not None,
                "source_entity_handle": (evidence.get("source_entity") or {}).get("handle"),
            })
    used = [item for item in references if item["used_for_decision"]]
    return {
        "reference_count": len(references),
        "valid_original_dxf_reference_count": sum(item["original_dxf_exists"] for item in references),
        "decision_reference_count": len(used),
        "valid_decision_reference_count": sum(item["original_dxf_exists"] for item in used),
        "references": references,
    }


def _audit_generated_dxf_layout(dxf_path: str) -> Dict[str, Any]:
    """Detect generated geometry/annotation outside the drawing border."""

    document = ezdxf.readfile(dxf_path)
    modelspace = document.modelspace()
    border_entities = [entity for entity in modelspace if str(entity.dxf.layer).upper() == "BORDER"]
    border_extents = dxf_bbox.extents(border_entities)
    if not border_extents.has_data:
        return {"status": "NO_BORDER", "within_border": False, "outside_entities": []}
    minimum = border_extents.extmin
    maximum = border_extents.extmax
    ignored_layers = {"BORDER", "TITLE_LABEL", "TITLE_VALUE"}
    outside = []
    tolerance = 0.01
    for entity in modelspace:
        layer = str(entity.dxf.layer or "0").upper()
        if layer in ignored_layers:
            continue
        extents = dxf_bbox.extents([entity])
        if not extents.has_data:
            continue
        overflow = {
            "left": max(0.0, float(minimum.x) - float(extents.extmin.x)),
            "bottom": max(0.0, float(minimum.y) - float(extents.extmin.y)),
            "right": max(0.0, float(extents.extmax.x) - float(maximum.x)),
            "top": max(0.0, float(extents.extmax.y) - float(maximum.y)),
        }
        maximum_overflow = max(overflow.values())
        if maximum_overflow > tolerance:
            outside.append({
                "handle": str(getattr(entity.dxf, "handle", "") or ""),
                "entity_type": entity.dxftype(),
                "layer": layer,
                "maximum_overflow_mm": round(maximum_overflow, 4),
                "overflow_mm": {key: round(value, 4) for key, value in overflow.items()},
            })
    layers = Counter(item["layer"] for item in outside)
    return {
        "status": "WITHIN_BORDER" if not outside else "OUTSIDE_BORDER",
        "within_border": not outside,
        "border_bbox": [
            round(float(minimum.x), 4),
            round(float(minimum.y), 4),
            round(float(maximum.x), 4),
            round(float(maximum.y), 4),
        ],
        "outside_entity_count": len(outside),
        "outside_layer_counts": dict(layers),
        "maximum_overflow_mm": max((item["maximum_overflow_mm"] for item in outside), default=0.0),
        "outside_entities": outside[:50],
    }


def run_acceptance(
    model_name: str = DEFAULT_MODEL,
    manifest_path: Path = DEFAULT_MANIFEST,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    render: bool = True,
) -> Dict[str, Any]:
    pair = _find_pair(model_name, manifest_path)
    output_dir = output_root / model_name
    output_dir.mkdir(parents=True, exist_ok=True)
    shape = load_step(pair["step_path"])

    drawing_paths: Dict[str, str] = {}
    if render:
        dxf_path, pdf_path, png_path = generate_single(
            step_path=pair["step_path"],
            output_dir=str(output_dir),
            output_name=f"{model_name}_acceptance",
            part_name=model_name,
            drawing_no=model_name,
            revision=model_name.rsplit("-", 1)[-1] if "-" in model_name else "R00",
        )
        drawing_paths = {"dxf": dxf_path, "pdf": pdf_path, "png": png_path}

    view_data = ViewProjector().project_all_views(
        shape,
        view_names=["front", "top", "right", "left"],
    )
    candidate_rules = SmartAnnotationEngine().get_candidate_rules(shape, view_data)
    case_base = FeatureCaseBase()
    recommendation_result = ToleranceDecisionService(case_base=case_base).recommend_for_rules(
        shape=shape,
        candidate_rules=candidate_rules,
        view_data=view_data,
        product_family=FeatureCaseBase.infer_product_family(model_name),
    )
    recommendations = recommendation_result.get("recommendations") or {}
    source_validation = _validate_recommendation_sources(recommendations, case_base)
    historical_geometry = _verify_historical_geometry(pair)
    drawing_checks = {
        kind: bool(path and os.path.isfile(path) and os.path.getsize(path) > 0)
        for kind, path in drawing_paths.items()
    }
    layout_audit = (
        _audit_generated_dxf_layout(drawing_paths["dxf"])
        if render and drawing_checks.get("dxf")
        else {"status": "NOT_RUN", "within_border": None}
    )
    gates = {
        "real_step_loaded": not shape.IsNull(),
        "real_2d_outputs_created": bool(drawing_checks) and all(drawing_checks.values()) if render else True,
        "candidate_rules_created": len(candidate_rules) > 0,
        "historical_topology_cases_verified": historical_geometry["verified_historical_tolerance_count"] > 0,
        "recommendation_cites_existing_original_dxf": source_validation["valid_original_dxf_reference_count"] > 0,
        "adopted_recommendation_cites_existing_original_dxf": (
            source_validation["decision_reference_count"] == 0
            or source_validation["valid_decision_reference_count"] == source_validation["decision_reference_count"]
        ),
    }
    core_passed = all(gates.values())
    quality_warnings = []
    if layout_audit.get("within_border") is False:
        quality_warnings.append({
            "code": "GENERATED_ANNOTATIONS_OUTSIDE_BORDER",
            "message": "The legacy drawing layout places generated entities outside the sheet border.",
            "details": layout_audit,
        })
    report = {
        "schema_version": 1,
        "model": model_name,
        "status": (
            "FAILED" if not core_passed
            else "PASSED_WITH_WARNINGS" if quality_warnings
            else "PASSED"
        ),
        "gates": gates,
        "source_pair": {
            "pair_id": pair.get("pair_id"),
            "pair_method": pair.get("pair_method"),
            "step_path": pair.get("step_path"),
            "dxf_path": pair.get("dxf_path"),
        },
        "generated_drawings": drawing_paths,
        "generated_drawing_checks": drawing_checks,
        "generated_layout_audit": layout_audit,
        "quality_warnings": quality_warnings,
        "candidate_rule_count": len(candidate_rules),
        "recommendation_count": len(recommendations),
        "recommended_count": sum(
            item.get("decision_status") == "RECOMMENDED" for item in recommendations.values()
        ),
        "historical_geometry": historical_geometry,
        "recommendation_source_validation": source_validation,
        "recommendations": recommendations,
    }
    report_path = output_dir / "acceptance_report.json"
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
    report = run_acceptance(
        model_name=args.model,
        manifest_path=args.manifest,
        output_root=args.output_root,
        render=not args.skip_render,
    )
    print(json.dumps({
        "status": report["status"],
        "model": report["model"],
        "gates": report["gates"],
        "candidate_rule_count": report["candidate_rule_count"],
        "recommendation_count": report["recommendation_count"],
        "recommended_count": report["recommended_count"],
        "verified_historical_tolerance_count": report["historical_geometry"]["verified_historical_tolerance_count"],
        "valid_original_dxf_reference_count": report["recommendation_source_validation"]["valid_original_dxf_reference_count"],
        "report_path": report["report_path"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["status"].startswith("PASSED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
