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

from dimension_intents import build_dimension_intent_set
from thin_wall_dimension_pipeline import extract_planar_thin_wall_dimension


class ThinWallDimensionPipelineTests(unittest.TestCase):
    def test_planar_wall_uses_normal_separation_not_face_centre_distance(self):
        result = extract_planar_thin_wall_dimension(
            BRepPrimAPI_MakeBox(40.0, 25.0, 0.8).Shape(),
            {
                "display_value": 0.8,
                "tolerance": {"mode": "SYMMETRIC", "upper": 0.05, "lower": -0.05},
                "tolerance_source": "TEST_CASE",
            },
        )
        self.assertTrue(result["resolved"])
        dimension = result["dimensions"][0]
        self.assertAlmostEqual(dimension["nominal_value"], 0.8, places=6)
        self.assertAlmostEqual(math.dist(*dimension["anchors"]), 0.8, places=6)
        intent = build_dimension_intent_set(thin_wall_result=result)["intents"][0]
        self.assertNotEqual(intent["verification"]["status"], "INVALID")
        self.assertEqual(intent["renderer_contract"]["renderer"], "THICKNESS_RENDERER")
        self.assertEqual(intent["measurement"]["tolerance_source"], "TEST_CASE")

    def test_solid_cylinder_does_not_create_planar_wall_dimension(self):
        result = extract_planar_thin_wall_dimension(BRepPrimAPI_MakeCylinder(8.0, 30.0).Shape())
        self.assertFalse(result["resolved"])


if __name__ == "__main__":
    unittest.main()
