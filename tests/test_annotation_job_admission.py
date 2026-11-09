from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

from fastapi import HTTPException
from web_app.backend import server


class AnnotationJobAdmissionTests(unittest.TestCase):
    def setUp(self):
        with server.annotation_state_lock:
            self.original = dict(server.annotation_jobs)
            server.annotation_jobs.clear()

    def tearDown(self):
        with server.annotation_state_lock:
            server.annotation_jobs.clear()
            server.annotation_jobs.update(self.original)

    def test_concurrent_submissions_for_same_account_admit_one(self):
        barrier = threading.Barrier(2)
        def submit(index):
            barrier.wait(timeout=5)
            try:
                server._register_annotation_job(str(index), {
                    'status': 'queued', 'owner_user_id': 'same-user',
                    'model_id': f'model-{index}', 'candidate_engine': 'legacy_smart',
                })
                return 'accepted'
            except HTTPException as error:
                self.assertEqual(error.status_code, 409)
                self.assertEqual(error.detail['code'], 'ANNOTATION_ALREADY_RUNNING')
                self.assertIn('job_id', error.detail['job'])
                return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(submit, range(2)))
        self.assertCountEqual(outcomes, ['accepted', 'rejected'])
        self.assertEqual(len(server.annotation_jobs), 1)

    def test_different_accounts_and_completed_job_do_not_block(self):
        server._register_annotation_job('a', {'status': 'queued', 'owner_user_id': 'a'})
        server._register_annotation_job('b', {'status': 'queued', 'owner_user_id': 'b'})
        server.annotation_jobs['a']['status'] = 'completed'
        server._register_annotation_job('a-next', {'status': 'queued', 'owner_user_id': 'a'})
        self.assertEqual(server._active_annotation_job_for_user('a')[0], 'a-next')

    def test_status_restores_model_and_completed_result(self):
        job = {'status': 'completed', 'owner_user_id': 'a', 'model_id': 'model-a',
               'part_id': 'shaft', 'result': {'png_url': '/api/files/a/result.png'}}
        server.annotation_jobs['done'] = job
        user = type('User', (), {'id': 'a', 'is_admin': False})()
        restored = server.get_annotation_job('done', current_user=user)
        self.assertEqual(restored['model_id'], 'model-a')
        self.assertEqual(restored['part_id'], 'shaft')
        self.assertEqual(restored['result'], job['result'])
        other = type('User', (), {'id': 'b', 'is_admin': False})()
        with self.assertRaises(HTTPException):
            server.get_annotation_job('done', current_user=other)
