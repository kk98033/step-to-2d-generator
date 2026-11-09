import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from web_app.backend import server


class CandidateCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / '_parts').mkdir()
        self.step = self.root / '_parts/part.step'
        self.step.write_text('first model', encoding='utf-8')
        self.output_patch = patch.object(server, '_safe_output_dir', return_value=str(self.root))
        self.output_patch.start()
        self.addCleanup(self.output_patch.stop)

    def result(self, payload, *_args):
        return {'status': 'ok', 'rules': [{'rule_id': payload['action']}]}

    def test_both_engines_cache_separately_and_refresh(self):
        with patch.object(server, '_run_cad_annotation_worker', side_effect=self.result) as worker:
            for engine in ('legacy_smart', 'experimental_v026'):
                first = server._candidate_annotation_rules_impl('model', 'part', engine)
                cached = server._candidate_annotation_rules_impl('model', 'part', engine)
                self.assertFalse(first['cached'])
                self.assertTrue(cached['cached'])
                self.assertEqual(first['rules'], cached['rules'])
            self.assertEqual(worker.call_count, 2)
            server._candidate_annotation_rules_impl('model', 'part', force_refresh=True)
            self.assertEqual(worker.call_count, 3)

    def test_changed_bytes_invalidate_even_with_equal_size_and_timestamp(self):
        with patch.object(server, '_run_cad_annotation_worker', side_effect=self.result) as worker:
            server._candidate_annotation_rules_impl('model', 'part')
            stat = self.step.stat()
            self.step.write_text('other model', encoding='utf-8')
            os.utime(self.step, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            updated = server._candidate_annotation_rules_impl('model', 'part')
            self.assertFalse(updated['cached'])
            self.assertEqual(worker.call_count, 2)

    def test_simultaneous_requests_start_only_one_worker(self):
        started, release, second_entered = threading.Event(), threading.Event(), threading.Event()
        def render(payload, *args):
            started.set()
            self.assertTrue(release.wait(5))
            return self.result(payload, *args)
        def second_request():
            second_entered.set()
            return server._candidate_annotation_rules_impl('model', 'part')
        with patch.object(server, '_run_cad_annotation_worker', side_effect=render) as worker:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(server._candidate_annotation_rules_impl, 'model', 'part')
                self.assertTrue(started.wait(5))
                second = executor.submit(second_request)
                self.assertTrue(second_entered.wait(5))
                release.set()
                self.assertFalse(first.result(timeout=5)['cached'])
                self.assertTrue(second.result(timeout=5)['cached'])
            self.assertEqual(worker.call_count, 1)
            self.assertFalse(server.candidate_cache._locks)

    def test_failed_analysis_is_retried(self):
        with patch.object(server, '_run_cad_annotation_worker', side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(HTTPException):
                server._candidate_annotation_rules_impl('model', 'part')
        with patch.object(server, '_run_cad_annotation_worker', side_effect=self.result) as worker:
            self.assertFalse(server._candidate_annotation_rules_impl('model', 'part')['cached'])
            worker.assert_called_once()

    def test_rule_revision_invalidates_cache(self):
        with patch.object(server, '_run_cad_annotation_worker', side_effect=self.result) as worker:
            server._candidate_annotation_rules_impl('model', 'part')
            original = server.candidate_cache.signature
            def revised(*args):
                return {**original(*args), 'analysis_revision': 'new-algorithm'}
            with patch.object(server.candidate_cache, 'signature', side_effect=revised):
                self.assertFalse(server._candidate_annotation_rules_impl('model', 'part')['cached'])
            self.assertEqual(worker.call_count, 2)

    def test_same_part_name_in_different_models_keeps_results_isolated(self):
        other = self.root / 'other-model'
        (other / '_parts').mkdir(parents=True)
        (other / '_parts/part.step').write_text('second model', encoding='utf-8')
        def model_output(model_id):
            return str(other if model_id == 'other' else self.root)
        with patch.object(server, '_safe_output_dir', side_effect=model_output), patch.object(
            server, '_run_cad_annotation_worker', side_effect=self.result
        ) as worker:
            server._candidate_annotation_rules_impl('first', 'part')
            second = server._candidate_annotation_rules_impl('other', 'part')
            self.assertEqual(second['model_id'], 'other')
            self.assertFalse(second['cached'])
            self.assertTrue(server._candidate_annotation_rules_impl('first', 'part')['cached'])
            self.assertEqual(worker.call_count, 2)
