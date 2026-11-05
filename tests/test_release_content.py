import unittest

from tools.check_release_content import private_path


class ReleaseContentTests(unittest.TestCase):
    def test_private_data_and_notes_are_blocked(self):
        for path in ('models/company.STEP', 'output/company.dxf', 'nested/output/tree.json',
                     'db/customer.sqlite3-wal', 'company_data/cases.json', '.env.production',
                     'auto_2d_drawing/tolerance/data/feature_case_base.json',
                     'project_knowledge/weekly_progress_2026_w44.md', 'docs/agent/CURRENT_HANDOFF.md',
                     'experimental_lab/tolerance_extraction_audit/experiments/sample/report.json',
                     'experimental_lab/tolerance_extraction_audit/CURRENT_FINAL/database_pilot_samples.json'):
            with self.subTest(path=path):
                self.assertTrue(private_path(path))

    def test_runtime_source_and_public_configuration_are_allowed(self):
        for path in ('web_app/backend/server.py', 'web_app/backend/identity_store.py', '.env.example',
                     'docs/deployment-docker.md', 'docs/generated/api_graph.json',
                     'auto_2d_drawing/templates/shaft_standard_preset.json',
                     'experimental_lab/tolerance_extraction_audit/common/scripts/minimal_multiview_drawing.py',
                     'experimental_lab/tolerance_extraction_audit/DRAWING_CURRENT_FINAL/ENGINE_MANIFEST.json'):
            with self.subTest(path=path):
                self.assertFalse(private_path(path))


if __name__ == '__main__':
    unittest.main()
