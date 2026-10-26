import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.historical_view_comparison import (
    bbox_iou,
    expanded_crop_bounds,
    select_distinct_view_matches,
)


class HistoricalViewComparisonTests(unittest.TestCase):
    def test_single_view_is_not_forced_to_three(self):
        rows = [{"step_view": "front", "candidate_bbox": [0, 0, 10, 10], "comparison_score": .8}]
        self.assertEqual(len(select_distinct_view_matches(rows)), 1)

    def test_overlapping_multiscale_candidates_are_deduplicated(self):
        rows = [
            {"step_view": "front", "candidate_bbox": [0, 0, 10, 10], "comparison_score": .9},
            {"step_view": "top", "candidate_bbox": [.2, .2, 10.2, 10.2], "comparison_score": .85},
            {"step_view": "top", "candidate_bbox": [20, 0, 30, 10], "comparison_score": .75},
        ]
        selected = select_distinct_view_matches(rows)
        self.assertEqual([(item["step_view"], item["candidate_bbox"][0]) for item in selected], [("front", 0), ("top", 20)])

    def test_low_score_extra_view_is_not_invented(self):
        rows = [
            {"step_view": "front", "candidate_bbox": [0, 0, 10, 10], "comparison_score": .9},
            {"step_view": "top", "candidate_bbox": [20, 0, 30, 10], "comparison_score": .3},
        ]
        self.assertEqual(len(select_distinct_view_matches(rows)), 1)

    def test_rejects_local_profile_and_bad_scale(self):
        rows = [{
            "step_view": "top", "candidate_bbox": [20, 0, 30, 1],
            "comparison_score": .8, "scale": .06, "source_coverage": .9,
            "target_coverage": .9, "aspect_similarity": .1, "primitive_count": 12,
        }]
        self.assertEqual(select_distinct_view_matches(rows), [])

    def test_equivalent_step_views_are_not_counted_twice(self):
        rows = [
            {"step_view": "front", "step_view_group": "group_1", "candidate_bbox": [0, 0, 10, 2], "comparison_score": .9},
            {"step_view": "right", "step_view_group": "group_1", "candidate_bbox": [20, 0, 30, 2], "comparison_score": .8},
        ]
        self.assertEqual(len(select_distinct_view_matches(rows)), 1)

    def test_accepts_complete_dxf_when_step_has_extra_hidden_contours(self):
        rows = [{
            "step_view": "front", "candidate_bbox": [0, 0, 20, 3],
            "comparison_score": .57, "scale": 1.0, "source_coverage": .479,
            "target_coverage": 1.0, "aspect_similarity": 1.0,
            "sheet_area_fraction": .04, "primitive_count": 23,
        }]
        self.assertEqual(len(select_distinct_view_matches(rows)), 1)

    def test_crop_expansion_is_clipped_to_sheet(self):
        self.assertEqual(expanded_crop_bounds([0, 0, 10, 10], [0, 0, 100, 100]), [0, 0, 11.8, 11.8])
        self.assertAlmostEqual(bbox_iou([0, 0, 10, 10], [0, 0, 10, 10]), 1.0)


if __name__ == "__main__":
    unittest.main()
