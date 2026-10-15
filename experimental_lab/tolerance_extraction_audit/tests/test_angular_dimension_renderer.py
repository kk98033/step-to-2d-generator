import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.angular_dimension_renderer import (
    build_angular_render_tasks,
    minor_arc,
)


class AngularDimensionRendererTests(unittest.TestCase):
    def test_minor_arc_crosses_zero_without_using_long_way(self):
        start, end, span = minor_arc(10.0, 350.0)
        self.assertEqual((start, end, span), (350.0, 10.0, 20.0))

    def test_tasks_use_measured_relations_and_separate_lanes(self):
        relations = [
            {"start_angle": 0.0, "end_angle": 30.0, "angle": 30.0},
            {"start_angle": 0.0, "end_angle": 60.0, "angle": 60.0},
        ]
        tasks = build_angular_render_tasks(
            relations,
            [{"target_angle": 30.0}, {"target_angle": 60.0}],
            base_radius=50.0,
            lane_spacing=8.0,
        )
        self.assertEqual([item["angle"] for item in tasks], [30.0, 60.0])
        self.assertEqual([item["radius"] for item in tasks], [50.0, 58.0])


if __name__ == "__main__":
    unittest.main()
