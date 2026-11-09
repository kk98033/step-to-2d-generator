import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from web_app.backend.drawing_history import list_drawing_history
from web_app.backend import server


class DrawingHistoryTests(unittest.TestCase):
    def test_saved_results_survive_missing_runtime_jobs_and_remain_scoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / '_users/user-a/_annotation_audit/2026-11-09'
            root.mkdir(parents=True)
            def write(name, **changes):
                payload = {'status': 'completed', 'engineer': {'id': 'user-a'},
                           'request': {'part_id': 'shaft', 'candidate_engine': 'legacy_smart'},
                           'operation_id': name, 'completed_at': '2026-11-09T08:00:00Z',
                           'result': {'png_url': f'/api/files/{name}.png'}}
                payload.update(changes)
                (root / (name + '.json')).write_text(json.dumps(payload), encoding='utf-8')
            write('01')
            write('02')
            write('03', status='error')
            write('04', request={'part_id': 'other', 'candidate_engine': 'legacy_smart'})
            write('05', engineer={'id': 'user-b'})
            (root / '06.json').write_text('{broken', encoding='utf-8')
            records = list_drawing_history(temporary, 'user-a', 'shaft', 'legacy_smart')
            self.assertEqual([item['operation_id'] for item in records], ['02', '01'])
            self.assertEqual(records[0]['result']['png_url'], '/api/files/02.png')
            self.assertEqual(len(list_drawing_history(temporary, 'user-a', 'shaft', 'legacy_smart', limit=1)), 1)
            self.assertEqual(list_drawing_history(temporary, 'user-b', 'shaft', 'legacy_smart'), [])
            self.assertEqual(list_drawing_history(temporary, 'user-a', 'shaft', 'experimental_v026'), [])

    def test_endpoint_checks_model_access_before_reading_history(self):
        user = type('User', (), {'id': 'user-a', 'is_admin': False})()
        with patch.object(server, 'assert_model_access', side_effect=HTTPException(403)), patch.object(server, 'list_drawing_history') as read:
            with self.assertRaises(HTTPException):
                server.get_drawing_history('other-model', 'shaft', current_user=user)
            read.assert_not_called()
