import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.minimal_multiview_drawing import (
    _adapt_rules_to_single_view,
    choose_half_section_candidate,
    configure_engineer_selections,
)


class MinimalMultiviewDrawingTests(unittest.TestCase):
    def test_configures_only_explicit_engineer_selections(self):
        candidates = [
            {"rule_id": "feature.front", "preferred_view": "front", "default_tolerance": ""},
            {"rule_id": "feature.top", "preferred_view": "top", "default_tolerance": ""},
        ]
        configured, missing = configure_engineer_selections(candidates, [
            {"feature_id": "feature.top", "view": "top", "side": "LEFT", "tolerance": "+0.1/-0.1"},
        ])
        self.assertEqual(missing, [])
        self.assertEqual(len(configured), 1)
        self.assertEqual(configured[0]["rule_id"], "feature.top")
        self.assertEqual(configured[0]["target_views"], ["top"])
        self.assertEqual(configured[0]["tolerance"], "+0.1/-0.1")

    def test_reports_unknown_feature_without_inventing_rule(self):
        configured, missing = configure_engineer_selections([], [
            {"feature_id": "missing", "view": "front"},
        ])
        self.assertEqual(configured, [])
        self.assertEqual(missing, ["missing"])

    def test_selects_highest_quality_dynamic_half_section(self):
        selected = choose_half_section_candidate([
            {"id": "front", "kind": "ORTHOGRAPHIC", "quality": 1.0},
            {"id": "section-low", "kind": "HALF_SECTION", "quality": 0.4},
            {"id": "section-high", "kind": "HALF_SECTION", "quality": 0.8},
        ])
        self.assertEqual(selected["id"], "section-high")

    def test_reuses_prior_verified_half_section_specification(self):
        selected = choose_half_section_candidate([
            {
                "id": "negative",
                "kind": "HALF_SECTION",
                "base_view": "right",
                "quality": 0.9,
                "view_data": {"hidden": [1]},
                "section_spec": {"cut_normal_axis": "X", "kept_side": "NEGATIVE"},
            },
            {
                "id": "positive",
                "kind": "HALF_SECTION",
                "base_view": "right",
                "quality": 0.7,
                "view_data": {"hidden": [1]},
                "section_spec": {"cut_normal_axis": "X", "kept_side": "POSITIVE"},
            },
        ], {
            "cut_normal_axis": "X",
            "kept_side": "POSITIVE",
            "view_direction": "right",
            "include_hidden": False,
            "source": "prior",
        })
        self.assertEqual(selected["id"], "positive")
        self.assertEqual(selected["view_data"]["hidden"], [])

    def test_adapts_original_view_geometry_to_single_sheet_slot(self):
        rules = [{
            "rule_id": "feature.top",
            "target_views": ["top"],
            "view_geometry": {"top": {"start_proj": [1, 2], "end_proj": [3, 4]}},
        }]
        adapted = _adapt_rules_to_single_view(rules, "top")
        self.assertEqual(adapted[0]["target_views"], ["front"])
        self.assertEqual(adapted[0]["view_geometry"]["front"]["start_proj"], [1, 2])


if __name__ == "__main__":
    unittest.main()
