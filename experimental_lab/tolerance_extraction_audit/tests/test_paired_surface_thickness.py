import math
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_ROOT = PROJECT_ROOT / "experimental_lab" / "tolerance_extraction_audit" / "common" / "scripts"
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder

from paired_surface_thickness import (
    aggregate_station_thickness,
    extract_parallel_planar_thickness,
    extract_section_boundary_thickness,
)


class PairedSurfaceThicknessTests(unittest.TestCase):
    def test_thin_box_resolves_without_part_type(self):
        result = extract_parallel_planar_thickness(BRepPrimAPI_MakeBox(40.0, 25.0, 0.8).Shape())
        self.assertTrue(result["resolved"])
        self.assertAlmostEqual(result["candidate"]["thickness"], 0.8, places=6)
        self.assertEqual(result["candidate"]["semantic"], "thin_wall.normal_thickness")

    def test_solid_cylinder_is_not_planar_wall(self):
        result = extract_parallel_planar_thickness(BRepPrimAPI_MakeCylinder(8.0, 30.0).Shape())
        self.assertFalse(result["resolved"])

    def test_local_companion_gap_is_geometry_only(self):
        edges = []
        count = 12
        for index in range(count):
            angle = 2.0 * math.pi * index / count
            tangent = (-math.sin(angle), math.cos(angle))
            radial = (math.cos(angle), math.sin(angle))
            for offset in (-0.125, 0.125):
                points = []
                for step in range(9):
                    radius = 10.0 + step
                    points.append([
                        radius * radial[0] + offset * tangent[0],
                        radius * radial[1] + offset * tangent[1],
                    ])
                edges.append({"type": "spline", "points": points})
        view = {"visible": edges, "hidden": [], "bbox": [-19, -19, 19, 19], "center_hint": [0, 0]}
        refs = [f"visible:{index * 2}" for index in range(count)]
        detection = {
            "resolved": True,
            "center": [0, 0],
            "pattern_count": count,
            "selected_boundary_edge_refs": refs,
        }
        result = extract_section_boundary_thickness(view, detection)
        self.assertTrue(result["resolved"])
        self.assertAlmostEqual(result["summary"]["median"], 0.25, places=3)
        self.assertNotIn("fan", result["semantic"])
        self.assertNotIn("blade", result["semantic"])

    def test_multi_station_requires_three_agreeing_sections(self):
        stations = [
            {"resolved": True, "summary": {"median": value}}
            for value in (0.250, 0.251, 0.252, 0.251)
        ]
        result = aggregate_station_thickness(stations)
        self.assertTrue(result["resolved"])
        self.assertTrue(result["uniform_thickness"])
        self.assertEqual(result["verification_status"], "AUTO_VERIFIED")

    def test_variable_thickness_is_preserved_as_profile(self):
        stations = [
            {"resolved": True, "summary": {"median": value}}
            for value in (0.250, 0.250, 0.281)
        ]
        result = aggregate_station_thickness(stations)
        self.assertTrue(result["resolved"])
        self.assertFalse(result["uniform_thickness"])
        self.assertEqual(result["semantic"], "thin_wall.thickness_profile")
        self.assertEqual(result["verification_status"], "REVIEW_CANDIDATE")


if __name__ == "__main__":
    unittest.main()
