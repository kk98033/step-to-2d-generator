import unittest
from pathlib import Path

from auto_2d_drawing.tolerance.audit_visual_assets import _feature_box


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ToleranceAuditVisualTests(unittest.TestCase):
    def test_cylindrical_feature_box_follows_axis(self):
        result = _feature_box({
            "matched_feature_source_info": {
                "center": [2.0, 3.0, 4.0],
                "axis_dir": [0.0, 1.0, 0.0],
                "radius": 5.0,
                "length": 12.0,
            }
        })
        self.assertEqual(result, ([2.0, 3.0, 4.0], [10.0, 12.0, 10.0]))

    def test_audit_page_uses_lazy_images_instead_of_pdf_iframe(self):
        page = (
            PROJECT_ROOT / "web_app" / "frontend" / "public" / "tolerance-audit-v2.html"
        ).read_text(encoding="utf-8")
        self.assertIn("model_preview_url", page)
        self.assertIn("loading=\"lazy\"", page)
        self.assertNotIn("<iframe", page.lower())
        self.assertIn("另開 PDF", page)
        self.assertIn('id="leftSplitter"', page)
        self.assertIn('id="rightSplitter"', page)
        self.assertIn('id="viewerSplitter"', page)
        self.assertIn("toleranceAuditLayout", page)


if __name__ == "__main__":
    unittest.main()
