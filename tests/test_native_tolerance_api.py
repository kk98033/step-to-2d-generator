import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, mock_open

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
            with patch('builtins.open', mock_open(read_data='{"status":"PASS_FOR_ADMIN_TEST_ONLY","engine_sha256":"validated"}')), patch.object(server, '_file_sha256', return_value='validated'), patch.object(server, 'assert_model_access') as authorize, patch.object(server, '_safe_output_dir', return_value=temporary), patch.object(
                server.identity_store, 'validate_recommendation_tag_ids', return_value=['tag-a']
            ), patch.object(server.identity_store, 'company_case_ids_for_tags', return_value={'case-a'}), patch.object(
                server, '_run_cad_annotation_worker', return_value={'status': 'ok', 'recommendations': {}}
            ) as worker:
                with patch.object(server, '_attach_tolerance_source_drawings', wraps=server._attach_tolerance_source_drawings) as attach:
                    server.recommend_experimental_tolerances({'model_id': 'model', 'part_id': 'shaft',
                        'candidate_rules': [{'rule_id': 'native-one'}], 'recommendation_tag_ids': ['tag-a']}, admin)
                    attach.assert_called_once()
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
        with patch('builtins.open', mock_open(read_data='{"status":"PASS_FOR_ADMIN_TEST_ONLY","engine_sha256":"validated"}')), patch.object(server, 'assert_model_access'), patch.object(server, '_file_sha256', return_value='changed-engine'), patch.object(server, '_run_cad_annotation_worker') as worker:
            with self.assertRaises(HTTPException) as raised:
                server.recommend_experimental_tolerances({'model_id': 'model', 'part_id': 'shaft'}, admin)
            self.assertEqual(raised.exception.status_code, 503)
            worker.assert_not_called()

    def test_existing_source_drawing_is_clickable_and_evidence_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            drawing = Path(temporary) / 'source.dxf'
            drawing.write_text('fixture')
            evidence = {'source_model': 'source.dxf', 'case_id': 'case-1', 'entity_handle': 'A1', 'used_for_decision': False}
            result = {'recommendations': {'rule-1': {'evidence_cases': [evidence]}}}
            with patch.object(server, 'lookup_drawing_paths', return_value={'dxf': str(drawing)}):
                server._attach_tolerance_source_drawings(result)
            self.assertTrue(evidence['has_source_drawing'])
            self.assertEqual(evidence['drawing_urls']['svg'], '/api/tolerance/drawing-svg/source')
            self.assertEqual(evidence['entity_handle'], 'A1')
            self.assertFalse(evidence['used_for_decision'])

    def test_missing_source_has_no_invented_viewer_link(self):
        evidence = {'drawing': 'missing.dxf'}
        with patch.object(server, 'lookup_drawing_paths', return_value={'dxf': 'does-not-exist.dxf'}):
            server._attach_tolerance_source_drawings({'recommendations': {'rule': {'evidence_cases': [evidence]}}})
        self.assertFalse(evidence['has_source_drawing'])
        self.assertIsNone(evidence['drawing_urls'])

    def test_abstained_recommendations_do_not_acquire_unrelated_sources(self):
        result = {'recommendations': {'blocked': {'recommended_mode': 'NONE', 'evidence_cases': []}}}
        with patch.object(server, 'lookup_drawing_paths') as lookup:
            self.assertIs(server._attach_tolerance_source_drawings(result), result)
            lookup.assert_not_called()
