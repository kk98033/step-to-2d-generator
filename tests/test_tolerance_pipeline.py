import unittest

import ezdxf

from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor
from auto_2d_drawing.tolerance.feature_graph import FeatureNode
from auto_2d_drawing.tolerance.tolerance_decision_service import ToleranceDecisionService


class DxfToleranceExtractorTests(unittest.TestCase):
    def setUp(self):
        self.extractor = DxfToleranceExtractor()
        self.doc = ezdxf.new()
        self.msp = self.doc.modelspace()

    def test_tolerance_only_text_is_drawing_default(self):
        entity = self.msp.add_text("%%P0.25", dxfattribs={"layer": "TITLE_BLOCK"})
        item = self.extractor._parse_text_entity(entity, "sample.dxf")

        self.assertIsNotNone(item)
        self.assertEqual(item.validation_status, "DRAWING_DEFAULT")
        self.assertFalse(item.is_feature_dimension)
        self.assertEqual(item.nominal_value, 0.0)

    def test_note_with_unit_is_rejected(self):
        entity = self.msp.add_text("2.73Kg%%P10%%%")
        item = self.extractor._parse_text_entity(entity, "sample.dxf")

        self.assertIsNotNone(item)
        self.assertEqual(item.validation_status, "REJECTED")
        self.assertFalse(item.is_feature_dimension)

    def test_dimension_geometry_and_dimstyle_tolerance_are_read(self):
        override = self.msp.add_linear_dim(
            base=(0, 2),
            p1=(0, 0),
            p2=(10, 0),
            override={"dimtol": 1, "dimtp": 0.2, "dimtm": 0.1},
        )
        override.render()
        item = self.extractor._parse_dimension_entity(override.dimension, "sample.dxf")

        self.assertAlmostEqual(item.nominal_value, 10.0)
        self.assertEqual(item.tolerance_config["source"], "DXF_DIMSTYLE")
        self.assertAlmostEqual(item.tolerance_config["upper_dev"], 0.2)
        self.assertAlmostEqual(item.tolerance_config["lower_dev"], -0.1)
        self.assertEqual(item.validation_status, "AUTO_VALIDATED")


class CaseRetrievalTests(unittest.TestCase):
    def test_only_verified_or_strictly_auto_verified_cases_are_eligible(self):
        base_kwargs = dict(
            case_id="status-check",
            part_type="SHAFT",
            feature_type="shaft_segment",
            inferred_role="SHAFT_DIAMETER",
            nominal_dimensions={"diameter": 10.0},
            neighbor_types=[],
            boundary_position="INTERIOR",
            tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.02},
            confidence=0.9,
            evidence_source="company.dxf",
            description="status check",
        )
        for status in ("ENGINEER_VERIFIED", "AUTO_VERIFIED"):
            self.assertTrue(ToleranceCase(**base_kwargs, verification_status=status).is_retrieval_eligible())
        for status in ("AUTO_EXTRACTED", "UNVERIFIED", "SEED_REFERENCE"):
            self.assertFalse(ToleranceCase(**base_kwargs, verification_status=status).is_retrieval_eligible())

    def test_retrieval_normalizes_feature_type_and_excludes_unverified(self):
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        base.cases = [
            ToleranceCase(
                case_id="verified",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 3.0, "length": 8.0},
                neighbor_types=["locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "h6"},
                confidence=0.9,
                evidence_source="drawing-a",
                description="verified case",
                verification_status="ENGINEER_VERIFIED",
            ),
            ToleranceCase(
                case_id="unverified",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 3.0, "length": 8.0},
                neighbor_types=["locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "p6"},
                confidence=0.99,
                evidence_source="drawing-b",
                description="unverified case",
            ),
        ]
        node = FeatureNode(
            id="cylinder-1",
            feature_type="cylinder",
            nominal={"diameter": 3.0, "length": 8.0},
            axial_span=[0.0, 8.0],
            center_axial=4.0,
            neighbor_types=["locating_shoulder"],
            inferred_role="BEARING_JOURNAL",
        )

        matches = base.search_similar_cases_detailed(node, part_type="SHAFT")

        self.assertEqual([item["case"].case_id for item in matches], ["verified"])
        self.assertEqual(matches[0]["score_breakdown"]["feature_type"], 1.0)

    def test_step_length_does_not_adopt_bearing_fit_case(self):
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        base.cases = [
            ToleranceCase(
                case_id="bearing-diameter",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 3.0, "length": 0.9},
                neighbor_types=["locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "h6"},
                confidence=0.95,
                evidence_source="drawing-bearing",
                description="bearing diameter case",
                verification_status="ENGINEER_VERIFIED",
            )
        ]
        node = FeatureNode(
            id="shaft-1",
            feature_type="shaft_segment",
            nominal={"diameter": 3.0, "length": 0.9},
            axial_span=[0.0, 0.9],
            center_axial=0.45,
            neighbor_types=["locating_shoulder"],
            inferred_role="BEARING_JOURNAL",
        )
        service = ToleranceDecisionService(case_base=base)

        result = service._evaluate_recommendation(
            "step_len_1", "step", False, 0.9, node, "SHAFT"
        ).to_dict()

        self.assertEqual(result["tier_level"], "TIER_3_GENERAL_FALLBACK")
        self.assertEqual(result["recommended_mode"], "NONE")
        self.assertEqual(result["confidence"], 0.35)
        self.assertEqual(result["retrieval_trace"]["retrieved_case_count"], 1)
        self.assertEqual(result["retrieval_trace"]["compatible_case_count"], 0)
        self.assertFalse(result["evidence_cases"][0]["used_for_decision"])

    def test_same_product_family_is_ranked_before_cross_family(self):
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        common = dict(
            part_type="SHAFT",
            feature_type="shaft_segment",
            inferred_role="BEARING_JOURNAL",
            nominal_dimensions={"diameter": 3.0, "length": 8.0},
            neighbor_types=["locating_shoulder"],
            boundary_position="INTERIOR",
            tolerance_config={"mode": "FIT", "fit_class": "h6"},
            confidence=0.8,
            description="company case",
        )
        base.cases = [
            ToleranceCase(case_id="cross", evidence_source="1AJ0A3010H-R01.dxf", **common),
            ToleranceCase(case_id="same", evidence_source="1FQ6H3010H-R01.dxf", **common),
        ]
        node = FeatureNode(
            id="shaft-1",
            feature_type="shaft_segment",
            nominal={"diameter": 3.0, "length": 8.0},
            axial_span=[0.0, 8.0],
            center_axial=4.0,
            neighbor_types=["locating_shoulder"],
            inferred_role="BEARING_JOURNAL",
        )

        matches = base.search_similar_cases_detailed(
            node,
            part_type="SHAFT",
            include_unverified=True,
            product_family="FQ6H",
        )

        self.assertEqual(matches[0]["case"].case_id, "same")
        self.assertTrue(matches[0]["same_product_family"])
        self.assertGreater(
            matches[0]["score_breakdown"]["product_family"],
            matches[1]["score_breakdown"]["product_family"],
        )


if __name__ == "__main__":
    unittest.main()
