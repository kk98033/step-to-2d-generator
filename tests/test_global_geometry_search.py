import math
import unittest

from auto_2d_drawing.tolerance.global_geometry_search import (
    GeometryDescriptor,
    GlobalGeometryPairSearcher,
)


def transformed(points, scale=1.0, angle=0.0, offset=(0.0, 0.0), mirrored=False):
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    result = []
    for x, y in points:
        if mirrored:
            x = -x
        result.append((
            offset[0] + scale * (x * cosine - y * sine),
            offset[1] + scale * (x * sine + y * cosine),
        ))
    return result


class GeometryDescriptorTests(unittest.TestCase):
    def test_descriptor_is_similarity_and_mirror_invariant(self):
        points = [
            (-4.0, -2.0), (5.0, -2.0), (5.0, 1.0),
            (2.0, 1.0), (2.0, 4.0), (-4.0, 4.0),
            (-4.0, -2.0), (0.0, 0.0), (3.0, -1.0),
        ]
        left = GeometryDescriptor.build(points)
        right = GeometryDescriptor.build(
            transformed(points, scale=3.2, angle=73.0, offset=(82.0, -41.0), mirrored=True)
        )

        distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))
        self.assertLess(distance, 1e-6)

    def test_dense_view_reduction_is_bounded_and_keeps_sheet_coverage(self):
        points = [(x / 10.0, y / 10.0) for x in range(200) for y in range(100)]
        reduced = GlobalGeometryPairSearcher(maximum_record_points=128)._reduce_points(points)

        self.assertEqual(len(reduced), 128)
        xs = [point[0] for point in reduced]
        ys = [point[1] for point in reduced]
        self.assertGreater(max(xs) - min(xs), 19.0)
        self.assertGreater(max(ys) - min(ys), 9.0)


class GlobalPairClassificationTests(unittest.TestCase):
    @staticmethod
    def _row(fingerprint, score, dxf_record_id="drawing:view_001"):
        return {
            "component": {
                "fingerprint": fingerprint,
                "step_path": f"{fingerprint}.stp",
                "label_entry": "0:1:1:2",
                "name": fingerprint,
                "part_number": None,
                "component_revision": None,
                "equivalent_step_sources": [f"{fingerprint}.stp"],
            },
            "dxf_path": "drawing.dxf",
            "best_match": {
                "step_view_id": "front",
                "dxf_view_id": "view_001",
                "dxf_record_id": dxf_record_id,
                "descriptor_distance": 0.02,
                "descriptor_similarity": 0.95,
                "registration": {
                    "score": score,
                    "contour_score": score,
                    "inlier_ratio": 0.92,
                    "normalized_chamfer": 0.01,
                    "ranked_hausdorff": 0.04,
                    "status": "REGISTERED",
                },
            },
            "supporting_matches": [],
        }

    def test_unique_strong_reciprocal_match_is_verified(self):
        manifest = GlobalGeometryPairSearcher()._classify([self._row("part-a", 0.93)])

        self.assertEqual(len(manifest["verified_pairs"]), 1)
        self.assertTrue(manifest["verified_pairs"][0]["eligible_for_auto_verification"])
        self.assertEqual(manifest["verified_pairs"][0]["matched_dxf_view_ids"], ["view_001"])

    def test_near_tied_components_remain_candidates(self):
        rows = [self._row("part-a", 0.93), self._row("part-b", 0.91)]
        manifest = GlobalGeometryPairSearcher()._classify(rows)

        self.assertEqual(manifest["verified_pairs"], [])
        self.assertEqual(len(manifest["candidates"]), 2)
        checks = manifest["candidates"][0]["geometry_search_evidence"]["checks"]
        self.assertFalse(checks["unambiguous_component_margin"])


if __name__ == "__main__":
    unittest.main()
