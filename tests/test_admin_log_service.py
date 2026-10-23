import json
import tempfile
import unittest
from pathlib import Path

from web_app.backend.admin_log_service import (
    get_operation_log,
    get_operation_reproduction,
    list_operation_logs,
)


class AdminLogServiceTests(unittest.TestCase):
    def test_lists_metadata_and_omits_model_binary(self):
        operation_id = "12345678-abcd-4321-abcd-123456789abc"
        with tempfile.TemporaryDirectory() as directory:
            audit_dir = Path(directory) / "model-a" / "_users" / "user-a" / "_annotation_audit" / "2026-10-21"
            audit_dir.mkdir(parents=True)
            path = audit_dir / f"20261021T010203Z_{operation_id}.json"
            path.write_text(json.dumps({
                "operation_id": operation_id,
                "status": "error",
                "message": "render failed",
                "created_at": "2026-10-21T01:02:03+00:00",
                "engineer": {"id": "user-a", "display_name": "Engineer A", "email": "a@example.com"},
                "request": {
                    "model_id": "model-a",
                    "part_id": "shaft-a",
                    "candidate_engine": "legacy_smart",
                    "feature_records": [{"rule_id": "r1", "geometry_payload": {"large": "value"}}],
                },
                "execution": {"worker_pid": 42, "output_files": {"dxf_url": "/a.dxf"}},
                "failure": {"error_type": "RuntimeError", "full_error": "native failure", "traceback": "trace"},
            }), encoding="utf-8")

            summaries = list_operation_logs(output_root=directory)
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["model_id"], "model-a")
            self.assertEqual(summaries[0]["feature_record_count"], 1)

            detail = get_operation_log(operation_id, output_root=directory)
            self.assertIsNotNone(detail)
            self.assertFalse(detail["model_binary_included"])
            self.assertNotIn("geometry_payload", detail["request"]["feature_records"][0])

    def test_rejects_invalid_operation_id(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(get_operation_log("../outside", output_root=directory))

    def test_reproduction_loads_full_worker_input_and_model_preview(self):
        operation_id = "87654321-abcd-4321-abcd-123456789abc"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_root = root / "model-a"
            audit_dir = model_root / "_users" / "user-a" / "_annotation_audit" / "2026-10-23"
            job_dir = model_root / "_users" / "user-a" / "_annotation_jobs" / operation_id
            parts_dir = model_root / "_parts"
            audit_dir.mkdir(parents=True)
            job_dir.mkdir(parents=True)
            parts_dir.mkdir(parents=True)
            step_path = parts_dir / "shaft-a.stp"
            step_path.write_text("STEP fixture", encoding="utf-8")
            (parts_dir / "shaft-a.stl").write_text("solid fixture\nendsolid fixture", encoding="utf-8")
            worker_path = job_dir / "worker_request.json"
            worker_path.write_text(json.dumps({
                "step_path": str(step_path),
                "part_id": "shaft-a",
                "feature_records": [{
                    "rule_id": "r1",
                    "enabled": True,
                    "geometry_payload": {"center_3d": [1, 2, 3]},
                }],
            }), encoding="utf-8")
            audit_path = audit_dir / f"20261023T010203Z_{operation_id}.json"
            audit_path.write_text(json.dumps({
                "operation_id": operation_id,
                "status": "error",
                "request": {
                    "model_id": "model-a",
                    "part_id": "shaft-a",
                    "candidate_engine": "experimental_v026",
                },
                "execution": {"diagnostic_files": {"worker_request": str(worker_path)}},
            }), encoding="utf-8")

            reproduction = get_operation_reproduction(operation_id, output_root=directory)

            self.assertTrue(reproduction["replay_available"])
            self.assertEqual(reproduction["request"]["enabled_feature_record_count"], 1)
            self.assertEqual(
                reproduction["request"]["feature_records"][0]["geometry_payload"]["center_3d"],
                [1, 2, 3],
            )
            self.assertEqual(
                reproduction["model"]["preview_url"],
                "/api/files/model-a/_parts/shaft-a.stl",
            )
            self.assertTrue(reproduction["model"]["sha256"])
            self.assertFalse(reproduction["model_binary_included"])


if __name__ == "__main__":
    unittest.main()
