import tempfile
import unittest
from pathlib import Path

from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase
from auto_2d_drawing.tolerance.feature_graph import FeatureNode
from auto_2d_drawing.tolerance.tolerance_decision_service import ToleranceDecisionService
from experimental_lab.tolerance_extraction_audit.common.scripts.rebuild_and_deploy_tolerance_case_base import (
    build_cases,
)


class ProductionCaseRebuildTests(unittest.TestCase):
    def test_current_final_statuses_map_to_web_schema(self):
        extraction = {
            "drawings": [{
                "is_canonical_drawing_occurrence": True,
                "dxf_path": "C:/sample.dxf",
                "dimensions": [{
                    "entity_handle": "A1",
                    "raw_text": "10±0.1",
                    "layer": "DIM",
                    "points": {},
                    "extraction_confidence": 0.9,
                }],
            }],
        }
        feature_report = {
            "tolerances": [{
                "family": "TEST",
                "drawing": "sample.dxf",
                "dxf_path": "C:/sample.dxf",
                "entity_handle": "A1",
                "nominal_value": 10.0,
                "dimension_category": "LINEAR",
                "tolerance_config": {"mode": "CUSTOM_SYMMETRIC", "dev": 0.1},
                "status": "CANDIDATE_ONLY",
                "feature_id": "planar_span_x_1",
                "feature_type": "composite_planar_span",
                "feature_role": "PLANAR_FACE_SPAN",
                "nominal_field": "length",
                "confidence": 0.72,
                "reason": "review",
                "case_retrieval": {"tier": "REVIEW_REQUIRED"},
            }],
        }
        cases, summary = build_cases(extraction, feature_report, [])
        self.assertEqual(1, summary["generated_case_count"])
        self.assertEqual("REVIEW_CANDIDATE", cases[0]["verification_status"])
        self.assertEqual("composite_planar_span", cases[0]["feature_type"])
        self.assertTrue(cases[0]["source_metadata"]["engineer_review_required"])

    def test_review_candidate_can_only_produce_review_required_suggestion(self):
        with tempfile.TemporaryDirectory() as directory:
            case_base = FeatureCaseBase(str(Path(directory) / "empty.json"))
        case_base.cases = [ToleranceCase(
            case_id="CANDIDATE_1",
            part_type="GENERAL",
            feature_type="composite_planar_span",
            inferred_role="PLANAR_FACE_SPAN",
            nominal_dimensions={"length": 10.0},
            neighbor_types=[],
            boundary_position="INTERIOR",
            tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.1},
            confidence=0.72,
            evidence_source="sample.dxf",
            description="candidate",
            verification_status="REVIEW_CANDIDATE",
            source_metadata={
                "dimension_category": "LINEAR",
                "engineer_review_required": True,
            },
        )]
        service = ToleranceDecisionService(case_base=case_base)
        node = FeatureNode(
            id="planar_1",
            feature_type="composite_planar_span",
            nominal={"length": 10.0},
            axial_span=[0.0, 10.0],
            center_axial=5.0,
            inferred_role="PLANAR_FACE_SPAN",
        )
        recommendation = service._evaluate_recommendation(
            "rule_1", "general", False, 10.0, node, "GENERAL",
            dimension_category="LINEAR",
        )
        self.assertEqual("TIER_REVIEW_CANDIDATE", recommendation.tier_level)
        self.assertEqual("REVIEW_REQUIRED", recommendation.decision_status)
        self.assertLessEqual(recommendation.confidence, 0.55)


if __name__ == "__main__":
    unittest.main()
