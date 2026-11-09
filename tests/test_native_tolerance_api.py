import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from web_app.backend import server
from tools import cad_annotation_worker


class NativeToleranceApiTests(unittest.TestCase):
    def test_non_admin_is_rejected_before_work(self):
        with patch.object(server, '_run_cad_annotation_worker') as worker:
            with self.assertRaises(HTTPException) as raised:
                server.recommend_experimental_tolerances({}, SimpleNamespace(is_admin=False))
            self.assertEqual(raised.exception.status_code, 403)
            worker.assert_not_called()

    def test_scoped_company_evidence_is_forwarded_to_isolated_worker(self):
        admin = SimpleNamespace(is_admin=True, id='admin')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '_parts').mkdir()
            (root / '_parts/shaft.stp').write_text('fixture')
            with patch.object(server, 'assert_model_access') as authorize, patch.object(server, '_safe_output_dir', return_value=temporary), patch.object(
                server.identity_store, 'validate_recommendation_tag_ids', return_value=['tag-a']
            ), patch.object(server.identity_store, 'company_case_ids_for_tags', return_value={'case-a'}), patch.object(
                server, '_run_cad_annotation_worker', return_value={'status': 'ok', 'recommendations': {}}
            ) as worker:
                server.recommend_experimental_tolerances({'model_id': 'model', 'part_id': 'shaft',
                    'candidate_rules': [{'rule_id': 'native-one'}], 'recommendation_tag_ids': ['tag-a']}, admin)
                authorize.assert_called_once_with(admin, 'model')
                payload = worker.call_args.args[0]
                self.assertEqual(payload['action'], 'experimental_tolerance')
                self.assertEqual(payload['allowed_case_ids'], ['case-a'])
                self.assertEqual(payload['requested_ids'], ['native-one'])

    def test_worker_does_not_use_legacy_candidates_or_personalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            step = Path(temporary) / 'part.step'
            step.write_text('fixture')
            with patch('experimental_lab.tolerance_extraction_audit.TOLERANCE_V1_EXPERIMENT.engine.recommend_step', return_value={'status': 'ok'}) as recommend:
                result = cad_annotation_worker.run_job({'action': 'experimental_tolerance', 'step_path': str(step), 'allowed_case_ids': []})
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(recommend.call_args.kwargs['allowed_case_ids'], set())

    def test_stale_validation_blocks_admin_test(self):
        admin = SimpleNamespace(is_admin=True, id='admin')
        with patch.object(server, 'assert_model_access'), patch.object(server, '_file_sha256', return_value='changed-engine'), patch.object(server, '_run_cad_annotation_worker') as worker:
            with self.assertRaises(HTTPException) as raised:
                server.recommend_experimental_tolerances({'model_id': 'model', 'part_id': 'shaft'}, admin)
            self.assertEqual(raised.exception.status_code, 503)
            worker.assert_not_called()
