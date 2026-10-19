import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.rectangular_hole_renderer import (
    build_rectangular_hole_render_tasks,
)


class RectangularHoleRendererTests(unittest.TestCase):
    def test_builds_hole_diameter_and_two_center_distances(self):
        tasks = build_rectangular_hole_render_tasks({
            "diameter": 4.3,
            "horizontal_center_distance": 100.0,
            "vertical_center_distance": 80.0,
            "centers": [[0, 0], [100, 0], [0, 80], [100, 80]],
        })
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0]["radius"], 2.15)
        self.assertEqual(tasks[1]["nominal_value"], 100.0)
        self.assertEqual(tasks[2]["nominal_value"], 80.0)

    def test_rejects_non_rectangular_centres(self):
        with self.assertRaises(ValueError):
            build_rectangular_hole_render_tasks({
                "diameter": 4.0,
                "centers": [[0, 0], [10, 0], [3, 8], [10, 8]],
            })

    def test_rejects_reported_distance_mismatch(self):
        with self.assertRaises(ValueError):
            build_rectangular_hole_render_tasks({
                "diameter": 4.0,
                "horizontal_center_distance": 12.0,
                "vertical_center_distance": 8.0,
                "centers": [[0, 0], [10, 0], [0, 8], [10, 8]],
            })


if __name__ == "__main__":
    unittest.main()
