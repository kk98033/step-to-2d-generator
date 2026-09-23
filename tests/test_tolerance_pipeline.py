import unittest

import ezdxf

from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor
from auto_2d_drawing.tolerance.feature_graph import FeatureNode
from auto_2d_drawing.tolerance.feature_graph import candidate_feature_types_for_dimension
from auto_2d_drawing.tolerance.ingest_historical_data import HistoricalDataIngestor
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
    def test_raw_dxf_dimensions_remain_unresolved_in_feature_graph_taxonomy(self):
        item = DxfToleranceExtractor()._parse_dimension_entity(
            self._native_dimension_with_tolerance(),
            "sample.dxf",
        )
        feature_type, role, nominal, status = HistoricalDataIngestor._raw_feature_identity(item)

        self.assertEqual(feature_type, "unresolved_feature")
        self.assertEqual(role, "UNRESOLVED_LINEAR")
        self.assertEqual(nominal, {"length": 10.0})
        self.assertEqual(status, "AUTO_EXTRACTED")
        self.assertEqual(
            candidate_feature_types_for_dimension("RADIUS"),
            ["transition_fillet"],
        )

    @staticmethod
    def _native_dimension_with_tolerance():
        doc = ezdxf.new()
        override = doc.modelspace().add_linear_dim(
            base=(0, 2),
            p1=(0, 0),
            p2=(10, 0),
            override={"dimtol": 1, "dimtp": 0.1, "dimtm": 0.1},
        )
        override.render()
        return override.dimension

    def test_retrieved_candidate_is_not_marked_adopted_on_fallback(self):
        case = ToleranceCase(
            case_id="low-similarity",
            part_type="SHAFT",
            feature_type="shaft_segment",
            inferred_role="SHAFT_SEGMENT_DIAMETER",
            nominal_dimensions={"diameter": 10.0, "length": 2.0},
            neighbor_types=[],
            boundary_position="INTERIOR",
            tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.02},
            confidence=0.95,
            evidence_source="company.dxf",
            description="verified company case",
            verification_status="AUTO_VERIFIED",
        )
        match = {
            "case": case,
            "similarity": 0.59,
            "score_breakdown": {},
            "verification_status": "AUTO_VERIFIED",
            "product_family": "TEST",
            "same_product_family": True,
        }

        class StubCaseBase:
            cases = [case]

            @staticmethod
            def search_similar_cases_detailed(*_args, **_kwargs):
                return [match]

        node = FeatureNode(
            id="shaft-1",
            feature_type="shaft_segment",
            nominal={"diameter": 10.0, "length": 2.0},
            axial_span=[0.0, 2.0],
            center_axial=1.0,
            inferred_role="BEARING_JOURNAL",
        )
        result = ToleranceDecisionService(case_base=StubCaseBase())._evaluate_recommendation(
            "journal_main", "shaft", True, 10.0, node, "SHAFT"
        ).to_dict()

        self.assertEqual(result["retrieval_trace"]["decision_source"], "GENERAL_FALLBACK")
        self.assertFalse(result["evidence_cases"][0]["used_for_decision"])
        self.assertFalse(result["evidence_cases"][0]["used_as_context"])
        self.assertEqual(result["evidence_cases"][0]["evidence_role"], "RETRIEVED_CANDIDATE")

    def test_high_similarity_historical_case_is_explicitly_marked_adopted(self):
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        base.cases = [
            ToleranceCase(
                case_id="adopted-case",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 10.0, "length": 8.0},
                neighbor_types=["locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.02},
                confidence=0.95,
                evidence_source="1FQ6H3010H-R01.dxf",
                description="verified company case",
                verification_status="ENGINEER_VERIFIED",
            )
        ]
        node = FeatureNode(
            id="shaft-1",
            feature_type="shaft_segment",
            nominal={"diameter": 10.0, "length": 8.0},
            axial_span=[0.0, 8.0],
            center_axial=4.0,
            neighbor_types=["locating_shoulder"],
            inferred_role="BEARING_JOURNAL",
        )
        result = ToleranceDecisionService(case_base=base)._evaluate_recommendation(
            "journal_main", "shaft", True, 10.0, node, "SHAFT", product_family="FQ6H"
        ).to_dict()

        self.assertEqual(result["retrieval_trace"]["decision_source"], "HISTORICAL_CASE")
        self.assertEqual(result["retrieval_trace"]["adopted_case_id"], "adopted-case")
        self.assertTrue(result["evidence_cases"][0]["used_for_decision"])
        self.assertEqual(result["evidence_cases"][0]["evidence_role"], "ADOPTED_HISTORICAL_CASE")

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
