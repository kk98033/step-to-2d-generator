import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.run_fan_section_render_experiment import (
    _candidate_angles,
    _closed_loops,
    classify_component_role,
)


class FanSectionRenderExperimentTests(unittest.TestCase):
    def test_component_roles_come_from_relative_radial_span(self):
        assembly = (-50, -50, -10, 50, 50, 10)
        self.assertEqual(classify_component_role((-2, -2, -8, 2, 2, 8), assembly, 2), "CENTRAL_SHAFT")
        self.assertEqual(classify_component_role((-20, -20, -8, 20, 20, 8), assembly, 2), "CENTRAL_HUB")
        self.assertEqual(classify_component_role((-48, -48, -8, 48, 48, 8), assembly, 2), "ROTATING_ENVELOPE")

    def test_angle_scan_covers_half_turn_without_duplicate_endpoint(self):
        angles = _candidate_angles(30)
        self.assertEqual(angles, [0.0, 30.0, 60.0, 90.0, 120.0, 150.0])

    def test_open_edges_are_stitched_into_closed_section_loop(self):
        paths = [
            [(0.0, 0.0), (1.0, 0.0)],
            [(1.0, 1.0), (0.0, 1.0)],
            [(1.0, 0.0), (1.0, 1.0)],
            [(0.0, 1.0), (0.0, 0.0)],
        ]
        loops = _closed_loops(paths, 1e-6)
        self.assertEqual(len(loops), 1)
        self.assertLessEqual(abs(loops[0][0][0] - loops[0][-1][0]), 1e-6)


if __name__ == "__main__":
    unittest.main()
