import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from web_app.backend import server
from web_app.backend.identity_store import CurrentUser


class ModelReanalysisQueueTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.output_root = self.root / "output"
        self.models_root = self.root / "models"
        self.output_root.mkdir()
        self.models_root.mkdir()
        self.model_id = "fan_ab12cd34_batch"
        self.output_dir = self.output_root / self.model_id
        self.output_dir.mkdir()
        self.user = CurrentUser(
            id="user-a",
            username="engineer-a",
            display_name="Engineer A",
            email=None,
            role="ENGINEER",
            is_active=True,
            must_change_password=False,
        )
        with server.jobs_lock:
            self.original_jobs = dict(server.jobs)
            server.jobs.clear()

    def tearDown(self):
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs.update(self.original_jobs)

    def _source_step(self) -> Path:
        source = self.models_root / "_users" / self.user.id / "ab12cd34-full-job" / "fan.step"
        source.parent.mkdir(parents=True)
        source.write_text("STEP", encoding="utf-8")
        return source

    def test_authorized_user_reanalysis_is_submitted_to_primary_queue(self):
        source = self._source_step()
        registration = {
            "model_id": self.model_id,
            "owner_user_id": self.user.id,
            "source_filename": source.name,
        }
        with (
            patch.object(server, "OUTPUT_DIR", str(self.output_root)),
            patch.object(server, "MODELS_DIR", str(self.models_root)),
            patch.object(server, "assert_model_access"),
            patch.object(server.identity_store, "model_registration", return_value=registration),
            patch.object(server.executor, "submit") as submit,
        ):
            payload = server.reanalyze_model(self.model_id, self.user)

        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["queue_position"], 1)
        queued = server.jobs[payload["job_id"]]
        self.assertEqual(queued["job_type"], "model_reanalysis")
        self.assertEqual(queued["requested_model_id"], self.model_id)
        submit.assert_called_once_with(
            server.run_model_reanalysis_job,
            payload["job_id"],
            str(source),
        )

    def test_full_reanalysis_preserves_user_artifacts_and_switches_generated_data(self):
        source = self._source_step()
        audit = self.output_dir / "_users" / self.user.id / "_annotation_audit" / "audit.json"
        audit.parent.mkdir(parents=True)
        audit.write_text('{"kept": true}', encoding="utf-8")
        (self.output_dir / "old-generated.txt").write_text("old", encoding="utf-8")
        job_id = "reanalysis-job"
        with server.jobs_lock:
            server.jobs[job_id] = {
                "job_type": "model_reanalysis",
                "status": "queued",
                "requested_model_id": self.model_id,
                "owner_user_id": self.user.id,
                "created_at": "2026-01-01T00:00:00+00:00",
                "message": "queued",
            }

        def fake_batch_generate(_source, destination, _assembly, progress_cb):
            destination = Path(destination)
            parts = destination / "_parts"
            parts.mkdir(parents=True)
            (parts / "assembly_tree.json").write_text(
                json.dumps({"name": "assembly", "children": []}), encoding="utf-8"
            )
            (parts / "part-01.stp").write_text("STEP", encoding="utf-8")
            (destination / "new-generated.txt").write_text("new", encoding="utf-8")
            progress_cb(2, 2, "done")

        with (
            patch.object(server, "OUTPUT_DIR", str(self.output_root)),
            patch.object(server, "MODELS_DIR", str(self.models_root)),
            patch.object(server, "batch_generate", side_effect=fake_batch_generate),
            patch.object(server, "_schedule_experimental_shadow_analysis"),
        ):
            server.run_model_reanalysis_job(job_id, str(source))

        self.assertEqual(server.jobs[job_id]["status"], "completed")
        self.assertTrue(audit.exists())
        self.assertTrue((self.output_dir / "new-generated.txt").exists())
        self.assertFalse((self.output_dir / "old-generated.txt").exists())
        self.assertIn("part-01", server.jobs[job_id]["parts_map"])


if __name__ == "__main__":
    unittest.main()
