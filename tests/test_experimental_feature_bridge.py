import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from auto_2d_drawing.smart_extractors.experimental_feature_bridge import (
    bridge_experimental_candidate_records,
)
from auto_2d_drawing.tolerance.tolerance_decision_service import ToleranceDecisionService


class ExperimentalFeatureBridgeTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "id": "hole_32",
                "type": "hole",
                "role": "bore",
                "nominal": {"diameter": 32.0, "length": 4.0},
                "geometry": {
                    "kind": "cylinder",
                    "center": [0.0, 0.0, 0.0],
                    "size": [32.0, 32.0, 4.0],
                    "diameter": 32.0,
                    "length": 4.0,
                },
            },
            {
                "id": "shaft_35",
                "type": "shaft_or_boss",
                "role": "hub",
                "nominal": {"diameter": 35.0, "length": 5.0},
                "geometry": {
                    "kind": "cylinder",
                    "center": [0.0, 0.0, 0.5],
                    "size": [35.0, 35.0, 5.0],
                    "diameter": 35.0,
                    "length": 5.0,
                },
            },
        ]

    @staticmethod
    def _rule(rule_id, role, nominal, *, kind="DIMENSION_INTENT", dim_type="DIAMETER"):
        return {
            "id": rule_id,
            "rule_id": rule_id,
            "canonical_feature_id": "experimental-local-id",
            "feature_id": "experimental-local-id",
            "candidate_kind": kind,
            "measurement_role": role,
            "feature_family": "ANNULAR_SECTION",
            "dim_type": dim_type,
            "nominal_value": nominal,
        }

    def test_single_feature_rules_receive_shared_canonical_identity_and_geometry(self):
        rules, summary = bridge_experimental_candidate_records(
            [
                self._rule("inner", "INNER_DIAMETER", 32.0),
                self._rule("outer", "OUTER_DIAMETER", 35.0),
            ],
            self.records,
            part_type="FAN_IMPELLER",
        )
        by_id = {rule["rule_id"]: rule for rule in rules}
        self.assertEqual(by_id["inner"]["canonical_feature_id"], "hole_32")
        self.assertEqual(by_id["outer"]["canonical_feature_id"], "shaft_35")
        self.assertEqual(by_id["inner"]["experimental_feature_id"], "experimental-local-id")
        self.assertEqual(by_id["inner"]["geometry"]["center"], [0.0, 0.0, 0.0])
        self.assertTrue(by_id["inner"]["tolerance_eligible"])
        self.assertEqual(summary["matched_count"], 2)
        self.assertEqual(summary["geometry_ready_count"], 2)

    def test_relationship_rules_get_dynamic_group_geometry_without_false_frg_id(self):
        wall = self._rule(
            "wall",
            "RADIAL_WALL_THICKNESS",
            1.5,
            dim_type="LINEAR",
        )
        angle = self._rule(
            "angle",
            "",
            20.0,
            kind="RADIAL_PATTERN_RELATION",
            dim_type="ANGULAR",
        )
        rules, summary = bridge_experimental_candidate_records(
            [wall, angle], self.records, part_type="FAN_IMPELLER"
        )
        for rule in rules:
            self.assertNotIn("canonical_feature_id", rule)
            self.assertEqual(rule["geometry"]["kind"], "virtual_relation_group")
            self.assertFalse(rule["tolerance_eligible"])
            self.assertEqual(
                rule["tolerance_block_reason"],
                "RELATION_NODE_NOT_AVAILABLE_IN_FRG",
            )
        self.assertEqual(summary["virtual_relation_count"], 2)

    def test_unresolved_rule_does_not_reuse_experimental_id_as_canonical_id(self):
        rules, summary = bridge_experimental_candidate_records(
            [self._rule("missing", "INNER_DIAMETER", 99.0)],
            self.records,
        )
        self.assertNotIn("canonical_feature_id", rules[0])
        self.assertFalse(rules[0]["tolerance_eligible"])
        self.assertEqual(rules[0]["feature_bridge"]["status"], "UNRESOLVED")
        self.assertEqual(summary["unresolved_count"], 1)

    def test_radial_pattern_uses_detected_radius_on_canonical_axis(self):
        records = self.records + [
            {
                "id": "axis",
                "type": "datum",
                "geometry": {
                    "kind": "axis",
                    "axis": "Z",
                    "center": [10.0, 20.0, 30.0],
                    "size": [0.3, 0.3, 8.0],
                },
            },
            {
                "id": "overall",
                "type": "overall_size",
                "geometry": {
                    "kind": "bbox",
                    "center": [10.0, 20.0, 30.0],
                    "size": [80.0, 70.0, 8.0],
                },
            },
        ]
        angle = self._rule(
            "angle",
            "",
            20.0,
            kind="RADIAL_PATTERN_RELATION",
            dim_type="ANGULAR",
        )
        angle["pattern_reference_radius"] = 12.0
        rules, _summary = bridge_experimental_candidate_records([angle], records)
        geometry = rules[0]["geometry"]
        self.assertEqual(geometry["center"], [10.0, 20.0, 30.0])
        self.assertEqual(geometry["size"][:2], [24.0, 24.0])
        self.assertEqual(geometry["axis"], "Z")

    def test_tolerance_service_explicitly_skips_relation_without_querying_cases(self):
        case_base = Mock()
        service = ToleranceDecisionService(case_base=case_base)
        service.frg_extractor = Mock()
        service.frg_extractor.build_graph.return_value = SimpleNamespace(
            part_type="FAN_IMPELLER",
            to_dict=lambda: {"nodes": []},
        )
        result = service.recommend_for_rules(
            shape=object(),
            candidate_rules=[{
                "rule_id": "angle",
                "category": "pattern_angle",
                "candidate_kind": "RADIAL_PATTERN_RELATION",
                "dim_type": "ANGULAR",
                "nominal_value": 20.0,
                "tolerance_eligible": False,
                "tolerance_block_reason": "RELATION_NODE_NOT_AVAILABLE_IN_FRG",
            }],
        )
        recommendation = result["recommendations"]["angle"]
        self.assertEqual(recommendation["recommended_mode"], "NONE")
        self.assertEqual(recommendation["tier_level"], "NOT_APPLICABLE")
        self.assertEqual(
            recommendation["retrieval_trace"]["decision_source"],
            "FEATURE_BRIDGE_GUARD",
        )
        case_base.search_similar_cases_detailed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
