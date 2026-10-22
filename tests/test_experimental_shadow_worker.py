import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import cad_annotation_worker


class ExperimentalShadowWorkerTests(unittest.TestCase):
    def test_analysis_only_report_is_written_without_rendering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            step_path = root / "part.step"
            step_path.write_text("fixture", encoding="utf-8")
            output_dir = root / "shadow"
            fake_report = {
                "schema_version": 1,
                "method": "DRAWING_EXPERIMENTAL_MAIN_V0_26",
                "mode": "ANALYSIS_ONLY",
                "active_detectors": ["solid_shaft_features"],
                "full_drawing_render_ready": False,
                "dimension_intents": {"unified_intent_count": 3},
            }
            fake_module = types.SimpleNamespace(analyse_step=lambda path: fake_report)
            with patch.object(
                cad_annotation_worker,
                "_load_experimental_main_module",
                return_value=fake_module,
            ):
                result = cad_annotation_worker.run_job({
                    "action": "experimental_shadow_analysis",
                    "step_path": str(step_path),
                    "output_dir": str(output_dir),
                })

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["method"], "DRAWING_EXPERIMENTAL_MAIN_V0_26")
            self.assertEqual(result["unified_intent_count"], 3)
            self.assertFalse(result["full_drawing_render_ready"])
            self.assertTrue((output_dir / "drawing_engine_report.json").is_file())

    def test_rejects_non_analysis_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            step_path = root / "part.step"
            step_path.write_text("fixture", encoding="utf-8")
            fake_module = types.SimpleNamespace(analyse_step=lambda path: {
                "schema_version": 1,
                "method": "DRAWING_EXPERIMENTAL_MAIN_V0_26",
                "mode": "ANALYSIS_AND_ENGINEER_SELECTED_DRAWING",
            })
            with patch.object(
                cad_annotation_worker,
                "_load_experimental_main_module",
                return_value=fake_module,
            ):
                with self.assertRaisesRegex(RuntimeError, "unexpectedly requested drawing"):
                    cad_annotation_worker.run_job({
                        "action": "experimental_shadow_analysis",
                        "step_path": str(step_path),
                        "output_dir": str(root / "shadow"),
                    })

    def test_experimental_candidates_are_returned_as_renderable_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            step_path = root / "part.step"
            step_path.write_text("fixture", encoding="utf-8")
            fake_report = {
                "schema_version": 1,
                "method": "DRAWING_EXPERIMENTAL_MAIN_V0_26",
                "mode": "ANALYSIS_ONLY",
                "dimension_intents": {"intents": []},
                "detectors": {
                    "radial_pattern_bands": {
                        "best_view": {
                            "confidence": 0.9,
                            "candidate_rules": [{
                                "rule_id": "outer_annular_slots.full_angle",
                                "dim_type": "ANGULAR",
                                "nominal_value": 20.0,
                            }],
                        },
                    },
                },
            }
            fake_module = types.SimpleNamespace(analyse_step=lambda path: fake_report)
            with patch.object(
                cad_annotation_worker,
                "_load_experimental_main_module",
                return_value=fake_module,
            ), patch.object(
                cad_annotation_worker,
                "_load_shape",
                return_value=object(),
            ), patch(
                "auto_2d_drawing.smart_extractors.experimental_feature_bridge."
                "bridge_experimental_candidate_rules",
                side_effect=lambda _shape, rules, **_kwargs: (
                    rules,
                    {
                        "matched_count": 0,
                        "virtual_relation_count": 1,
                        "unresolved_count": 0,
                    },
                ),
            ):
                result = cad_annotation_worker.run_job({
                    "action": "experimental_candidates",
                    "step_path": str(step_path),
                })

            self.assertEqual(result["candidate_engine"], "experimental_v026")
            self.assertEqual(result["rule_count"], 1)
            self.assertTrue(result["rules"][0]["render_eligible"])
            self.assertEqual(result["feature_bridge"]["virtual_relation_count"], 1)

    def test_experimental_render_routes_only_enabled_authoritative_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            step_path = root / "part.step"
            step_path.write_text("fixture", encoding="utf-8")
            output_dir = root / "drawing"
            fake_report = {
                "schema_version": 1,
                "method": "DRAWING_EXPERIMENTAL_MAIN_V0_26",
                "mode": "ANALYSIS_ONLY",
                "dimension_intents": {"intents": []},
                "detectors": {
                    "radial_pattern_bands": {
                        "best_view": {
                            "confidence": 0.9,
                            "candidate_rules": [{
                                "rule_id": "outer_annular_slots.full_angle",
                                "dim_type": "ANGULAR",
                                "nominal_value": 20.0,
                                "preferred_view": "front",
                            }],
                        },
                    },
                },
            }
            captured = {}

            def fake_render(_step, _selections, output, **kwargs):
                captured.update(kwargs)
                front = output / "front"
                front.mkdir(parents=True)
                files = {}
                for extension in ("dxf", "pdf", "png"):
                    path = front / f"front.{extension}"
                    path.write_text("drawing", encoding="utf-8")
                    files[extension] = str(path)
                (output / "minimal_multiview_report.json").write_text("{}", encoding="utf-8")
                return {
                    "success": True,
                    "orthographic_drawings": [{"view": "front", "files": files}],
                    "half_section_drawing": None,
                }

            fake_module = types.SimpleNamespace(
                analyse_step=lambda path: fake_report,
                render_minimal_multiview_drawing=fake_render,
            )
            with patch.object(
                cad_annotation_worker,
                "_load_experimental_main_module",
                return_value=fake_module,
            ):
                result = cad_annotation_worker.run_job({
                    "action": "experimental_render",
                    "step_path": str(step_path),
                    "part_id": "part",
                    "output_dir": str(output_dir),
                    "feature_records": [{
                        "rule_id": "outer_annular_slots.full_angle",
                        "source_engine": "experimental_v026",
                        "enabled": True,
                    }],
                })

            self.assertEqual(result["selection_summary"]["routed"], 1)
            self.assertEqual(
                captured["radial_band_angle_selections"][0]["feature_id"],
                "outer_annular_slots.full_angle",
            )

    def test_experimental_render_failure_persists_full_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            step_path = root / "part.step"
            step_path.write_text("fixture", encoding="utf-8")
            output_dir = root / "drawing"
            fake_report = {
                "schema_version": 1,
                "method": "DRAWING_EXPERIMENTAL_MAIN_V0_26",
                "mode": "ANALYSIS_ONLY",
                "dimension_intents": {"intents": []},
                "detectors": {
                    "radial_pattern_bands": {
                        "best_view": {
                            "confidence": 0.9,
                            "candidate_rules": [{
                                "rule_id": "outer_annular_slots.full_angle",
                                "dim_type": "ANGULAR",
                                "nominal_value": 20.0,
                                "preferred_view": "front",
                            }],
                        },
                    },
                },
            }
            drawing_report = {
                "success": False,
                "rejected_feature_selections": [],
                "radial_band_angle_placement": {
                    "minimum_group_separation_deg": -2.61,
                    "placements": [{"rule_id": "outer_annular_slots.full_angle"}],
                },
            }
            fake_module = types.SimpleNamespace(
                analyse_step=lambda path: fake_report,
                render_minimal_multiview_drawing=lambda *args, **kwargs: drawing_report,
            )
            with patch.object(
                cad_annotation_worker,
                "_load_experimental_main_module",
                return_value=fake_module,
            ):
                with self.assertRaisesRegex(RuntimeError, "renderer audit failed"):
                    cad_annotation_worker.run_job({
                        "action": "experimental_render",
                        "step_path": str(step_path),
                        "part_id": "part",
                        "output_dir": str(output_dir),
                        "feature_records": [{
                            "rule_id": "outer_annular_slots.full_angle",
                            "source_engine": "experimental_v026",
                            "enabled": True,
                        }],
                    })

            audit_path = output_dir / "experimental_renderer_failure_audit.json"
            self.assertTrue(audit_path.is_file())
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "error")
            self.assertEqual(
                audit["drawing_report"]["radial_band_angle_placement"]
                ["minimum_group_separation_deg"],
                -2.61,
            )
            self.assertEqual(
                audit["submitted_feature_records"][0]["rule_id"],
                "outer_annular_slots.full_angle",
            )


if __name__ == "__main__":
    unittest.main()
