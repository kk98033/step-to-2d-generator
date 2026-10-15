from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "common" / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blade_profile_semantics import (  # noqa: E402
    measure_curve_endpoint_angles, trace_outward_boundary_chain,
)


def rotate(point, angle_degrees):
    angle = math.radians(angle_degrees)
    return (
        point[0] * math.cos(angle) - point[1] * math.sin(angle),
        point[0] * math.sin(angle) + point[1] * math.cos(angle),
    )


class BladeProfileSemanticsTests(unittest.TestCase):
    def test_endpoint_offsets_are_rotation_and_order_invariant(self):
        points = [(2.0, 0.0), (3.0, 0.7), (4.2, 1.8), (5.4, 3.2)]
        baseline = measure_curve_endpoint_angles(points, (0.0, 0.0), 0.2)
        rotated = measure_curve_endpoint_angles(
            [rotate(point, 137.0) for point in reversed(points)], (0.0, 0.0), 0.2
        )
        self.assertIsNotNone(baseline)
        self.assertIsNotNone(rotated)
        self.assertAlmostEqual(
            baseline["inner_relative_to_radial"], rotated["inner_relative_to_radial"], places=6
        )
        self.assertAlmostEqual(
            baseline["outer_relative_to_radial"], rotated["outer_relative_to_radial"], places=6
        )

    def test_outward_chain_joins_connected_segments(self):
        view = {
            "bbox": (-6.0, -6.0, 6.0, 6.0),
            "visible": [
                {"type": "spline", "points": [(2.0, 0.0), (3.0, 0.5)]},
                {"type": "spline", "points": [(3.0, 0.5), (4.2, 1.2)]},
                {"type": "spline", "points": [(4.2, 1.2), (5.0, 2.0)]},
                {"type": "circle", "points": [(5.0, 0.0), (0.0, 5.0)]},
            ],
            "hidden": [],
        }
        result = trace_outward_boundary_chain(
            view, "visible:0", (0.0, 0.0), connection_tolerance_fraction=0.01
        )
        self.assertTrue(result["resolved"])
        self.assertEqual(result["segment_count"], 3)
        self.assertEqual(result["edge_refs"], ["visible:0", "visible:1", "visible:2"])


if __name__ == "__main__":
    unittest.main()
