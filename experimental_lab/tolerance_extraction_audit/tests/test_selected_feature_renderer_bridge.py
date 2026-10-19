import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.selected_feature_renderer_bridge import (
    bridge_selected_features,
    build_relational_feature_candidates,
)


class SelectedFeatureRendererBridgeTests(unittest.TestCase):
    def setUp(self):
        self.fan = {
            "center": [10.0, 20.0],
            "view": "front",
            "verification_status": "REVIEW_CANDIDATE",
            "angular_render_tasks": [{
                "id": "angle_20",
                "start_angle": 10.0,
                "end_angle": 30.0,
                "angle": 20.0,
                "radius": 50.0,
            }],
        }
        self.holes = {
            "resolved": True,
            "view": "front",
            "verification_status": "AUTO_VERIFIED",
            "confidence": 0.96,
            "diameter": 4.0,
            "horizontal_center_distance": 100.0,
            "vertical_center_distance": 80.0,
            "centers": [[0, 0], [100, 0], [0, 80], [100, 80]],
            "topology_refs": ["face:1", "face:2", "face:3", "face:4"],
        }
        self.candidates = build_relational_feature_candidates(self.fan, self.holes)

    def test_candidates_are_disabled_until_engineer_selects_them(self):
        self.assertTrue(all(not item["enabled"] for item in self.candidates))

    def test_selecting_angle_emits_only_angular_task(self):
        result = bridge_selected_features(self.candidates, [{
            "feature_id": "hub_radial_relation.angle_20",
            "enabled": True,
            "views": ["front"],
        }])
        self.assertEqual(result["render_task_count"], 1)
        self.assertEqual(result["render_tasks"][0]["renderer"], "ANGULAR_RELATION_RENDERER")

    def test_selecting_hole_pattern_emits_diameter_and_two_distances(self):
        result = bridge_selected_features(self.candidates, [{
            "feature_id": "mounting_hole_pattern.rectangular_001",
            "enabled": True,
        }])
        self.assertEqual(result["render_task_count"], 3)
        self.assertEqual(
            {item["semantic"] for item in result["render_tasks"]},
            {
                "mounting_hole_pattern.hole_diameter",
                "mounting_hole_pattern.horizontal_center_distance",
                "mounting_hole_pattern.vertical_center_distance",
            },
        )

    def test_disabled_and_unknown_features_do_not_render(self):
        result = bridge_selected_features(self.candidates, [
            {"feature_id": "hub_radial_relation.angle_20", "enabled": False},
            {"feature_id": "missing", "enabled": True},
        ])
        self.assertEqual(result["render_task_count"], 0)
        self.assertEqual(
            {item["reason"] for item in result["rejected_selections"]},
            {"DISABLED_BY_ENGINEER", "UNKNOWN_FEATURE_ID"},
        )


if __name__ == "__main__":
    unittest.main()
