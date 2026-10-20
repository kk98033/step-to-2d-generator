import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.radial_band_pattern_features import (
    build_radial_band_render_tasks,
)


def relation(start, angle):
    return {"start_angle": float(start), "end_angle": float(start + angle), "angle": float(angle)}


class RadialBandPatternFeatureTests(unittest.TestCase):
    def test_groups_half_and_full_angle_and_avoids_reserved_sector(self):
        outer_gaps = [relation(start, 20.0) for start in (170, 210, 250, 290, 330)]
        inner_pitch = [relation(start, 36.0) for start in (0, 72, 144, 216, 288)]
        inner_elements = [relation(start, 6.0) for start in (18, 90, 162, 234, 306)]
        result = {
            "center": [0.0, 0.0],
            "patterns": [
                {
                    "id": "outer",
                    "radial_role": "OUTER_ANNULAR_SLOT_PATTERN",
                    "repetition_count": 13,
                    "gap_angle": 20.0,
                    "half_gap_angle": 10.0,
                    "gap_relations": outer_gaps,
                    "half_gap_relations": [relation(item["start_angle"], 10.0) for item in outer_gaps],
                    "verification_status": "AUTO_VERIFIED",
                },
                {
                    "id": "inner",
                    "radial_role": "INNER_HUB_SLOT_PATTERN",
                    "repetition_count": 10,
                    "center_pitch_angle": 36.0,
                    "element_angle": 6.0,
                    "center_pitch_relations": inner_pitch,
                    "element_relations": inner_elements,
                    "verification_status": "AUTO_VERIFIED",
                },
            ],
        }
        trace = []
        tasks, rejected = build_radial_band_render_tasks(
            result,
            [
                {"feature_id": "outer_annular_slots.half_angle"},
                {"feature_id": "outer_annular_slots.full_angle"},
                {"feature_id": "inner_hub_slots.pitch_angle"},
                {"feature_id": "inner_hub_slots.element_angle"},
            ],
            view_name="front",
            view_span=100.0,
            reserved_sectors=[{"mid_angle": 325.0, "span": 60.0}],
            placement_trace=trace,
        )
        self.assertEqual(rejected, [])
        by_id = {task["id"]: task for task in tasks}
        full = by_id["outer_annular_slots.full_angle"]
        half = by_id["outer_annular_slots.half_angle"]
        self.assertEqual(full["placement_group"], half["placement_group"])
        self.assertAlmostEqual(half["angle"] * 2.0, full["angle"])
        self.assertEqual(half["start_angle"], full["start_angle"])
        independent_midpoints = [
            item["mid_angle"]
            for item in trace
            if item["rule_id"] != "outer_annular_slots.half_angle"
        ]
        self.assertEqual(len(set(independent_midpoints)), len(independent_midpoints))
        separations = [
            min(abs(first - second) % 360.0, 360.0 - abs(first - second) % 360.0)
            for index, first in enumerate(independent_midpoints)
            for second in independent_midpoints[index + 1:]
        ]
        self.assertGreaterEqual(min(separations), 45.0)
        self.assertTrue(all(task["extend_to_center"] for task in tasks))


if __name__ == "__main__":
    unittest.main()
