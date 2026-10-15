import json
import unittest
from pathlib import Path


AUDIT_ROOT = Path(__file__).resolve().parents[1]
FINAL_ROOT = AUDIT_ROOT / "DRAWING_CURRENT_FINAL"


class DrawingCurrentFinalTests(unittest.TestCase):
    def test_manifest_references_existing_modules(self):
        manifest = json.loads((FINAL_ROOT / "ENGINE_MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["deployment_status"], "EXPERIMENT_ONLY")
        self.assertFalse(manifest["production_integrated"])
        self.assertTrue((AUDIT_ROOT / manifest["entrypoint"]).is_file())
        for capability in manifest["capabilities"]:
            self.assertTrue(
                (AUDIT_ROOT / capability["module"]).is_file(),
                capability["id"],
            )

    def test_manifest_forbids_filename_trigger_for_half_section(self):
        manifest = json.loads((FINAL_ROOT / "ENGINE_MANIFEST.json").read_text(encoding="utf-8"))
        section = next(item for item in manifest["capabilities"] if item["id"] == "geometry_triggered_half_section")
        self.assertEqual(section["trigger_policy"], "geometry_evidence_only_not_filename")


if __name__ == "__main__":
    unittest.main()
