import math
import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.feature_anchor_repair import (
    _primitive_bbox,
    repair_rules,
)


class _FeatureExtractorStub:
    cylinders_raw = []

    def summary(self):
        return {
            "main_axis": "Z",
            "bounds": (-5, -5, -5, 5, 5, 5),
            "holes": [{
                "diameter": 4.0,
                "radius": 2.0,
                "length": 6.0,
                "center": (3.0, 2.0, 1.0),
                "axis_dir": (0.0, 0.0, 1.0),
                "is_hole": True,
            }],
            "shafts": [], "cones": [], "fillets": [], "hole_patterns": [],
        }


class FeatureAnchorRepairTests(unittest.TestCase):
    def test_axis_aligned_cylinder_end_view_has_diameter_span(self):
        bbox = _primitive_bbox((3, 2, 1), (0, 0, 1), 6, 2, "front")
        self.assertAlmostEqual(bbox[2] - bbox[0], 4.0, places=6)
        self.assertAlmostEqual(bbox[3] - bbox[1], 4.0, places=6)

    def test_axis_aligned_cylinder_side_view_has_length_and_diameter(self):
        bbox = _primitive_bbox((3, 2, 1), (0, 0, 1), 6, 2, "top")
        spans = sorted((bbox[2] - bbox[0], bbox[3] - bbox[1]))
        self.assertAlmostEqual(spans[0], 4.0, places=6)
        self.assertAlmostEqual(spans[1], 6.0, places=6)

    def test_hole_gets_inner_material_semantics(self):
        records = [{
            "id": "hole_01", "type": "hole", "role": "functional",
            "nominal": {"diameter": 4.0, "length": 6.0},
            "geometry": {"kind": "cylinder", "center": [3, 2, 1], "diameter": 4, "length": 6},
            "source": {"extractor": "UniversalFeatureEngine.holes"},
        }]
        rules = [{
            "rule_id": "hole_01", "dim_type": "DIAMETER", "nominal_value": 4.0,
            "preferred_view": "front", "view_geometry": {},
        }]
        repaired, audit = repair_rules(rules, records, _FeatureExtractorStub(), {"front": {}, "top": {}})
        self.assertEqual(audit["hole_01"]["status"], "REPAIRED")
        self.assertEqual(repaired[0]["view_geometry"]["front"]["material_relation"], "INNER_MATERIAL_BOUNDARY")
        self.assertAlmostEqual(
            repaired[0]["view_geometry"]["front"]["projected_feature_bbox"][2]
            - repaired[0]["view_geometry"]["front"]["projected_feature_bbox"][0],
            4.0,
        )


if __name__ == "__main__":
    unittest.main()
