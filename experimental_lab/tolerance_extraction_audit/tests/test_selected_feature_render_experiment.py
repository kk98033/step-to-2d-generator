import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.run_selected_feature_render_experiment import (
    validate_selection,
)


class SelectedFeatureRenderContractTests(unittest.TestCase):
    def test_exact_linear_anchor_passes(self):
        rule = {
            "dim_type": "LINEAR",
            "nominal_value": 10.0,
            "target_views": ["front"],
            "view_geometry": {
                "front": {
                    "start_proj": [0.0, 0.0],
                    "end_proj": [10.0, 0.0],
                    "projected_feature_bbox": [0.0, -1.0, 10.0, 1.0],
                }
            },
        }
        result = validate_selection(rule, "front", {"bbox": [-2.0, -2.0, 12.0, 2.0]})
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["suggested_view"], "front")

    def test_bbox_span_cannot_impersonate_wall_thickness(self):
        rule = {
            "dim_type": "LINEAR",
            "nominal_value": 0.2,
            "target_views": ["right"],
            "view_geometry": {
                "right": {
                    "start_proj": [0.0, -17.5],
                    "end_proj": [0.0, 17.5],
                    "projected_feature_bbox": [-0.25, -17.5, 0.25, 17.5],
                }
            },
        }
        result = validate_selection(rule, "right", {"bbox": [-60.0, -60.0, 60.0, 60.0]})
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("PROJECTED_SPAN_CANNOT_REPRESENT_NOMINAL", result["errors"])

    def test_edge_on_pattern_recommends_face_view(self):
        rule = {
            "dim_type": "HOLE_PATTERN",
            "nominal_value": 100.0,
            "nominal": {"pcd": 100.0},
            "target_views": ["front", "top"],
            "view_geometry": {
                "front": {"center": [0.0, 0.0], "projected_feature_bbox": [-50.0, -50.0, 50.0, 50.0]},
                "top": {"center": [0.0, 0.0], "projected_feature_bbox": [-2.0, -50.0, 2.0, 50.0]},
            },
        }
        result = validate_selection(rule, "top", {"bbox": [-60.0, -60.0, 60.0, 60.0]})
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["suggested_view"], "front")
        self.assertIn("PATTERN_EDGE_ON_IN_SELECTED_VIEW", result["errors"])


if __name__ == "__main__":
    unittest.main()
