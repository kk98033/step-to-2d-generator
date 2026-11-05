import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from web_app.backend import server


class SystemStatusTests(unittest.TestCase):
    def test_authenticated_aggregate_deduplicates_users_without_identity_data(self):
        with patch.dict(server.jobs, {
            'private-job-a': {'owner_user_id': 'private-user-a', 'status': 'processing'},
            'private-job-b': {'owner_user_id': 'private-user-b', 'status': 'queued'},
        }, clear=True), patch.dict(server.annotation_jobs, {
            'private-job-c': {'owner_user_id': 'private-user-a', 'status': 'processing'},
            'private-job-d': {'owner_user_id': 'private-user-c', 'status': 'queued'},
        }, clear=True), patch.object(server.presence_service, 'snapshot', return_value={'summary': {'online': 3, 'idle': 1}}), \
                patch.object(server, 'runtime_control_service', SimpleNamespace(maintenance_mode=False)):
            result = server.system_status(SimpleNamespace(id='viewer', role='ENGINEER'))
        self.assertEqual(result, {'status': 'ok', 'maintenance_mode': False, 'online': 3, 'idle': 1,
                                  'executing_users': 1, 'queued_users': 2})
        self.assertNotIn('private-', json.dumps(result))
        route = next(route for route in server.app.routes if getattr(route, 'path', '') == '/api/system/status')
        self.assertTrue(any(dependency.call is server.require_user for dependency in route.dependant.dependencies))


if __name__ == '__main__':
    unittest.main()
