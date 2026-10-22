import unittest

from web_app.backend import server


class JobConcurrencyControlTests(unittest.TestCase):
    def setUp(self):
        with server.jobs_lock:
            self.original_jobs = dict(server.jobs)
            server.jobs.clear()

    def tearDown(self):
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs.update(self.original_jobs)

    def test_queue_position_and_user_active_job(self):
        with server.jobs_lock:
            server.jobs.update({
                "job-a": {
                    "status": "processing", "owner_user_id": "user-a",
                    "created_at": "2026-01-01T00:00:00+00:00", "message": "processing",
                },
                "job-b": {
                    "status": "queued", "owner_user_id": "user-b",
                    "created_at": "2026-01-01T00:00:01+00:00", "message": "queued",
                },
                "job-c": {
                    "status": "queued", "owner_user_id": "user-c",
                    "created_at": "2026-01-01T00:00:02+00:00", "message": "queued",
                },
            })
        self.assertEqual(server._active_job_for_user("user-a")[0], "job-a")
        self.assertEqual(server._active_job_for_user("user-b")[0], "job-b")
        self.assertEqual(server._queue_position("job-b"), 1)
        self.assertEqual(server._queue_position("job-c"), 2)

    def test_finalizing_job_still_blocks_duplicate_submission(self):
        with server.jobs_lock:
            server.jobs["job-final"] = {
                "status": "finalizing", "owner_user_id": "user-a",
                "created_at": "2026-01-01T00:00:00+00:00", "message": "finalizing",
            }
        self.assertEqual(server._active_job_for_user("user-a")[0], "job-final")
        server._set_job_state("job-final", status="completed")
        self.assertIsNone(server._active_job_for_user("user-a"))

    def test_default_worker_limits_are_bounded(self):
        self.assertGreaterEqual(server.ANALYSIS_MAX_WORKERS, 1)
        self.assertLessEqual(server.ANALYSIS_MAX_WORKERS, 8)
        self.assertGreaterEqual(server.ANNOTATION_MAX_CONCURRENCY, 1)
        self.assertLessEqual(server.ANNOTATION_MAX_CONCURRENCY, 8)
        self.assertGreaterEqual(server.EXPERIMENTAL_SHADOW_MAX_WORKERS, 1)
        self.assertLessEqual(server.EXPERIMENTAL_SHADOW_MAX_WORKERS, 2)

    def test_dynamic_capacity_limiter_applies_new_admissions_immediately(self):
        limiter = server.DynamicCapacityLimiter()
        capacity = 2
        self.assertTrue(limiter.acquire(lambda: capacity, blocking=False))
        self.assertTrue(limiter.acquire(lambda: capacity, blocking=False))
        self.assertFalse(limiter.acquire(lambda: capacity, blocking=False))
        self.assertEqual(limiter.active, 2)
        capacity = 1
        limiter.release()
        self.assertFalse(limiter.acquire(lambda: capacity, blocking=False))
        limiter.release()
        self.assertTrue(limiter.acquire(lambda: capacity, blocking=False))
        limiter.release()

    def test_job_status_exposes_shadow_state_without_changing_primary_status(self):
        with server.jobs_lock:
            server.jobs["job-shadow"] = {
                "status": "completed",
                "owner_user_id": "user-a",
                "created_at": "2026-01-01T00:00:00+00:00",
                "message": "done",
                "experimental_shadow_analysis": {
                    "status": "error",
                    "message": "shadow failed",
                },
            }
        payload = server._job_status_payload("job-shadow", server.jobs["job-shadow"])
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["experimental_shadow_analysis"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
