import unittest

from auto_2d_drawing.tolerance.projection_registration_v2 import SimilarityTransform2D
from experimental_lab.tolerance_extraction_audit.common.scripts.replay_drawing_rules import (
    denormalise_point,
    format_rule_text,
    rule_step_anchors,
)


class DrawingRuleReplayTests(unittest.TestCase):
    def test_denormalise_point_restores_view_coordinates(self):
        self.assertEqual(denormalise_point((0.25, 0.75), (10.0, 20.0, 30.0, 60.0)), (15.0, 50.0))

    def test_rule_anchor_is_mapped_back_to_step_space(self):
        transform = SimilarityTransform2D(2.0, 0.0, (100.0, 50.0), mirrored=True)
        rule = {
            "view_bbox": [90.0, 40.0, 110.0, 60.0],
            "dimension": {"dim_type": "LINEAR"},
            "placement": {"definition_points_normalized": {"defpoint2": [0.0, 0.5], "defpoint3": [1.0, 0.5]}},
        }
        self.assertEqual(rule_step_anchors(rule, transform), [(5.0, 0.0), (-5.0, 0.0)])

    def test_rule_text_keeps_tolerance(self):
        rule = {
            "dimension": {
                "prefix": "Φ",
                "nominal_value": 3.0,
                "tolerance_config": {"mode": "CUSTOM_LIMITS", "upper_dev": 0.006, "lower_dev": -0.05},
            }
        }
        self.assertEqual(format_rule_text(rule), "Φ3 +0.006/-0.05")


if __name__ == "__main__":
    unittest.main()
