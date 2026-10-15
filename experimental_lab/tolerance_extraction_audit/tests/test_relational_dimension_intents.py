import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.relational_dimension_intents import (
    build_pairwise_center_relations,
    circular_difference,
    classify_radial_intervals,
    cluster_angles,
)


class RelationalDimensionIntentTests(unittest.TestCase):
    def test_angle_clustering_merges_zero_degree_seam(self):
        self.assertEqual(len(cluster_angles([359.9, 0.1, 20.0], 0.3)), 2)

    def test_circular_difference_is_directed_and_positive(self):
        self.assertAlmostEqual(circular_difference(350.0, 10.0), 20.0)

    def test_element_gap_and_pitch_are_measured_from_boundaries(self):
        centres = [10, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330]
        boundaries = [value for centre in centres for value in (centre - 1.25, centre + 1.25)]
        result = classify_radial_intervals(boundaries)
        self.assertTrue(result["resolved"])
        self.assertTrue(all(abs(item["angle"] - 2.5) < 1e-6 for item in result["elements"]))
        self.assertEqual([round(item["angle"], 2) for item in result["center_pitches"][:3]], [20.0, 30.0, 30.0])
        self.assertNotIn("blade_count", result)

    def test_uniform_pattern_does_not_invent_element_width(self):
        result = classify_radial_intervals([0, 30, 60, 90, 120, 150])
        self.assertFalse(result["resolved"])
        self.assertIn(result["reason"], {"NO_SEPARABLE_ELEMENT_AND_GAP_WIDTHS", "INSUFFICIENT_REPEATED_ELEMENT_GAP_EVIDENCE"})

    def test_pairwise_relations_include_non_adjacent_structures(self):
        relations = build_pairwise_center_relations([0.0, 30.0, 60.0])
        self.assertEqual(sorted(round(item["angle"], 1) for item in relations), [30.0, 30.0, 60.0])


if __name__ == "__main__":
    unittest.main()
