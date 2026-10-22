import unittest

from auto_2d_drawing.smart_extractors.experimental_candidate_adapter import (
    build_experimental_candidate_rules,
    build_experimental_selection_config,
    candidate_summary,
)
from auto_2d_drawing.smart_extractors.experimental_family_policy import (
    apply_experimental_family_policies,
)


def _intent(intent_id: str, status: str = "AUTO_VERIFIED"):
    return {
        "intent_id": intent_id,
        "source_candidates": [{
            "extractor": "ANNULAR_COMPOSITE_V1",
            "dimension_id": "annular-1.outer_diameter",
            "feature_id": "annular-1",
        }],
        "feature": {
            "family": "ANNULAR_SECTION",
            "feature_id": "annular-1",
            "topology_refs": ["face:1"],
        },
        "measurement": {
            "semantic": "annular_section.outer_diameter",
            "role": "OUTER_DIAMETER",
            "kind": "DIAMETER",
            "nominal_value": 32.0,
            "unit": "mm",
        },
        "projection": {
            "preferred_view": "front",
            "anchors": [[-16.0, 0.0], [16.0, 0.0]],
        },
        "renderer_contract": {"renderer": "DIAMETER_RENDERER"},
        "relationships": {"related_feature_ids": ["face:1"]},
        "verification": {
            "status": status,
            "confidence": 0.95,
            "issues": [] if status != "INVALID" else ["INVALID_ANCHORS"],
        },
    }


class ExperimentalCandidateAdapterTests(unittest.TestCase):
    def test_adapts_verified_intents_to_renderable_routes(self):
        report = {
            "method": "DRAWING_EXPERIMENTAL_MAIN_V0_26",
            "mode": "ANALYSIS_ONLY",
            "dimension_intents": {
                "intents": [_intent("diameter-1"), _intent("invalid-1", "INVALID")],
            },
            "detectors": {
                "radial_pattern_bands": {
                    "best_view": {
                        "confidence": 0.9,
                        "candidate_rules": [{
                            "rule_id": "outer_annular_slots.full_angle",
                            "name": "Outer slot gap 20 deg",
                            "category": "pattern_angle",
                            "dim_type": "ANGULAR",
                            "nominal_value": 20.0,
                            "preferred_view": "front",
                            "target_views": ["front"],
                        }],
                    },
                },
                "rectangular_hole_relations": {
                    "resolved": True,
                    "view": "front",
                    "confidence": 0.92,
                    "intents": [{
                        "semantic": "mounting_hole_pattern.horizontal_center_distance",
                        "nominal_value": 71.5,
                        "unit": "mm",
                        "topology_refs": ["face:2", "face:3"],
                    }],
                },
            },
        }

        rules = build_experimental_candidate_rules(report)
        self.assertEqual(len(rules), 3)
        self.assertNotIn("invalid-1", {rule["rule_id"] for rule in rules})
        self.assertTrue(all(rule["source_engine"] == "experimental_v026" for rule in rules))
        self.assertTrue(all(rule["render_eligible"] is True for rule in rules))
        diameter = next(rule for rule in rules if rule["rule_id"] == "diameter-1")
        self.assertEqual(diameter["geometry_payload"]["start_proj"], [-16.0, 0.0])
        self.assertEqual(diameter["default_prefix"], "%%c")
        self.assertEqual(diameter["experimental_selection_group"], "annular_selections")
        self.assertEqual(diameter["experimental_selection_id"], "annular-1.outer_diameter")
        summary = candidate_summary(rules)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["render_eligible"], 3)

        submitted = [{**diameter, "enabled": True, "side": "LEFT"}]
        config = build_experimental_selection_config(
            rules,
            submitted,
            part_hint="annular fixture",
        )
        self.assertEqual(config["selection_summary"]["routed"], 1)
        self.assertEqual(
            config["annular_selections"][0]["feature_id"],
            "annular-1.outer_diameter",
        )
        self.assertEqual(config["annular_selections"][0]["side"], "LEFT")

    def test_rejects_unknown_experimental_method(self):
        with self.assertRaisesRegex(ValueError, "Unsupported Experimental Main method"):
            build_experimental_candidate_rules({
                "method": "DRAWING_EXPERIMENTAL_MAIN_V9",
                "mode": "ANALYSIS_ONLY",
            })

    def test_shaft_policy_assigns_left_right_baselines_and_overall_rank(self):
        rules = apply_experimental_family_policies([
            {
                "rule_id": "left",
                "feature_family": "SOLID_SHAFT",
                "shaft_chain_id": "shaft-1",
                "measurement_role": "AXIAL_SEGMENT_LENGTH",
                "geometry_payload": {"start_proj": [0, 0], "end_proj": [2, 0]},
            },
            {
                "rule_id": "right",
                "feature_family": "SOLID_SHAFT",
                "shaft_chain_id": "shaft-1",
                "measurement_role": "AXIAL_SEGMENT_LENGTH",
                "geometry_payload": {"start_proj": [8, 0], "end_proj": [10, 0]},
            },
            {
                "rule_id": "overall",
                "feature_family": "SOLID_SHAFT",
                "shaft_chain_id": "shaft-1",
                "measurement_role": "AXIAL_OVERALL_LENGTH",
                "geometry_payload": {"start_proj": [0, 0], "end_proj": [10, 0]},
            },
        ])
        by_id = {rule["rule_id"]: rule for rule in rules}
        self.assertEqual(by_id["left"]["baseline"], "LEFT")
        self.assertEqual(by_id["right"]["baseline"], "RIGHT")
        self.assertEqual(by_id["overall"]["rank"], 2)
        self.assertGreater(by_id["overall"]["policy_lane"], by_id["left"]["policy_lane"])


if __name__ == "__main__":
    unittest.main()
