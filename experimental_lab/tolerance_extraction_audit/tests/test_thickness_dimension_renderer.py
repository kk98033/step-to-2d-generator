import math
import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.thickness_dimension_renderer import (
    build_thickness_render_task,
    format_thickness_label,
)


class ThicknessDimensionRendererTests(unittest.TestCase):
    def _intent(self):
        return {
            "intent_id": "thin_wall_1.thickness",
            "measurement": {
                "semantic": "thin_wall.normal_thickness",
                "role": "NORMAL_WALL_THICKNESS",
                "nominal_value": 0.2513,
                "display_value": 0.25,
                "tolerance": {"mode": "SYMMETRIC", "upper": 0.03, "lower": -0.03},
                "tolerance_source": "HISTORICAL_CASE:test",
            },
            "projection": {"anchors": [[5.0, 2.0], [5.2513, 2.0]]},
            "verification": {"status": "AUTO_VERIFIED"},
        }

    def test_formats_released_value_and_tolerance(self):
        self.assertEqual(format_thickness_label(self._intent()), "0.25 +/- 0.030 mm")

    def test_builds_local_parallel_dimension_line(self):
        task = build_thickness_render_task(self._intent(), [0, 0, 20, 10])
        source = task["source_anchors"]
        placed = task["dimension_points"]
        source_vector = [source[1][0] - source[0][0], source[1][1] - source[0][1]]
        placed_vector = [placed[1][0] - placed[0][0], placed[1][1] - placed[0][1]]
        cross = source_vector[0] * placed_vector[1] - source_vector[1] * placed_vector[0]
        self.assertAlmostEqual(cross, 0.0, places=9)
        self.assertAlmostEqual(math.dist(*source), math.dist(*placed), places=9)
        self.assertEqual(task["renderer"], "THICKNESS_RENDERER")
        self.assertEqual(task["tolerance_source"], "HISTORICAL_CASE:test")


if __name__ == "__main__":
    unittest.main()
