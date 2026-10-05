import types
import unittest

from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCylinder

from auto_2d_drawing.tolerance.topology_feature_mapper import TopologyFeatureMapper


class TopologyFeatureMapperTests(unittest.TestCase):
    def setUp(self):
        self.shape = BRepPrimAPI_MakeCylinder(5.0, 10.0).Shape()
        self.mapper = TopologyFeatureMapper(self.shape)
        self.node = types.SimpleNamespace(
            id="cylinder_01",
            feature_type="shaft_segment",
            source_info={
                "type": "cylinder",
                "center": [0.0, 0.0, 5.0],
                "axis_dir": [0.0, 0.0, 1.0],
                "radius": 5.0,
                "length": 10.0,
                "is_hole": False,
            },
        )
        self.dimension = types.SimpleNamespace(
            dimension_category="DIAMETER",
            nominal_value=10.0,
        )

    @staticmethod
    def _association():
        return {
            "association_status": "GEOMETRIC_ATTACHMENT",
            "association_confidence": 0.96,
            "definition_point_links": [
                {"definition_point": "defpoint", "point": [105.0, 100.0]},
                {"definition_point": "defpoint4", "point": [95.0, 100.0]},
            ],
            "circular_attachment_profiles": [{
                "geometry_group": "CIRCULAR:1",
                "center": [100.0, 100.0],
                "radius": 5.0,
                "diameter": 10.0,
                "handles": ["A", "B", "C", "D"],
                "entity_types": ["ARC"],
                "definition_point_count": 2,
                "represented_by_split_arcs": True,
            }],
        }

    def test_local_profile_maps_endpoints_to_real_cylindrical_surface(self):
        result = self.mapper.map_dimension(
            self.dimension,
            self.node,
            "diameter",
            self._association(),
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["mode"], "LOCAL_CIRCULAR_PROFILE")
        self.assertEqual(result["status"], "TOPOLOGY_FACE_MAPPED")
        self.assertTrue(result["logical_surface_identity_verified"])
        self.assertEqual(result["mapped_endpoint_count"], 2)
        self.assertTrue(result["logical_surface_id"].startswith("surface_"))
        for endpoint in result["endpoint_mappings"]:
            x, y, z = endpoint["mapped_point_3d"]
            self.assertAlmostEqual((x * x + y * y) ** 0.5, 5.0, places=5)
            self.assertAlmostEqual(z, 5.0, places=5)

    def test_wrong_diameter_profile_is_not_promoted(self):
        association = self._association()
        association["circular_attachment_profiles"][0]["diameter"] = 11.0

        result = self.mapper.map_dimension(
            self.dimension,
            self.node,
            "diameter",
            association,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "NO_UNIQUE_DXF_CIRCULAR_PROFILE")


if __name__ == "__main__":
    unittest.main()
