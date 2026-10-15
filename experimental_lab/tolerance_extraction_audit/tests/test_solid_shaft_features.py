import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.solid_shaft_features import (
    build_solid_shaft_chains,
    build_solid_shaft_dimension_candidates,
)


def cylinder(face, radius, start, end, relation="OUTER_MATERIAL_BOUNDARY", solid=1, location=(0, 0, 0), axis=(1, 0, 0)):
    return {
        "solid_index": solid,
        "face_index": face,
        "radius": float(radius),
        "diameter": float(radius) * 2.0,
        "location": location,
        "center": ((start + end) / 2.0, 0, 0),
        "axis": axis,
        "start": float(start),
        "end": float(end),
        "length": float(end - start),
        "material_relation": relation,
    }


class SolidShaftFeatureTests(unittest.TestCase):
    def test_builds_ordered_segments_shoulders_and_overall_extent(self):
        chains, rejected = build_solid_shaft_chains([
            cylinder(2, 3, 4, 10),
            cylinder(1, 2, 0, 4),
        ], 15, {1: (-1, 11)})
        self.assertFalse(rejected)
        self.assertEqual(len(chains), 1)
        chain = chains[0]
        self.assertEqual([item["face_index"] for item in chain["segments"]], [1, 2])
        self.assertEqual(len(chain["shoulders"]), 1)
        self.assertAlmostEqual(chain["overall_length"], 12.0)
        self.assertAlmostEqual(chain["terminal_start_allowance"], 1.0)
        self.assertAlmostEqual(chain["terminal_end_allowance"], 1.0)

    def test_delegates_hollow_interval_to_annular_workflow(self):
        chains, rejected = build_solid_shaft_chains([
            cylinder(1, 5, 0, 8),
            cylinder(2, 2, 0, 8, relation="INNER_MATERIAL_BOUNDARY"),
        ], 15)
        self.assertFalse(chains)
        self.assertEqual(rejected[0]["reason"], "OVERLAPS_INNER_CYLINDER_USE_ANNULAR_WORKFLOW")

    def test_rejects_isolated_short_boss(self):
        chains, rejected = build_solid_shaft_chains([cylinder(1, 4, 0, 2)], 10)
        self.assertFalse(chains)
        self.assertEqual(rejected[0]["reason"], "ISOLATED_SHORT_OUTER_CYLINDER")

    def test_builds_two_dimensions_per_segment_plus_overall(self):
        chains, _ = build_solid_shaft_chains([
            cylinder(1, 2, 0, 4),
            cylinder(2, 3, 4, 10),
        ], 15, {1: (0, 10)})
        dimensions = build_solid_shaft_dimension_candidates(chains[0])
        self.assertEqual(len(dimensions), 5)
        self.assertEqual([item["semantic"] for item in dimensions].count("solid_shaft.segment_outer_diameter"), 2)
        self.assertEqual([item["semantic"] for item in dimensions].count("solid_shaft.segment_axial_length"), 2)
        overall = dimensions[-1]
        self.assertEqual(overall["semantic"], "solid_shaft.overall_length")
        self.assertAlmostEqual(overall["nominal_value"], 10.0)


if __name__ == "__main__":
    unittest.main()
