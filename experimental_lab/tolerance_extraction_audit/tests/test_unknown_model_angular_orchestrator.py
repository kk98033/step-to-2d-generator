import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "common" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from unknown_model_angular_orchestrator import (  # noqa: E402
    assess_radial_trigger,
    select_automatic_angle_specifications,
)


def resolved_fixture():
    centres = [10.0, 30.0, 60.0, 90.0]
    elements = [
        {"angle": 2.5, "left_boundary_ref": f"visible:{index * 2}", "right_boundary_ref": f"visible:{index * 2 + 1}"}
        for index in range(4)
    ]
    gaps = [{"angle": value} for value in (17.5, 27.5, 27.5, 287.5)]
    pitches = [
        {"start_angle": 10.0, "end_angle": 30.0, "angle": 20.0},
        {"start_angle": 30.0, "end_angle": 60.0, "angle": 30.0},
        {"start_angle": 60.0, "end_angle": 90.0, "angle": 30.0},
        {"start_angle": 90.0, "end_angle": 10.0, "angle": 280.0},
    ]
    pairwise = []
    for left_index, left in enumerate(centres):
        for right_index, right in enumerate(centres[left_index + 1:], start=left_index + 1):
            value = min((right - left) % 360.0, (left - right) % 360.0)
            pairwise.append({"left_element_index": left_index, "right_element_index": right_index, "start_angle": left, "end_angle": right, "angle": value})
    return {
        "resolved": True,
        "boundary_angles": list(range(8)),
        "elements": elements,
        "gaps": gaps,
        "center_pitches": pitches,
        "pairwise_center_relations": pairwise,
        "center_evidence": {"method": "CONCENTRIC_PROJECTED_CIRCLES", "confidence": 0.95},
        "candidate_line_count": 10,
        "selected_line_count": 8,
        "split_ratio": 7.0,
    }


class UnknownModelAngularOrchestratorTests(unittest.TestCase):
    def test_trigger_requires_concentric_geometry_evidence(self):
        result = resolved_fixture()
        result["center_evidence"] = {"method": "BBOX_FALLBACK", "confidence": 0.25}
        self.assertFalse(assess_radial_trigger(result)["eligible"])

    def test_resolved_repeated_radial_structure_passes(self):
        assessment = assess_radial_trigger(resolved_fixture())
        self.assertTrue(assessment["eligible"])
        self.assertGreaterEqual(assessment["score"], 0.78)

    def test_selection_derives_local_and_compound_relations(self):
        specs = select_automatic_angle_specifications(resolved_fixture())
        self.assertEqual([round(item["target_angle"]) for item in specs], [20, 30, 50])
        self.assertEqual(specs[-1]["selection_reason"], "ACTUAL_PAIRWISE_RELATION_NEAR_LOCAL_SPACING_SUM")


if __name__ == "__main__":
    unittest.main()
