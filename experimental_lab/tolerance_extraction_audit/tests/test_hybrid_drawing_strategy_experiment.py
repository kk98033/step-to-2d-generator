import unittest
from types import SimpleNamespace

from experimental_lab.tolerance_extraction_audit.common.scripts.hybrid_drawing_strategy_experiment import (
    build_drawing_strategy,
    build_shaft_dimension_intents,
    classify_native_dimension_axis,
    infer_style_prior,
    solve_adaptive_layout,
)


class HybridDrawingStrategyExperimentTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "id": "shaft_overall",
                "type": "overall_size",
                "role": "assembly",
                "nominal": {"length": 20.0, "diameter": 10.0},
                "geometry": {"center": [0, 0, 10], "size": [10, 10, 20]},
            },
            {"id": "datum_a_axis", "type": "datum", "role": "datum", "nominal": {"axis": "Z"}, "geometry": {}},
            {
                "id": "step_01",
                "type": "step",
                "role": "shaft_step",
                "nominal": {"length": 0.8, "diameter": 10.0},
                "geometry": {"center": [0, 0, 0.4], "size": [10, 10, 0.8], "axis": "Z"},
            },
            {
                "id": "groove_01",
                "type": "groove_or_slot",
                "role": "snap_ring_groove",
                "nominal": {"groove_width": 0.8, "groove_diameter": 9.0},
                "geometry": {"center": [0, 0, 1.2], "size": [9, 9, 0.8], "axis": "Z"},
            },
        ]
        self.views = {
            "front": {"bbox": (-5, -5, 5, 5)},
            "top": {"bbox": (-20, -5, 0, 5)},
            "right": {"bbox": (-5, -20, 5, 0)},
        }
        self.prior = {
            "preferred_side": {"AXIAL": "BOTTOM", "TRANSVERSE": "TOP"},
        }

    def test_style_prior_does_not_reuse_source_ranks(self):
        rules = [
            {"dimension_handle": "A", "placement": {"side": "BOTTOM", "rank": 9}},
            {"dimension_handle": "B", "placement": {"side": "TOP", "rank": 7}},
        ]
        links = {
            "A": {"nominal_field": "length"},
            "B": {"nominal_field": "diameter"},
        }
        prior = infer_style_prior(rules, links)
        self.assertFalse(prior["source_ranks_reused"])
        self.assertEqual(prior["preferred_side"], {"AXIAL": "BOTTOM", "TRANSVERSE": "TOP"})

    def test_strategy_uses_family_for_horizontal_longitudinal_view(self):
        strategy = build_drawing_strategy("SHAFT", self.records, self.views, self.prior)
        self.assertEqual(strategy["primary_view"], "top")
        self.assertEqual(strategy["primary_axis"], "Z")
        self.assertEqual(strategy["layout_policy"], "TARGET_INTERVAL_GRAPH")

    def test_feature_boundaries_create_datum_chain_without_drawing(self):
        strategy = build_drawing_strategy("SHAFT", self.records, self.views, self.prior)
        intents, rejected = build_shaft_dimension_intents(self.records, strategy, self.views["top"]["bbox"])
        values = {round(item["nominal_value"], 3) for item in intents if item["semantic_type"] == "DATUM_BASELINE_DISTANCE"}
        self.assertIn(0.8, values)
        self.assertIn(1.6, values)
        self.assertTrue(all(item.get("reference_end") in {"MIN", "MAX"} for item in intents if item["semantic_type"] == "DATUM_BASELINE_DISTANCE"))
        self.assertFalse(rejected)

    def test_adaptive_layout_recomputes_ranks_without_axial_overlap(self):
        strategy = build_drawing_strategy("SHAFT", self.records, self.views, self.prior)
        intents, _ = build_shaft_dimension_intents(self.records, strategy, self.views["top"]["bbox"])
        planned, metrics = solve_adaptive_layout(intents, self.views["top"]["bbox"], self.prior)
        self.assertEqual(metrics["axial_interval_collisions"], 0)
        overall = next(item for item in planned if item["intent_id"] == "overall_length")
        axial_ranks = [item["placement"]["rank"] for item in planned if item["intent_class"] == "AXIAL"]
        self.assertEqual(overall["placement"]["rank"], max(axial_ranks))

    def test_rotated_linear_dimension_recovers_shaft_semantics(self):
        axial = SimpleNamespace(
            dimension_category="LINEAR", dim_type="LINEAR", nominal_value=4.9,
            points={"defpoint2": [0.0, 0.0], "defpoint3": [4.9, 1.0]},
        )
        transverse = SimpleNamespace(
            dimension_category="LINEAR", dim_type="LINEAR", nominal_value=2.0,
            points={"defpoint2": [0.0, 0.0], "defpoint3": [0.0, 2.0]},
        )
        self.assertEqual(classify_native_dimension_axis(axial, "HORIZONTAL"), "LINEAR")
        self.assertEqual(classify_native_dimension_axis(transverse, "HORIZONTAL"), "DIAMETER_SURROGATE")


if __name__ == "__main__":
    unittest.main()
