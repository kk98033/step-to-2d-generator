import math
import types
import unittest

from auto_2d_drawing.tolerance.projected_feature_mapper import (
    FeatureFootprintProjector,
    GlobalFeatureAssignmentResolver,
)
from auto_2d_drawing.tolerance.projection_registration_v2 import SimilarityTransform2D


class ProjectedFeatureMapperTests(unittest.TestCase):
    def setUp(self):
        self.view = {
            "bbox": (-8.0, -12.0, 8.0, 12.0),
            "projection": {
                "direction": [0.0, 0.0, 1.0],
                "up": [0.0, 1.0, 0.0],
            },
        }
        self.transform = SimilarityTransform2D(
            scale=2.0,
            rotation_degrees=31.0,
            translation=(40.0, -10.0),
            mirrored=False,
        )

    @staticmethod
    def _cylinder_node(node_id, center):
        return types.SimpleNamespace(
            id=node_id,
            feature_type="shaft_segment",
            nominal={"diameter": 4.0, "length": 6.0},
            source_info={
                "type": "cylinder",
                "center": center,
                "axis_dir": [1.0, 0.0, 0.0],
                "radius": 2.0,
                "length": 6.0,
            },
        )

    def test_registered_footprint_retains_feature_provenance(self):
        node = self._cylinder_node("shaft_02", [4.0, 0.0, 0.0])
        projector = FeatureFootprintProjector()
        footprint = projector.project(
            node,
            "front",
            self.view,
            self.transform.to_dict(),
        )

        self.assertEqual(footprint.status, "PROJECTED")
        self.assertEqual(footprint.feature_id, "shaft_02")
        self.assertEqual(footprint.feature_type, "shaft_segment")
        self.assertEqual(footprint.geometry_kind, "cylinder")
        self.assertGreater(len(footprint.points), 80)
        expected_center = self.transform.apply_point((0.0, -4.0))
        self.assertLess(math.dist(footprint.center, expected_center), 1e-8)

    def test_anchor_selects_correct_same_diameter_feature_by_position(self):
        projector = FeatureFootprintProjector()
        left = projector.project(
            self._cylinder_node("left", [-5.0, 0.0, 0.0]),
            "front",
            self.view,
            self.transform.to_dict(),
        )
        right = projector.project(
            self._cylinder_node("right", [5.0, 0.0, 0.0]),
            "front",
            self.view,
            self.transform.to_dict(),
        )
        anchor = self.transform.apply_point((0.0, -5.0))
        all_points = left.points + right.points
        drawing_bbox = (
            min(point[0] for point in all_points),
            min(point[1] for point in all_points),
            max(point[0] for point in all_points),
            max(point[1] for point in all_points),
        )

        left_result = projector.evaluate_anchor(anchor, left, drawing_bbox)
        right_result = projector.evaluate_anchor(anchor, right, drawing_bbox)

        self.assertFalse(left_result["passed"])
        self.assertTrue(right_result["passed"])
        self.assertGreater(right_result["score"], left_result["score"])

    def test_small_feature_does_not_inherit_large_sheet_position_tolerance(self):
        projector = FeatureFootprintProjector()
        footprint = projector.project(
            self._cylinder_node("small-on-large-sheet", [0.0, 0.0, 0.0]),
            "front",
            self.view,
            None,
        )
        anchor = (footprint.bbox[2] + 2.0, footprint.center[1])
        result = projector.evaluate_anchor(anchor, footprint, (-100.0, -100.0, 100.0, 100.0))

        self.assertFalse(result["passed"])
        self.assertGreater(result["bbox_distance"], result["bbox_distance_limit"])

    def test_global_assignment_avoids_greedy_feature_collision(self):
        candidates = [
            {"dimension_key": "d1", "feature_key": "f1", "score": 0.90, "passed": True},
            {"dimension_key": "d1", "feature_key": "f2", "score": 0.89, "passed": True},
            {"dimension_key": "d2", "feature_key": "f1", "score": 0.88, "passed": True},
            {"dimension_key": "d2", "feature_key": "f2", "score": 0.81, "passed": True},
        ]

        result = GlobalFeatureAssignmentResolver().resolve(
            candidates,
            min_score=0.80,
            min_margin=0.0,
        )
        selected = {(item["dimension_key"], item["feature_key"]) for item in result["selected"]}

        self.assertEqual(selected, {("d1", "f2"), ("d2", "f1")})

    def test_assignment_rejects_tied_ambiguous_dimension(self):
        result = GlobalFeatureAssignmentResolver().resolve([
            {"dimension_key": "d1", "feature_key": "f1", "score": 0.91, "passed": True},
            {"dimension_key": "d1", "feature_key": "f2", "score": 0.90, "passed": True},
        ])
        self.assertEqual(result["selected"], [])


if __name__ == "__main__":
    unittest.main()
