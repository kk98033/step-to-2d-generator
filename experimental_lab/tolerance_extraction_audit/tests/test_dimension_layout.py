import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.dimension_layout import (
    count_lane_collisions,
    plan_dimension_layout,
)


def intent(intent_id, role, anchors, nominal=10.0, status="AUTO_VERIFIED"):
    return {
        "intent_id": intent_id,
        "measurement": {"role": role, "semantic": role.lower(), "nominal_value": nominal},
        "projection": {"anchors": anchors},
        "renderer_contract": {"prefix": "Ø" if "DIAMETER" in role else "", "renderer": "TEST"},
        "verification": {"status": status},
    }


class DimensionLayoutTests(unittest.TestCase):
    def test_overlapping_intervals_receive_distinct_lanes(self):
        plan = plan_dimension_layout([
            intent("a", "OUTER_DIAMETER", [[-10, 0], [10, 0]], 20),
            intent("b", "INNER_DIAMETER", [[-8, 0], [8, 0]], 16),
        ], [-12, -12, 12, 12])
        self.assertEqual(len(plan["placements"]), 2)
        self.assertEqual(count_lane_collisions(plan), 0)

    def test_overall_length_is_outer_than_segment_lengths(self):
        plan = plan_dimension_layout([
            intent("overall", "AXIAL_OVERALL_LENGTH", [[0, 0], [20, 0]], 20),
            intent("segment", "AXIAL_SEGMENT_LENGTH", [[0, 0], [10, 0]], 10),
        ], [0, -2, 20, 2])
        by_id = {item["intent_id"]: item for item in plan["placements"]}
        self.assertEqual(by_id["overall"]["side"], "BOTTOM")
        self.assertGreater(by_id["overall"]["lane"], by_id["segment"]["lane"])

    def test_review_candidate_is_visible_but_invalid_is_skipped(self):
        plan = plan_dimension_layout([
            intent("review", "OUTER_DIAMETER", [[-5, 0], [5, 0]], status="REVIEW_CANDIDATE"),
            intent("invalid", "OUTER_DIAMETER", [[-5, 0], [5, 0]], status="INVALID"),
        ], [-6, -6, 6, 6])
        self.assertEqual([item["intent_id"] for item in plan["placements"]], ["review"])
        self.assertEqual(plan["skipped"][0]["intent_id"], "invalid")

    def test_layout_is_scale_relative(self):
        small = plan_dimension_layout([intent("a", "OUTER_DIAMETER", [[-5, 0], [5, 0]])], [-5, -5, 5, 5])
        large = plan_dimension_layout([intent("a", "OUTER_DIAMETER", [[-50, 0], [50, 0]], 100)], [-50, -50, 50, 50])
        self.assertAlmostEqual(large["base_gap"] / small["base_gap"], 10.0, places=6)

    def test_vertical_side_uses_model_centre_not_world_origin(self):
        plan = plan_dimension_layout([
            intent("left", "OUTER_DIAMETER", [[101, -5], [101, 5]], 10),
            intent("right", "OUTER_DIAMETER", [[109, -5], [109, 5]], 10),
        ], [100, -6, 110, 6])
        by_id = {item["intent_id"]: item for item in plan["placements"]}
        self.assertEqual(by_id["left"]["side"], "LEFT")
        self.assertEqual(by_id["right"]["side"], "RIGHT")


if __name__ == "__main__":
    unittest.main()
