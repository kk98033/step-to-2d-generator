"""Direct entry URLs must serve the same admin application after refresh."""

import unittest
from pathlib import Path

from web_app.backend import server


class AdminCenterRouteTests(unittest.TestCase):
    def test_admin_entrypoints_serve_frontend_index(self):
        routes = {route.path: route for route in server.app.routes if hasattr(route, 'endpoint')}
        for path in ('/admin', '/admin/', '/admin/accounts', '/admin/accounts/import',
                     '/admin/accounts/passwords', '/admin/presence', '/admin/system',
                     '/admin/service-control', '/admin/stress-test', '/admin/tags',
                     '/admin/trash', '/admin/logs', '/admin/settings', '/admin/api-graph'):
            with self.subTest(path=path):
                self.assertIn(path, routes)
                response = routes[path].endpoint()
                self.assertEqual(Path(response.path), Path(server.FRONTEND_DIR) / 'index.html')
                self.assertEqual(response.media_type, 'text/html')


if __name__ == '__main__':
    unittest.main()
