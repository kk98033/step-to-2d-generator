import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.run_view_plan_experiment import (
    score_feature_coverage,
    solve_minimum_view_plan,
)


def candidate(view_id, kind="ORTHOGRAPHIC", base_view=None, quality=1.0):
    return {
        "id": view_id,
        "kind": kind,
        "base_view": base_view or view_id,
        "cost": 1.25 if kind == "HALF_SECTION" else 1.0,
        "quality": quality,
        "section_spec": {} if kind == "HALF_SECTION" else None,
    }


class ViewPlanExperimentTests(unittest.TestCase):
    def test_internal_feature_rejects_orthographic_view(self):
        rule = {"view_geometry": {"front": {"projected_feature_bbox": [0, 0, 10, 10]}}}
        score, reasons = score_feature_coverage(rule, {"requires_section": True}, candidate("front"))
        self.assertEqual(score, 0.0)
        self.assertIn("INTERNAL_FEATURE_REQUIRES_SECTION", reasons)

    def test_edge_on_pattern_prefers_face_view(self):
        rule = {
            "dim_type": "HOLE_PATTERN",
            "target_views": ["front", "top"],
            "view_geometry": {
                "front": {"projected_feature_bbox": [-5, -5, 5, 5]},
                "top": {"projected_feature_bbox": [-1, -5, 1, 5]},
            },
        }
        front, _ = score_feature_coverage(rule, {}, candidate("front"))
        top, _ = score_feature_coverage(rule, {}, candidate("top"))
        self.assertGreater(front, top)

    def test_solver_uses_one_view_when_it_covers_all_features(self):
        rules = {
            "overall": {
                "category": "overall", "dim_type": "LINEAR", "nominal_value": 10,
                "target_views": ["front"],
                "view_geometry": {"front": {"projected_feature_bbox": [0, 0, 10, 2], "start_proj": [0, 0], "end_proj": [10, 0]}},
            },
            "diameter": {
                "category": "shaft", "dim_type": "DIAMETER", "nominal_value": 2,
                "target_views": ["front"],
                "view_geometry": {"front": {"projected_feature_bbox": [0, 0, 2, 2], "start_proj": [0, 0], "end_proj": [0, 2]}},
            },
        }
        result = solve_minimum_view_plan(rules, [{"feature_id": "overall"}, {"feature_id": "diameter"}], [candidate("front")])
        self.assertEqual(result["status"], "PLANNED")
        self.assertEqual([item["id"] for item in result["selected_views"]], ["front"])

    def test_solver_combines_face_and_section_views(self):
        rules = {
            "outer": {"category": "overall", "dim_type": "LINEAR", "nominal_value": 10, "target_views": ["front"], "view_geometry": {"front": {"projected_feature_bbox": [0, 0, 10, 2], "start_proj": [0, 0], "end_proj": [10, 0]}, "right": {"projected_feature_bbox": [0, 0, 2, 2], "start_proj": [0, 0], "end_proj": [2, 0]}}},
            "inner": {"category": "fillet", "dim_type": "LEADER", "nominal_value": 1, "target_views": ["right"], "view_geometry": {"front": {"projected_feature_bbox": [0, 0, 1, 1]}, "right": {"projected_feature_bbox": [0, 0, 1, 1]}}},
        }
        result = solve_minimum_view_plan(
            rules,
            [{"feature_id": "outer"}, {"feature_id": "inner", "requires_section": True}],
            [candidate("front"), candidate("section", "HALF_SECTION", "right", 0.9)],
        )
        self.assertEqual(result["status"], "PLANNED")
        self.assertEqual({item["kind"] for item in result["selected_views"]}, {"ORTHOGRAPHIC", "HALF_SECTION"})


if __name__ == "__main__":
    unittest.main()
