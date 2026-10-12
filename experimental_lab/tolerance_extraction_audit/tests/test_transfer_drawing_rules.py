import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.transfer_drawing_rules import (
    build_transfers,
    canonical_nominal,
    feature_measurement_anchors,
    select_transfer_view,
)


class DrawingRuleTransferTests(unittest.TestCase):
    def test_canonical_nominal_rejects_missing_and_non_positive_values(self):
        record = {"nominal": {"length": 20.6, "diameter": 0.0}}
        self.assertEqual(canonical_nominal(record, "length"), 20.6)
        self.assertIsNone(canonical_nominal(record, "diameter"))
        self.assertIsNone(canonical_nominal(record, "radius"))

    def test_axis_aware_diameter_does_not_use_short_step_length(self):
        record = {
            "geometry": {"center": [0.0, 0.0, 0.4], "size": [10.9, 10.9, 0.8], "axis": "Z"},
        }
        anchors = feature_measurement_anchors(record, "diameter", "top", (-0.8, -5.45, 0.0, 5.45))
        self.assertAlmostEqual(abs(anchors[1][1] - anchors[0][1]), 10.9)
        self.assertAlmostEqual(anchors[1][0] - anchors[0][0], 0.0)

    def test_transfer_requires_exact_feature_and_nominal_field(self):
        source_rules = [{"dimension_handle": "A", "placement": {"side": "BOTTOM", "rank": 2}, "style": {}}]
        links = {"A": {"canonical_feature_id": "shaft_overall", "nominal_field": "length", "verification_status": "AUTO_VERIFIED", "confidence": 0.98, "case_id": "case-a"}}
        records = [{"id": "shaft_overall", "type": "overall_size", "role": "assembly", "nominal": {"length": 20.6}, "geometry": {"center": [0, 0, 10.3], "size": [10.9, 10.9, 20.6]}}]
        transferred, rejected = build_transfers(source_rules, links, records, "top", (-20.6, -5.45, 0, 5.45))
        self.assertEqual(len(transferred), 1)
        self.assertFalse(rejected)
        self.assertEqual(transferred[0]["target_nominal_value"], 20.6)
        self.assertEqual(transferred[0]["tolerance_policy"], "NOT_TRANSFERRED_SEPARATE_RECOMMENDATION_REQUIRED")

    def test_view_selection_prefers_horizontal_axial_projection(self):
        overall = {"nominal": {"length": 20.0}, "geometry": {"center": [0, 0, 10], "size": [10, 10, 20]}}
        views = {
            "front": {"bbox": (-5, -5, 5, 5)},
            "top": {"bbox": (-20, -5, 0, 5)},
            "right": {"bbox": (-5, -20, 5, 0)},
        }
        rules = [{"placement": {"side": "BOTTOM"}}]
        view, _evidence = select_transfer_view(overall, views, rules)
        self.assertEqual(view, "top")


if __name__ == "__main__":
    unittest.main()
