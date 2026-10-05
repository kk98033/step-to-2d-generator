import math
import random
import types
import unittest

from auto_2d_drawing.tolerance.projection_registration_v2 import (
    AdaptiveProjectionCandidateGenerator,
    ProjectionRegistrationEngine,
    SimilarityTransform2D,
)


def _polyline_points(vertices, samples=12):
    result = []
    for left, right in zip(vertices, vertices[1:]):
        for index in range(samples):
            ratio = index / samples
            result.append((
                left[0] + (right[0] - left[0]) * ratio,
                left[1] + (right[1] - left[1]) * ratio,
            ))
    result.append(vertices[-1])
    return result


class ProjectionRegistrationV2Tests(unittest.TestCase):
    def setUp(self):
        # Deliberately asymmetric outline prevents 90/180-degree ambiguity.
        self.step_points = _polyline_points([
            (-4.0, -2.0),
            (5.0, -2.0),
            (5.0, 1.0),
            (2.0, 1.0),
            (2.0, 4.0),
            (-4.0, 4.0),
            (-4.0, -2.0),
        ])

    def test_recovers_arbitrary_similarity_transform(self):
        expected = SimilarityTransform2D(
            scale=2.35,
            rotation_degrees=37.0,
            translation=(83.0, -24.0),
            mirrored=False,
        )
        dxf_points = expected.apply(self.step_points)
        result = ProjectionRegistrationEngine(angle_step_degrees=15).register(
            dxf_points,
            self.step_points,
        )

        self.assertEqual(result["status"], "REGISTERED")
        self.assertGreater(result["score"], 0.94)
        self.assertLess(result["normalized_chamfer"], 0.01)
        recovered = SimilarityTransform2D(
            scale=result["transform"]["scale"],
            rotation_degrees=result["transform"]["rotation_degrees"],
            translation=tuple(result["transform"]["translation"]),
            mirrored=result["transform"]["mirrored"],
        )
        distances = [
            math.dist(left, right)
            for left, right in zip(recovered.apply(self.step_points), dxf_points)
        ]
        self.assertLess(sum(distances) / len(distances), 0.05)

    def test_handles_mirror_missing_curves_and_outliers(self):
        expected = SimilarityTransform2D(
            scale=1.7,
            rotation_degrees=23.0,
            translation=(-15.0, 41.0),
            mirrored=True,
        )
        transformed = expected.apply(self.step_points)
        dxf_points = transformed[8:-10]
        dxf_points.extend([(100.0, 100.0), (-90.0, 70.0), (44.0, -80.0)])

        result = ProjectionRegistrationEngine(
            angle_step_degrees=15,
            trim_fraction=0.76,
        ).register(dxf_points, self.step_points)

        self.assertEqual(result["status"], "REGISTERED")
        self.assertTrue(result["mirrored"])
        self.assertGreater(result["score"], 0.62)
        self.assertLess(result["normalized_chamfer"], 0.06)

    def test_density_balancing_prevents_oversampled_curve_from_dominating(self):
        expected = SimilarityTransform2D(
            scale=1.85,
            rotation_degrees=128.0,
            translation=(37.0, -52.0),
            mirrored=True,
        )
        transformed = expected.apply(self.step_points)
        rng = random.Random(19)
        dxf_points = [point for point in transformed if rng.random() > 0.22]
        segment_start, segment_end = transformed[18], transformed[19]
        for _index in range(300):
            ratio = rng.random()
            dxf_points.append((
                segment_start[0] + (segment_end[0] - segment_start[0]) * ratio + rng.gauss(0.0, 0.002),
                segment_start[1] + (segment_end[1] - segment_start[1]) * ratio + rng.gauss(0.0, 0.002),
            ))
        for _index in range(12):
            base = transformed[rng.randrange(len(transformed))]
            dxf_points.append((base[0] + rng.gauss(0.0, 2.0), base[1] + rng.gauss(0.0, 2.0)))

        result = ProjectionRegistrationEngine().register(dxf_points, self.step_points)
        recovered = SimilarityTransform2D(
            scale=result["transform"]["scale"],
            rotation_degrees=result["transform"]["rotation_degrees"],
            translation=tuple(result["transform"]["translation"]),
            mirrored=result["transform"]["mirrored"],
        )
        mean_error = sum(
            math.dist(left, right)
            for left, right in zip(recovered.apply(self.step_points), transformed)
        ) / len(self.step_points)

        self.assertEqual(result["method"], "DENSITY_BALANCED_MUTUAL_ICP_V3")
        self.assertLess(mean_error, 0.08)
        self.assertGreater(result["inlier_ratio"], 0.75)

    def test_adaptive_candidates_include_feature_axis_without_duplicates(self):
        nodes = [
            types.SimpleNamespace(source_info={"axis_dir": [1.0, 1.0, 0.0]}),
            types.SimpleNamespace(source_info={"axis_dir": [2.0, 2.0, 0.0]}),
        ]
        specs = AdaptiveProjectionCandidateGenerator(max_views=18).build_specs(nodes)

        directions = [spec.direction for spec in specs]
        diagonal = (math.sqrt(0.5), math.sqrt(0.5), 0.0)
        self.assertTrue(any(sum(a * b for a, b in zip(item, diagonal)) > 0.999 for item in directions))
        for index, left in enumerate(directions):
            for right in directions[index + 1:]:
                self.assertLess(sum(a * b for a, b in zip(left, right)), 0.9995)

    def test_inverse_round_trip(self):
        transform = SimilarityTransform2D(2.0, 71.0, (4.0, -9.0), mirrored=True)
        point = (3.2, -1.7)
        self.assertLess(math.dist(transform.inverse_point(transform.apply_point(point)), point), 1e-9)


if __name__ == "__main__":
    unittest.main()
