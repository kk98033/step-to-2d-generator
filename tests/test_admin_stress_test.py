import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, PropertyMock

from tools.admin_stress_test_runner import _capacity_planning, _recommend
from tools import model_reanalysis_worker
from tools.model_output_swap_worker import promote_reanalysis, replace_directory
from web_app.backend import server
from web_app.backend.stress_test_service import StressTestService
from web_app.backend.runtime_control_service import RuntimeControlService
from web_app.backend.presence_service import PresenceService


def _round(
    level: int, *, failures: int = 0, availability: float = 100.0,
    memory: float = 55.0, cpu: float = 70.0,
):
    return {
        "level": level,
        "round": 1,
        "failures": failures,
        "timed_out": False,
        "throughput_jobs_per_second": float(level),
        "health": {
            "availability_percent": availability,
            "latency_p95_seconds": 0.05,
        },
        "resources": {
            "cpu_peak_percent": cpu,
            "memory_peak_percent": memory,
        },
    }


class StressRecommendationTests(unittest.TestCase):
    def test_keeps_one_level_headroom_when_tested_ceiling_passes(self):
        recommendation = _recommend([_round(1), _round(2), _round(3), _round(4)], 4)
        self.assertEqual(recommendation["verified_active_heavy_user_ceiling"], 4)
        self.assertEqual(recommendation["recommended_active_heavy_users"], 3)
        self.assertEqual(recommendation["headroom_policy"], "ONE_LEVEL_BELOW_TESTED_CEILING")
        self.assertFalse(recommendation["capacity_boundary_found"])

    def test_stops_contiguous_ceiling_at_first_failed_level(self):
        recommendation = _recommend([_round(1), _round(2), _round(3, failures=1)], 4)
        self.assertEqual(recommendation["verified_active_heavy_user_ceiling"], 2)
        self.assertEqual(recommendation["recommended_active_heavy_users"], 2)
        self.assertFalse(recommendation["level_assessments"][-1]["passed"])
        self.assertTrue(recommendation["capacity_boundary_found"])

    def test_memory_pressure_fails_level(self):
        recommendation = _recommend([_round(1), _round(2, memory=91.0)], 2)
        self.assertEqual(recommendation["verified_active_heavy_user_ceiling"], 1)
        self.assertIn("MEMORY_PRESSURE_ABOVE_90", recommendation["level_assessments"][1]["reasons"])

    def test_cpu_pressure_fails_level(self):
        recommendation = _recommend([_round(1), _round(2, cpu=92.0)], 2)
        self.assertEqual(recommendation["verified_active_heavy_user_ceiling"], 1)
        self.assertIn("CPU_PRESSURE_ABOVE_85", recommendation["level_assessments"][1]["reasons"])

    def test_does_not_recommend_unverified_single_user_capacity(self):
        recommendation = _recommend([_round(1, failures=1)], 4)
        self.assertEqual(recommendation["verified_active_heavy_user_ceiling"], 0)
        self.assertEqual(recommendation["recommended_active_heavy_users"], 0)
        self.assertEqual(recommendation["headroom_policy"], "NO_SAFE_LEVEL_VERIFIED")

    def test_adaptive_sparse_levels_find_exact_boundary(self):
        recommendation = _recommend(
            [_round(1), _round(2), _round(4, failures=1), _round(3)],
            8,
        )
        self.assertEqual(recommendation["verified_active_heavy_user_ceiling"], 3)
        self.assertEqual(recommendation["recommended_active_heavy_users"], 3)
        self.assertTrue(recommendation["capacity_boundary_found"])

    def test_capacity_planning_models_fifo_wait_for_one_hundred_users(self):
        result = _round(7)
        result.update({
            "workload": "annotation",
            "workers": [
                {"workload": "annotation", "elapsed_seconds": 10.0}
                for _ in range(7)
            ],
            "wall_seconds": 10.0,
        })
        capacity = _capacity_planning([result], {
            "recommended_active_heavy_users": 7,
            "recommended_annotation_concurrency": 7,
            "verified_annotation_ceiling": 13,
            "verified_active_heavy_user_ceiling": 13,
        })
        pool = capacity["pools"][0]
        scenario = next(item for item in pool["queue_scenarios"] if item["submitted_users"] == 100)
        self.assertEqual(capacity["recommended_simultaneous_heavy_users"], 7)
        self.assertEqual(scenario["active_immediately"], 7)
        self.assertEqual(scenario["queued_initially"], 93)
        self.assertEqual(scenario["last_user_starts_after_seconds"], 140.0)
        self.assertEqual(scenario["estimated_queue_drain_seconds"], 150.0)


class StressTestServiceTests(unittest.TestCase):
    def test_fixture_catalog_is_bounded_and_report_recommendation_is_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models = root / "models"
            output = root / "output"
            tools = root / "tools"
            models.mkdir()
            output.mkdir()
            tools.mkdir()
            (models / "fixture.stp").write_text("ISO-10303-21;", encoding="utf-8")
            nested = models / "_users" / "user-a" / "job-a"
            nested.mkdir(parents=True)
            (nested / "uploaded.step").write_text("ISO-10303-21;", encoding="utf-8")
            service = StressTestService(root, output, models)
            fixtures = service.fixture_catalog()
            self.assertEqual(len(fixtures), 2)
            fixture = next(item for item in fixtures if item["name"] == "fixture.stp")
            self.assertEqual(service.resolve_fixture(fixture["id"])["name"], "fixture.stp")

            run_id = "stress-20261027-000000-abcdef"
            run_dir = service.runs_root / run_id
            run_dir.mkdir()
            (run_dir / "config.json").write_text(json.dumps({
                "engine": "legacy_smart",
                "fixture_id": fixture["id"],
                "step_path": str(models / "fixture.stp"),
                "settings_before": {
                    "analysis_max_concurrency": 1,
                    "annotation_max_concurrency": 2,
                },
            }), encoding="utf-8")
            (run_dir / "status.json").write_text(json.dumps({
                "run_id": run_id, "status": "completed", "phase": "complete",
            }), encoding="utf-8")
            (run_dir / "report.json").write_text(json.dumps({
                "status": "completed",
                "recommendation": {"recommended_active_heavy_users": 2},
            }), encoding="utf-8")

            self.assertEqual(service.recommendation(run_id)["recommended_active_heavy_users"], 2)
            self.assertEqual(service.settings_before(run_id)["annotation_max_concurrency"], 2)
            self.assertTrue(service.get(run_id)["report_json_url"].endswith("/report.json"))

    def test_runs_are_sorted_by_report_time_and_inactive_runs_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models = root / "models"
            output = root / "output"
            models.mkdir()
            output.mkdir()
            service = StressTestService(root, output, models)

            for run_id, generated_at in (
                ("stress-z-name", "2026-10-27T01:00:00+00:00"),
                ("stress-a-name", "2026-10-28T01:00:00+00:00"),
            ):
                run_dir = service.runs_root / run_id
                run_dir.mkdir()
                (run_dir / "status.json").write_text(json.dumps({
                    "run_id": run_id, "status": "completed", "phase": "complete",
                }), encoding="utf-8")
                (run_dir / "config.json").write_text(json.dumps({
                    "step_path": str(models / "fixture.stp"),
                }), encoding="utf-8")
                (run_dir / "report.json").write_text(json.dumps({
                    "status": "completed", "generated_at": generated_at,
                }), encoding="utf-8")

            self.assertEqual(service.list_runs()[0]["run_id"], "stress-a-name")
            self.assertTrue(service.delete("stress-z-name")["deleted"])
            self.assertFalse((service.runs_root / "stress-z-name").exists())

    def test_model_output_swap_worker_replaces_same_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "staging"
            destination = root / "current"
            source.mkdir()
            (source / "result.json").write_text("{}", encoding="utf-8")
            payload = replace_directory(source, destination)
            self.assertEqual(payload["status"], "ok")
            self.assertFalse(source.exists())
            self.assertTrue((destination / "result.json").is_file())

    def test_model_output_promotion_preserves_users_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            current = root / "current"
            backup = root / "backup"
            (staging / "_parts").mkdir(parents=True)
            (staging / "_parts" / "assembly_tree.json").write_text("{}", encoding="utf-8")
            (current / "_users" / "engineer").mkdir(parents=True)
            (current / "_users" / "engineer" / "note.json").write_text("{}", encoding="utf-8")

            payload = promote_reanalysis(staging, current, backup)

            self.assertEqual(payload["status"], "ok")
            self.assertTrue((current / "_parts" / "assembly_tree.json").is_file())
            self.assertTrue((current / "_users" / "engineer" / "note.json").is_file())
            self.assertTrue((backup / "_users" / "engineer" / "note.json").is_file())

    def test_reanalysis_promotion_runs_through_isolated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            current = root / "current"
            backup = root / "backup"
            staging.mkdir()
            current.mkdir()
            (staging / "new.txt").write_text("new", encoding="utf-8")
            (current / "old.txt").write_text("old", encoding="utf-8")

            server._promote_model_reanalysis_output(
                str(staging), str(current), str(backup), timeout_seconds=10,
            )

            self.assertEqual((current / "new.txt").read_text(encoding="utf-8"), "new")
            self.assertEqual((backup / "old.txt").read_text(encoding="utf-8"), "old")

    def test_reanalysis_generation_worker_writes_progress_in_isolation_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "assembly.stp"
            staging = root / "staging"
            progress = root / "progress.json"
            source.write_text("STEP", encoding="utf-8")

            def fake_batch(_source, output, _assembly, progress_cb):
                output_path = Path(output)
                (output_path / "_parts").mkdir(parents=True)
                (output_path / "_parts" / "assembly_tree.json").write_text("{}", encoding="utf-8")
                progress_cb(1, 1, "done")

            with patch.object(model_reanalysis_worker, "batch_generate", side_effect=fake_batch):
                result = model_reanalysis_worker.generate(source, staging, progress)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(json.loads(progress.read_text(encoding="utf-8"))["current"], 1)

    def test_forced_restart_interrupts_active_jobs_before_supervisor_request(self):
        with (
            patch.object(server, "_active_heavy_job_summary", return_value={"analysis": 2, "annotation": 0}),
            patch.object(server, "_interrupt_active_jobs_for_forced_restart") as interrupt,
            patch.object(StressTestService, "active_run_id", new_callable=PropertyMock, return_value=None),
            patch.object(server.runtime_control_service, "request_restart", return_value={"status": "accepted"}) as restart,
        ):
            result = server.admin_restart_runtime(
                server.AdminRestartRequest(force=True),
                SimpleNamespace(id="admin"),
            )

        self.assertEqual(result["status"], "accepted")
        interrupt.assert_called_once_with()
        restart.assert_called_once_with()

    def test_reanalysis_status_survives_backend_memory_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            job_id = "11111111-1111-1111-1111-111111111111"
            job = {
                "job_type": "model_reanalysis", "status": "completed",
                "message": "done", "requested_model_id": "model_batch",
                "owner_user_id": "engineer-1",
            }
            with patch.object(server, "OUTPUT_DIR", directory):
                server._persist_reanalysis_status(job_id, job)
                recovered = server._load_reanalysis_status(job_id)
            self.assertEqual(recovered["status"], "completed")
            self.assertEqual(recovered["requested_model_id"], "model_batch")

    def test_dual_recommendation_applies_both_work_pools(self):
        recommendation = {
            "recommended_analysis_concurrency": 3,
            "recommended_annotation_concurrency": 4,
        }
        applied = {"analysis_max_concurrency": 3, "annotation_max_concurrency": 4, "hard_limit": 8}
        with (
            patch.object(server.stress_test_service, "recommendation", return_value=recommendation),
            patch.object(server.identity_store, "cad_concurrency_limits", return_value={
                "analysis_max_concurrency": 4, "annotation_max_concurrency": 4, "hard_limit": 8,
            }),
            patch.object(server.identity_store, "set_cad_concurrency_limits", return_value=applied) as setter,
        ):
            response = server.admin_apply_stress_recommendation(
                "stress-20261027-000000-abcdef",
                admin=SimpleNamespace(id="admin-user"),
            )
        setter.assert_called_once_with(3, 4, "admin-user")
        self.assertEqual(response["scope"], "analysis_and_annotation")


class RuntimeControlServiceTests(unittest.TestCase):
    def test_maintenance_state_persists_and_runtime_snapshot_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            service = RuntimeControlService(directory)
            state = service.set_maintenance(True, "admin-user", "planned maintenance")
            self.assertTrue(state["maintenance_mode"])
            reloaded = RuntimeControlService(directory)
            snapshot = reloaded.snapshot()
            self.assertTrue(snapshot["maintenance_mode"])
            self.assertEqual(snapshot["reason"], "planned maintenance")
            self.assertGreater(snapshot["pid"], 0)
            self.assertIn("supervisor_available", snapshot["process_control"])
            self.assertGreater(snapshot["resources"]["disk_total_bytes"], 0)
            self.assertIn("cpu_percent", snapshot["resources"])
            self.assertIn("process_working_set_bytes", snapshot["resources"])

    def test_maintenance_mode_rejects_new_heavy_work(self):
        with patch.object(
            type(server.runtime_control_service),
            "maintenance_mode",
            new_callable=PropertyMock,
            return_value=True,
        ):
            with self.assertRaises(server.HTTPException) as raised:
                server._reject_heavy_work_during_exclusive_stress_test()
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "CAD_MAINTENANCE_MODE")


class PresenceServiceTests(unittest.TestCase):
    def test_presence_groups_sessions_and_tracks_activity_trend(self):
        service = PresenceService(online_seconds=90, recent_seconds=900, trend_minutes=10)
        user = SimpleNamespace(
            id="engineer-1", username="engineer", display_name="Engineer One",
            email="engineer@example.com", role="ENGINEER",
        )
        service.heartbeat(user, "token-one", page="/", idle=False)
        service.heartbeat(user, "token-two", page="/admin/system", idle=True)
        snapshot = service.snapshot()
        self.assertEqual(snapshot["summary"]["sessions"], 2)
        self.assertEqual(len(snapshot["users"]), 1)
        self.assertEqual(snapshot["users"][0]["session_count"], 2)
        self.assertEqual(snapshot["summary"]["online"], 1)
        self.assertGreaterEqual(snapshot["trend"][-1]["seen_users"], 1)
        service.record_activity(
            user, action="MODEL_UPLOAD", method="POST", path="/api/upload", status_code=200
        )
        self.assertEqual(service.activities()[0]["action"], "MODEL_UPLOAD")
        self.assertNotIn("request_body", service.activities()[0])

    def test_logout_removes_only_the_matching_session(self):
        service = PresenceService()
        user = SimpleNamespace(
            id="engineer-1", username="engineer", display_name="Engineer One",
            email="engineer@example.com", role="ENGINEER",
        )
        service.heartbeat(user, "token-one", page="/", idle=False)
        service.heartbeat(user, "token-two", page="/", idle=False)
        service.remove("token-one", user.id)
        self.assertEqual(service.snapshot()["summary"]["sessions"], 1)

    def test_admin_workforce_combines_presence_capacity_and_alerts(self):
        presence = {
            "sampled_at": "2026-10-28T12:00:00+00:00",
            "summary": {"online": 1, "idle": 0, "recently_offline": 0, "sessions": 1},
            "users": [{
                "user_id": "engineer-1", "display_name": "Engineer One",
                "email": "engineer@example.com", "status": "ONLINE",
                "last_seen_at": "2026-10-28T12:00:00+00:00", "page": "/",
            }],
            "trend": [],
        }
        capacity = {
            "recommended_simultaneous_heavy_users": 13,
            "pools": [{"workload": "annotation", "recommended_active_slots": 7}],
        }
        runtime = {
            "resources": {"cpu_percent": 90.0, "memory_percent": 50.0, "disk_percent": 60.0},
            "process_control": {"supervisor_available": True},
        }
        with (
            patch.object(server.presence_service, "snapshot", return_value=presence),
            patch.object(server.presence_service, "activities", return_value=[]),
            patch.object(server.stress_test_service, "list_runs", return_value=[{"report": {"capacity_planning": capacity}}]),
            patch.object(server.runtime_control_service, "snapshot", return_value=runtime),
        ):
            result = server.admin_workforce_dashboard(admin=SimpleNamespace(id="admin"))
        self.assertEqual(result["summary"]["online"], 1)
        self.assertEqual(result["capacity"]["recommended_simultaneous_heavy_users"], 13)
        self.assertIn("CPU_HIGH", [item["code"] for item in result["alerts"]])


if __name__ == "__main__":
    unittest.main()
