import unittest

from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt

from experimental_lab.tolerance_extraction_audit.common.scripts.annular_composite_features import (
    build_annular_dimension_candidates,
    extract_cylindrical_faces,
    pair_annular_faces,
)


def cylinder(face_index, radius, relation, solid=1, center=(0, 0, 2), axis=(0, 0, 1), start=0, end=4):
    return {
        "solid_index": solid,
        "face_index": face_index,
        "radius": float(radius),
        "diameter": float(radius) * 2,
        "location": (0, 0, 0),
        "center": center,
        "axis": axis,
        "start": float(start),
        "end": float(end),
        "length": float(end - start),
        "material_relation": relation,
    }


class AnnularCompositeFeatureTests(unittest.TestCase):
    def test_negative_axis_cylinder_uses_occ_world_coordinates(self):
        shape = BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(0, 0, 10), gp_Dir(0, 0, -1)), 2.0, 5.0
        ).Shape()
        cylinders = extract_cylindrical_faces(shape)
        self.assertEqual(len(cylinders), 1)
        self.assertEqual(cylinders[0]["axis"], (0.0, 0.0, 1.0))
        self.assertAlmostEqual(cylinders[0]["start"], 5.0)
        self.assertAlmostEqual(cylinders[0]["end"], 10.0)
        self.assertAlmostEqual(cylinders[0]["center"][2], 7.5)

    def test_pairs_nearest_outer_face_in_same_solid(self):
        cylinders = [
            cylinder(1, 3, "INNER_MATERIAL_BOUNDARY"),
            cylinder(2, 5, "OUTER_MATERIAL_BOUNDARY"),
            cylinder(3, 8, "OUTER_MATERIAL_BOUNDARY"),
        ]
        pairs, rejected = pair_annular_faces(cylinders, 20)
        self.assertEqual(len(pairs), 1)
        self.assertFalse(rejected)
        self.assertEqual(pairs[0]["outer_face_index"], 2)
        self.assertAlmostEqual(pairs[0]["radial_wall_thickness"], 2.0)

    def test_does_not_pair_faces_from_different_solids(self):
        cylinders = [
            cylinder(1, 3, "INNER_MATERIAL_BOUNDARY", solid=1),
            cylinder(2, 5, "OUTER_MATERIAL_BOUNDARY", solid=2),
        ]
        pairs, rejected = pair_annular_faces(cylinders, 20)
        self.assertFalse(pairs)
        self.assertEqual(rejected[0]["reason"], "NO_SAME_SOLID_COAXIAL_OVERLAPPING_OUTER_FACE")

    def test_rejects_non_overlapping_axial_faces(self):
        cylinders = [
            cylinder(1, 3, "INNER_MATERIAL_BOUNDARY", start=0, end=2),
            cylinder(2, 5, "OUTER_MATERIAL_BOUNDARY", start=3, end=5),
        ]
        pairs, _ = pair_annular_faces(cylinders, 20)
        self.assertFalse(pairs)

    def test_builds_four_dimension_semantics(self):
        pairs, _ = pair_annular_faces([
            cylinder(1, 3, "INNER_MATERIAL_BOUNDARY"),
            cylinder(2, 5, "OUTER_MATERIAL_BOUNDARY"),
        ], 20)
        dimensions = build_annular_dimension_candidates(pairs[0])
        self.assertEqual({item["semantic"] for item in dimensions}, {
            "annular_section.inner_diameter",
            "annular_section.outer_diameter",
            "annular_section.radial_wall_thickness",
            "annular_section.axial_thickness",
        })
        self.assertEqual([item["nominal_value"] for item in dimensions], [6.0, 10.0, 2.0, 4.0])


if __name__ == "__main__":
    unittest.main()
