import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from web_app.backend import server


class ExperimentalShadowServerTests(unittest.TestCase):
    def setUp(self):
        with server.jobs_lock:
            self.original_jobs = dict(server.jobs)
            server.jobs.clear()
        with server.annotation_state_lock:
            self.original_annotation_jobs = dict(server.annotation_jobs)
            server.annotation_jobs.clear()

    def tearDown(self):
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs.update(self.original_jobs)
        with server.annotation_state_lock:
            server.annotation_jobs.clear()
            server.annotation_jobs.update(self.original_annotation_jobs)

    def _add_completed_job(self, job_id: str) -> None:
        with server.jobs_lock:
            server.jobs[job_id] = {
                "status": "completed",
                "message": "production drawing completed",
                "owner_user_id": "user-a",
                "created_at": "2026-01-01T00:00:00+00:00",
            }

    def test_disabled_shadow_records_state_without_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._add_completed_job("job-disabled")
            with patch.object(server, "EXPERIMENTAL_SHADOW_ANALYSIS_ENABLED", False):
                scheduled = server._schedule_experimental_shadow_analysis(
                    "job-disabled",
                    str(Path(temporary) / "part.step"),
                    temporary,
                    "model-id",
                )

            self.assertFalse(scheduled)
            self.assertEqual(server.jobs["job-disabled"]["status"], "completed")
            self.assertEqual(
                server.jobs["job-disabled"]["experimental_shadow_analysis"]["status"],
                "disabled",
            )
            self.assertTrue(
                (Path(temporary) / "_experimental_shadow" / "shadow_status.json").is_file()
            )

    def test_shadow_failure_is_contained_from_primary_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._add_completed_job("job-error")
            with patch.object(
                server,
                "_run_cad_annotation_worker",
                side_effect=RuntimeError("experimental failure"),
            ):
                server._run_experimental_shadow_job(
                    "job-error",
                    str(Path(temporary) / "part.step"),
                    temporary,
                    "model-id",
                )

            self.assertEqual(server.jobs["job-error"]["status"], "completed")
            shadow = server.jobs["job-error"]["experimental_shadow_analysis"]
            self.assertEqual(shadow["status"], "error")
            self.assertIn("experimental failure", shadow["error"])

    def test_shadow_success_exposes_versioned_report_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._add_completed_job("job-success")
            worker_result = {
                "status": "ok",
                "method": "DRAWING_EXPERIMENTAL_MAIN_V0_26",
                "schema_version": 1,
                "active_detectors": ["radial_pattern_bands"],
                "unified_intent_count": 4,
                "full_drawing_render_ready": False,
            }
            with patch.object(
                server,
                "_run_cad_annotation_worker",
                return_value=worker_result,
            ):
                server._run_experimental_shadow_job(
                    "job-success",
                    str(Path(temporary) / "part.step"),
                    temporary,
                    "model-id",
                )

            self.assertEqual(server.jobs["job-success"]["status"], "completed")
            shadow = server.jobs["job-success"]["experimental_shadow_analysis"]
            self.assertEqual(shadow["status"], "completed")
            self.assertEqual(shadow["method"], "DRAWING_EXPERIMENTAL_MAIN_V0_26")
            self.assertEqual(shadow["unified_intent_count"], 4)
            self.assertEqual(
                shadow["report_url"],
                "/api/files/model-id/_experimental_shadow/drawing_engine_report.json",
            )

    def test_experimental_candidates_are_cached_by_step_signature(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parts = root / "_parts"
            parts.mkdir()
            (parts / "part.step").write_text("fixture", encoding="utf-8")
            worker_result = {
                "status": "ok",
                "pid": 123,
                "candidate_engine": "experimental_v026",
                "candidate_schema_version": 1,
                "analysis_method": "DRAWING_EXPERIMENTAL_MAIN_V0_26",
                "rules": [{"rule_id": "candidate-1", "render_eligible": False}],
                "summary": {"total": 1, "render_eligible": 0},
            }
            with patch.object(server, "_safe_output_dir", return_value=str(root)), patch.object(
                server,
                "_run_cad_annotation_worker",
                return_value=worker_result,
            ) as worker:
                first = server._candidate_annotation_rules_impl(
                    "model-id", "part", "experimental_v026"
                )
                second = server._candidate_annotation_rules_impl(
                    "model-id", "part", "experimental_v026"
                )

            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            self.assertEqual(second["candidate_engine"], "experimental_v026")
            worker.assert_called_once()

    def test_force_refresh_recomputes_experimental_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parts = root / "_parts"
            parts.mkdir()
            (parts / "part.step").write_text("fixture", encoding="utf-8")
            worker_result = {
                "status": "ok",
                "pid": 123,
                "candidate_engine": "experimental_v026",
                "candidate_schema_version": 3,
                "rules": [{"rule_id": "candidate-1"}],
            }
            with patch.object(server, "_safe_output_dir", return_value=str(root)), patch.object(
                server,
                "_run_cad_annotation_worker",
                return_value=worker_result,
            ) as worker:
                server._candidate_annotation_rules_impl(
                    "model-id", "part", "experimental_v026"
                )
                refreshed = server._candidate_annotation_rules_impl(
                    "model-id", "part", "experimental_v026", force_refresh=True
                )

            self.assertFalse(refreshed["cached"])
            self.assertEqual(worker.call_count, 2)

    def test_parts_map_keeps_stl_only_parts_from_older_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parts = root / "_parts"
            parts.mkdir()
            (parts / "legacy-part.stp").write_text("step", encoding="utf-8")
            (parts / "legacy-part.stl").write_text("stl", encoding="utf-8")

            parts_map = server.build_parts_map(str(root), "legacy-model_batch")

            self.assertIn("legacy-part", parts_map)
            self.assertEqual(
                parts_map["legacy-part"]["stl"],
                "/api/files/legacy-model_batch/_parts/legacy-part.stl",
            )

    def test_non_admin_cannot_force_candidate_reanalysis(self):
        engineer = types.SimpleNamespace(
            id="00000000-0000-0000-0000-000000000002",
            is_admin=False,
        )
        with patch.object(server, "_candidate_annotation_rules_impl") as implementation:
            with self.assertRaises(HTTPException) as raised:
                server.get_candidate_annotation_rules(
                    "model-id",
                    "part-id",
                    "legacy_smart",
                    refresh=True,
                    current_user=engineer,
                )
        self.assertEqual(raised.exception.status_code, 403)
        implementation.assert_not_called()

    def test_candidate_engine_mismatch_is_rejected_by_render_api(self):
        admin = types.SimpleNamespace(
            id="00000000-0000-0000-0000-000000000001",
            is_admin=True,
        )
        with self.assertRaises(HTTPException) as raised:
            server.submit_custom_annotation_drawing(
                {
                    "model_id": "model-id",
                    "part_id": "part-id",
                    "candidate_engine": "experimental_v026",
                    "feature_records": [{
                        "rule_id": "outer_annular_slots.full_angle",
                        "source_engine": "legacy_smart",
                        "enabled": True,
                    }],
                },
                current_user=admin,
            )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(
            raised.exception.detail["code"],
            "CANDIDATE_ENGINE_MISMATCH",
        )

    def test_non_admin_cannot_render_with_experimental_engine(self):
        engineer = types.SimpleNamespace(
            id="00000000-0000-0000-0000-000000000002",
            is_admin=False,
        )
        with self.assertRaises(HTTPException) as raised:
            server.submit_custom_annotation_drawing(
                {
                    "model_id": "model-id",
                    "part_id": "part-id",
                    "candidate_engine": "experimental_v026",
                    "feature_records": [],
                },
                current_user=engineer,
            )
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(
            raised.exception.detail["code"],
            "EXPERIMENTAL_ENGINE_ADMIN_ONLY",
        )

    def test_non_admin_cannot_request_experimental_candidates(self):
        engineer = types.SimpleNamespace(
            id="00000000-0000-0000-0000-000000000002",
            is_admin=False,
        )
        with patch.object(server, "_candidate_annotation_rules_impl") as implementation:
            with self.assertRaises(HTTPException) as raised:
                server.get_candidate_annotation_rules(
                    "model-id",
                    "part-id",
                    "experimental_v026",
                    current_user=engineer,
                )
        self.assertEqual(raised.exception.status_code, 403)
        implementation.assert_not_called()

    def test_experimental_render_is_queued_in_isolated_output_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parts = root / "_parts"
            parts.mkdir()
            (parts / "part.step").write_text("fixture", encoding="utf-8")
            user = types.SimpleNamespace(
                id="00000000-0000-0000-0000-000000000001",
                is_admin=True,
            )
            with patch.object(server, "_safe_output_dir", return_value=str(root)), patch.object(
                server, "assert_model_access"
            ), patch.object(
                server.identity_store, "validate_recommendation_tag_ids", return_value=[]
            ), patch.object(server.annotation_executor, "submit") as submit:
                response = server.submit_custom_annotation_drawing(
                    {
                        "model_id": "model-id",
                        "part_id": "part",
                        "candidate_engine": "experimental_v026",
                        "feature_records": [{
                            "rule_id": "outer_annular_slots.full_angle",
                            "source_engine": "experimental_v026",
                            "preferred_view": "front",
                            "enabled": True,
                        }],
                    },
                    current_user=user,
                )

            job = server.annotation_jobs[response["job_id"]]
            self.assertEqual(job["candidate_engine"], "experimental_v026")
            self.assertIn("_experimental_annotations", job["custom_out_dir"])
            self.assertTrue(job["output_files"]["dxf_url"].endswith("/front/front.dxf"))
            audit_path = Path(job["audit_path"])
            self.assertTrue(audit_path.is_file())
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(audit["operation_id"], response["job_id"])
            self.assertEqual(audit["engineer"]["id"], user.id)
            self.assertEqual(
                audit["request"]["feature_records"][0]["rule_id"],
                "outer_annular_slots.full_angle",
            )
            server._set_annotation_job_state(
                response["job_id"],
                status="error",
                error="新版繪圖引擎稽核未通過，完整診斷已保存",
                error_detail="Experimental renderer audit failed: full payload",
                error_type="RuntimeError",
                completed_at=server._utc_iso(),
            )
            failed_audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(failed_audit["status"], "error")
            self.assertIn("full payload", failed_audit["failure"]["full_error"])
            status = server._annotation_job_status_payload(
                response["job_id"], server.annotation_jobs[response["job_id"]]
            )
            self.assertNotIn("full payload", status["error"])
            self.assertEqual(status["audit_url"], job["audit_url"])
            submit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
