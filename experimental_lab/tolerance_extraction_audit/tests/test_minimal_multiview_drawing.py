import unittest

import ezdxf

from auto_2d_drawing.dxf_drawer import DrawingLayout
from experimental_lab.tolerance_extraction_audit.common.scripts.minimal_multiview_drawing import (
    _adapt_rules_to_single_view,
    _rectangles_overlap,
    _select_multiview_paper,
    _visible_extents,
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

    def test_visible_extents_supports_curved_visible_geometry(self):
        document = ezdxf.new()
        document.layers.add("VISIBLE")
        document.modelspace().add_circle((10, 20), radius=5, dxfattribs={"layer": "VISIBLE"})

        bounds = _visible_extents(document.modelspace())

        self.assertEqual(tuple(round(value, 6) for value in bounds), (5.0, 15.0, 15.0, 25.0))

    def test_adaptive_multiview_layout_maximizes_each_view_without_overlap(self):
        layout = DrawingLayout(
            {"front": (120.0, 120.0), "top": (25.0, 120.0), "right": (120.0, 25.0)},
            paper="A3",
            adaptive=True,
        )
        rectangles = {
            name: {
                "x": layout.get_view_offset(name)[0],
                "y": layout.get_view_offset(name)[1],
                "width": layout.get_scaled_size(name)[0],
                "height": layout.get_scaled_size(name)[1],
            }
            for name in ("front", "top", "right")
        }
        pairs = [("front", "top"), ("front", "right"), ("top", "right")]
        self.assertTrue(all(not _rectangles_overlap(rectangles[a], rectangles[b]) for a, b in pairs))
        self.assertTrue(all(layout.get_view_scale(name) > 0.0 for name in rectangles))
        self.assertIn(layout.layout_strategy, {
            "HORIZONTAL_MAX_FIT", "VERTICAL_MAX_FIT", "ORTHOGRAPHIC_L_MAX_FIT",
        })

    def test_dense_multiview_uses_larger_sheet(self):
        self.assertEqual(_select_multiview_paper(15), "A3")
        self.assertEqual(_select_multiview_paper(24), "A2")


if __name__ == "__main__":
    unittest.main()
