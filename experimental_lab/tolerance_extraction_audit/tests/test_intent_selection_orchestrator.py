import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.intent_selection_orchestrator import (
    build_orchestration_plan,
)


def intent(intent_id, family, role, confidence=0.95, status="AUTO_VERIFIED", view="front"):
    return {
        "intent_id": intent_id,
        "feature": {"family": family},
        "measurement": {"role": role, "semantic": role.lower(), "nominal_value": 1.0},
        "projection": {"preferred_view": view, "anchors": [[0.0, 0.0], [1.0, 0.0]]},
        "renderer_contract": {"renderer": "TEST_RENDERER"},
        "verification": {"status": status, "confidence": confidence},
    }


def report(intents, *, thin=None, shafts=None, holes=None, radial=None):
    return {
        "method": "TEST_ANALYSIS",
        "source_step": "unknown.step",
        "dimension_intents": {"intents": intents},
        "detectors": {
            "paired_surface_thickness": thin or {"resolved": False, "features": []},
            "solid_shaft_features": {"chains": shafts or []},
            "rectangular_hole_relations": holes or {"resolved": False},
            "hub_radial_relations": {"best_view": radial or {"resolved": False}},
        },
    }


class IntentSelectionOrchestratorTests(unittest.TestCase):
    def test_complete_shaft_chain_is_selected_without_part_name(self):
        analysis = report(
            [intent("overall", "SOLID_SHAFT", "AXIAL_OVERALL_LENGTH")],
            shafts=[{
                "id": "chain",
                "coverage_ratio": 0.92,
                "confidence": 0.98,
                "segments": [{}, {}, {}],
                "shoulders": [{}, {}],
            }],
        )
        plan = build_orchestration_plan(analysis)
        self.assertEqual(plan["selected_intents"][0]["selection_status"], "AUTO_DISPATCH")

    def test_weak_single_segment_shaft_is_suppressed(self):
        analysis = report(
            [intent("shaft", "SOLID_SHAFT", "OUTER_DIAMETER", confidence=0.75)],
            shafts=[{
                "id": "chain",
                "coverage_ratio": 0.08,
                "confidence": 0.75,
                "segments": [{}],
                "shoulders": [],
            }],
        )
        plan = build_orchestration_plan(analysis)
        self.assertEqual(plan["suppressed_intents"][0]["selection_status"], "SUPPRESSED")
        self.assertIn(
            "WEAK_SHAFT_CHAIN_COVERAGE_OR_CONTINUITY",
            plan["suppressed_intents"][0]["reasons"],
        )

    def test_dominant_thin_skin_suppresses_unbacked_competing_family(self):
        thin = {
            "resolved": True,
            "features": [{
                "id": "wall",
                "detector_candidate": {"score": 0.96, "area_ratio": 0.95, "area_coverage": 0.44},
            }],
        }
        analysis = report([
            intent("wall", "THIN_WALL", "NORMAL_WALL_THICKNESS", status="REVIEW_CANDIDATE"),
            intent("ring", "ANNULAR_SECTION", "OUTER_DIAMETER"),
        ], thin=thin)
        plan = build_orchestration_plan(analysis)
        self.assertEqual([item["intent_id"] for item in plan["selected_intents"]], ["wall"])
        self.assertEqual([item["intent_id"] for item in plan["suppressed_intents"]], ["ring"])

    def test_verified_history_can_restore_competing_family(self):
        thin = {
            "resolved": True,
            "features": [{
                "id": "wall",
                "detector_candidate": {"score": 0.96, "area_ratio": 0.95, "area_coverage": 0.44},
            }],
        }
        analysis = report([intent("ring", "ANNULAR_SECTION", "OUTER_DIAMETER")], thin=thin)
        plan = build_orchestration_plan(analysis, {
            "outer_diameter": {"verified": True, "case_count": 5, "selection_frequency": 0.9},
        })
        self.assertEqual(plan["selected_intents"][0]["intent_id"], "ring")

    def test_radial_relations_use_measured_values(self):
        radial = {
            "resolved": True,
            "view": "front",
            "center_pitch_relations": [
                {"start_angle": 0.0, "end_angle": 20.0, "angle": 20.0},
                {"start_angle": 20.0, "end_angle": 50.0, "angle": 30.0},
            ],
            "pairwise_center_relations": [
                {"start_angle": 0.0, "end_angle": 50.0, "angle": 50.0},
            ],
        }
        plan = build_orchestration_plan(report([], radial=radial))
        angular = plan["supplemental_dispatch"][0]
        self.assertEqual([item["angle"] for item in angular["relations"]], [20.0, 30.0, 50.0])

    def test_verified_hole_pattern_remains_blocked_without_renderer(self):
        holes = {"resolved": True, "view": "top", "intents": [{"semantic": "hole.diameter"}]}
        plan = build_orchestration_plan(report([], holes=holes))
        self.assertEqual(
            plan["supplemental_dispatch"][0]["selection_status"],
            "BLOCKED_UNSUPPORTED_RENDERER",
        )
        self.assertFalse(plan["renderer_dispatch_ready"])
        self.assertFalse(plan["full_drawing_render_ready"])

    def test_dominant_thin_skin_suppresses_unbacked_hole_pattern(self):
        thin = {
            "resolved": True,
            "features": [{
                "id": "wall",
                "detector_candidate": {"score": 0.96, "area_ratio": 0.95, "area_coverage": 0.44},
            }],
        }
        holes = {"resolved": True, "view": "top", "intents": [{"semantic": "hole.diameter"}]}
        plan = build_orchestration_plan(report([
            intent("wall", "THIN_WALL", "NORMAL_WALL_THICKNESS", status="REVIEW_CANDIDATE"),
        ], thin=thin, holes=holes))
        self.assertEqual(
            plan["supplemental_dispatch"][0]["selection_status"],
            "SUPPRESSED_BY_COMPATIBILITY",
        )
        self.assertTrue(plan["renderer_dispatch_ready"])
        self.assertFalse(plan["full_drawing_render_ready"])


if __name__ == "__main__":
    unittest.main()
