import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from auto_2d_drawing.canonical_features import extract_canonical_features
from auto_2d_drawing.tolerance.canonical_feature_graph import CanonicalFeatureGraphExtractor
from auto_2d_drawing.tolerance.case_base import FeatureCaseBase
from auto_2d_drawing.tolerance.engineer_case_ingestion import (
    EngineerCaseValidationError,
    EngineerConfirmedCaseService,
)
from auto_2d_drawing.tolerance.feature_graph import FeatureNode, FeatureRelationGraph
from auto_2d_drawing.tolerance.tolerance_decision_service import ToleranceDecisionService


class _FakeExtractor:
    def summary(self):
        return {
            "main_axis": "Z",
            "center": [0.0, 0.0, 0.0],
            "bounding_box": {"W": 3.0, "H": 3.0, "D": 20.0},
        }


def _feature_set():
    record = {
        "id": "journal_main",
        "type": "shaft_or_boss",
        "role": "bearing_journal",
        "nominal": {"diameter": 3.0, "length": 8.0},
        "geometry": {
            "kind": "cylinder",
            "center": [0.0, 0.0, 2.0],
            "size": [3.0, 3.0, 8.0],
            "diameter": 3.0,
            "length": 8.0,
            "axis": "Z",
        },
    }
    return SimpleNamespace(
        feature_extractor=_FakeExtractor(),
        part_type="SHAFT",
        records=[record],
    )


class CanonicalFeatureFlowTests(unittest.TestCase):
    @patch("auto_2d_drawing.canonical_features.build_feature_records")
    @patch("auto_2d_drawing.canonical_features.PartClassifier")
    @patch("auto_2d_drawing.canonical_features.FeatureExtractor")
    def test_entry_uses_production_extractor_and_record_builder(self, extractor_cls, classifier_cls, builder):
        extractor = extractor_cls.return_value
        classifier_cls.return_value.classify.return_value = "SHAFT"
        builder.return_value = [{"id": "journal_main"}]

        result = extract_canonical_features(object())

        extractor_cls.assert_called_once()
        classifier_cls.return_value.classify.assert_called_once_with(extractor, None)
        builder.assert_called_once_with(extractor, "SHAFT")
        self.assertEqual(result.records[0]["id"], "journal_main")

    @patch("auto_2d_drawing.canonical_features.build_feature_records")
    @patch("auto_2d_drawing.canonical_features.PartClassifier")
    @patch("auto_2d_drawing.canonical_features.FeatureExtractor")
    def test_entry_assigns_deterministic_ids_to_duplicate_records(self, _extractor, classifier_cls, builder):
        classifier_cls.return_value.classify.return_value = "GENERAL"
        builder.return_value = [{"id": "hole_01"}, {"id": "hole_01"}]

        result = extract_canonical_features(object())

        self.assertEqual([item["id"] for item in result.records], ["hole_01", "hole_01__02"])
        self.assertEqual(result.records[1]["canonical_source_id"], "hole_01")

    @patch(
        "auto_2d_drawing.tolerance.canonical_feature_graph.extract_canonical_features",
        return_value=_feature_set(),
    )
    def test_graph_preserves_web_feature_id_and_spatial_provenance(self, _extract):
        graph = CanonicalFeatureGraphExtractor().build_graph(object())
        node = graph.get_node("journal_main")

        self.assertIsNotNone(node)
        self.assertEqual(node.feature_type, "shaft_segment")
        self.assertEqual(node.source_info["canonical_feature_id"], "journal_main")
        self.assertEqual(node.source_info["axis_dir"], [0.0, 0.0, 1.0])
        self.assertEqual(node.source_info["radius"], 1.5)

    def test_rule_matching_prefers_exact_feature_id_over_equal_nominal(self):
        graph = FeatureRelationGraph("SHAFT")
        for node_id in ("left_journal", "right_journal"):
            graph.add_node(FeatureNode(
                id=node_id,
                feature_type="shaft_segment",
                nominal={"diameter": 3.0, "length": 8.0},
                axial_span=[0.0, 8.0],
                center_axial=4.0,
            ))
        service = object.__new__(ToleranceDecisionService)

        matched = service._match_rule_to_node({
            "category": "shaft",
            "nominal_value": 3.0,
            "canonical_feature_id": "right_journal",
        }, graph)

        self.assertEqual(matched.id, "right_journal")

    def test_engineer_confirmation_revalidates_and_stores_canonical_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            case_base = FeatureCaseBase(db_path=os.path.join(temp_dir, "cases.json"))
            service = EngineerConfirmedCaseService(case_base=case_base)
            with patch(
                "auto_2d_drawing.tolerance.canonical_feature_graph.extract_canonical_features",
                return_value=_feature_set(),
            ):
                case = service.confirm(
                    shape=object(),
                    model_id="model-1",
                    part_id="shaft-1",
                    feature_id="journal_main",
                    nominal_field="diameter",
                    nominal_value=3.0,
                    dimension_category="DIAMETER",
                    tolerance_config={"mode": "FIT", "fit_class": "h6"},
                    engineer_id="engineer-1",
                    drawing_file="shaft-1.dxf",
                )

            self.assertEqual(case.verification_status, "ENGINEER_VERIFIED")
            self.assertTrue(case.is_verified_extraction())
            self.assertEqual(case.source_metadata["canonical_feature_id"], "journal_main")
            self.assertEqual(case.source_metadata["matched_nominal_field"], "diameter")
            self.assertEqual(len(case_base.cases), 1)

    def test_engineer_confirmation_rejects_unknown_feature(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = EngineerConfirmedCaseService(
                case_base=FeatureCaseBase(db_path=os.path.join(temp_dir, "cases.json"))
            )
            with patch(
                "auto_2d_drawing.tolerance.canonical_feature_graph.extract_canonical_features",
                return_value=_feature_set(),
            ):
                with self.assertRaises(EngineerCaseValidationError):
                    service.confirm(
                        shape=object(),
                        model_id="model-1",
                        part_id="shaft-1",
                        feature_id="missing_feature",
                        nominal_field="diameter",
                        nominal_value=3.0,
                        dimension_category="DIAMETER",
                        tolerance_config={"mode": "FIT", "fit_class": "h6"},
                        engineer_id="engineer-1",
                    )


if __name__ == "__main__":
    unittest.main()
