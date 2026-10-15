import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.run_full_classification_audit import (
    _bbox_metrics,
    route_rule,
)


class FullClassificationAuditTests(unittest.TestCase):
    def test_bbox_metrics_detect_outside_projection(self):
        metrics = _bbox_metrics((20, 20, 30, 30), (0, 0, 10, 10))
        self.assertEqual(metrics["intersection_ratio"], 0.0)

    def test_route_rule_reports_missing_projection(self):
        views = {"front": {"bbox": (0, 0, 10, 10)}}
        result = route_rule({"dim_type": "DIAMETER", "view_geometry": {}}, views)
        self.assertEqual(result["status"], "UNRESOLVED_NO_PROJECTION")

    def test_route_rule_prefers_valid_recommended_view(self):
        views = {
            "front": {"bbox": (0, 0, 10, 10)},
            "right": {"bbox": (0, 0, 10, 10)},
        }
        rule = {
            "category": "hole",
            "dim_type": "DIAMETER",
            "nominal_value": 2.0,
            "target_views": ["front"],
            "view_geometry": {
                "front": {"projected_feature_bbox": [4, 4, 6, 6], "start_proj": [4, 5], "end_proj": [6, 5]},
                "right": {"projected_feature_bbox": [4, 4, 6, 6], "start_proj": [4, 5], "end_proj": [6, 5]},
            },
        }
        result = route_rule(rule, views)
        self.assertEqual(result["view"], "front")
        self.assertEqual(result["status"], "ROUTED")

    def test_local_feature_covering_whole_view_requires_review(self):
        views = {"front": {"bbox": (0, 0, 10, 10)}}
        rule = {
            "category": "hole",
            "dim_type": "DIAMETER",
            "nominal_value": 9.0,
            "target_views": ["front"],
            "view_geometry": {
                "front": {"projected_feature_bbox": [0.5, 0.5, 9.5, 9.5], "start_proj": [0.5, 5], "end_proj": [9.5, 5]},
            },
        }
        result = route_rule(rule, views)
        self.assertEqual(result["status"], "REVIEW_OVERSIZED_BBOX")


if __name__ == "__main__":
    unittest.main()
