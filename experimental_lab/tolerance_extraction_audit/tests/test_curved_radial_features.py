import math
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "common" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from curved_radial_features import detect_curved_radial_partitions  # noqa: E402


def point(radius, angle):
    radians = math.radians(angle)
    return [radius * math.cos(radians), radius * math.sin(radians)]


def curved_view(count=8):
    visible = [
        {"type": "circle", "center": [0.0, 0.0], "radius": radius}
        for radius in (2.0, 4.0, 8.0)
    ]
    pitch = 360.0 / count
    for index in range(count):
        base = index * pitch
        for inner_radius, offset in ((10.0, -4.0), (10.5, 4.0)):
            inner_angle = base + offset
            visible.append({
                "type": "spline",
                "points": [point(inner_radius, inner_angle), point(20.0, inner_angle + 25.0)],
            })
    return {"visible": visible, "hidden": [], "bbox": (-25.0, -25.0, 25.0, 25.0)}


class CurvedRadialFeatureTests(unittest.TestCase):
    def test_repeated_curved_boundary_pairs_are_resolved(self):
        result = detect_curved_radial_partitions(curved_view())
        self.assertTrue(result["resolved"], result)
        self.assertEqual(len(result["elements"]), 8)
        self.assertAlmostEqual(result["center_pitches"][0]["angle"], 45.0, places=4)

    def test_isolated_curves_do_not_trigger(self):
        view = curved_view(count=3)
        result = detect_curved_radial_partitions(view)
        self.assertFalse(result["resolved"])


if __name__ == "__main__":
    unittest.main()
