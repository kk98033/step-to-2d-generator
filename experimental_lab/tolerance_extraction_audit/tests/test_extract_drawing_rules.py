import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.extract_drawing_rules import (
    _assign_ranks,
    _normalise_point,
    infer_side_and_offset,
)


class DrawingRuleExtractionTests(unittest.TestCase):
    def test_normalise_point_is_view_relative(self):
        self.assertEqual(_normalise_point((15.0, 30.0), (10.0, 20.0, 20.0, 40.0)), [0.5, 0.5])


    def test_side_and_offset_are_scale_independent(self):
        first = infer_side_and_offset((5.0, -2.0), (0.0, 0.0, 10.0, 10.0))
        second = infer_side_and_offset((50.0, -20.0), (0.0, 0.0, 100.0, 100.0))
        self.assertEqual(first, second)
        self.assertEqual(first, ("BOTTOM", 0.2, 1.0))


    def test_inside_view_is_not_reported_as_outside_offset(self):
        side, offset, confidence = infer_side_and_offset((8.0, 5.0), (0.0, 0.0, 10.0, 10.0))
        self.assertEqual(side, "RIGHT")
        self.assertEqual(offset, 0.0)
        self.assertLess(confidence, 1.0)


    def test_ranks_are_local_to_view_and_side(self):
        cases = [
            {"dimension_handle": "C", "dxf_view_id": "v1", "placement": {"side": "BOTTOM", "offset_normalized": 0.21}},
            {"dimension_handle": "A", "dxf_view_id": "v1", "placement": {"side": "BOTTOM", "offset_normalized": 0.05}},
            {"dimension_handle": "B", "dxf_view_id": "v1", "placement": {"side": "BOTTOM", "offset_normalized": 0.055}},
            {"dimension_handle": "D", "dxf_view_id": "v2", "placement": {"side": "BOTTOM", "offset_normalized": 0.30}},
        ]
        _assign_ranks(cases)
        ranks = {case["dimension_handle"]: case["placement"]["rank"] for case in cases}
        self.assertEqual(ranks, {"C": 2, "A": 1, "B": 1, "D": 1})


if __name__ == "__main__":
    unittest.main()
