import unittest
from pathlib import Path

import ezdxf

from auto_2d_drawing.tolerance.audit_visual_assets import _feature_box
from web_app.backend.server import (
    _modelspace_render_box,
    get_drawing_details,
    list_tolerance_cases,
    list_tolerance_debug_cases,
)


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
        self.assertIn('id="drawingCanvas"', page)
        self.assertIn("zoomDrawingAt", page)
        self.assertIn("addEventListener('wheel'", page)
        self.assertIn("setPointerCapture", page)
        self.assertIn('data-browse="folders"', page)
        self.assertIn("group_by_family", page)
        self.assertIn("product_family", page)
        self.assertIn('data-quality="DEBUG"', page)
        self.assertIn("/api/tolerance/debug/cases", page)

    def test_modelspace_render_box_uses_real_dxf_geometry(self):
        document = ezdxf.new()
        modelspace = document.modelspace()
        modelspace.add_line((10.0, 20.0), (110.0, 70.0))
        render_box = _modelspace_render_box(modelspace, padding_ratio=0.0)
        self.assertIsNotNone(render_box)
        self.assertAlmostEqual(render_box.extmin.x, 10.0)
        self.assertAlmostEqual(render_box.extmax.y, 70.0)

    def test_case_api_can_group_and_filter_model_folders(self):
        folders = list_tolerance_cases(
            quality="VERIFIED_EXTRACTION",
            group_by_family=True,
            page_size=0,
        )
        self.assertTrue(folders["group_by_family"])
        self.assertGreater(len(folders["cases"]), 0)
        names = [item["folder_name"] for item in folders["cases"]]
        self.assertEqual(len(names), len(set(names)))
        selected = names[0]
        drawings = list_tolerance_cases(
            quality="VERIFIED_EXTRACTION",
            product_family=selected,
            group_by_drawing=True,
            page_size=0,
        )
        self.assertTrue(drawings["cases"])
        self.assertTrue(all(item["product_family"] == selected for item in drawings["cases"]))

    def test_debug_category_reads_isolated_latest_canonical_snapshot(self):
        payload = list_tolerance_debug_cases()
        self.assertTrue(payload["debug_snapshot"])
        self.assertEqual(payload["algorithm"], "CANONICAL_FEATURE_REPLAY")
        self.assertEqual(payload["evidence_count"], 5)
        self.assertEqual(len(payload["cases"]), 1)
        drawing = payload["cases"][0]
        self.assertEqual(drawing["model_name"], "1AL0W5000H-R03")
        self.assertEqual(drawing["drawing_verified_extraction_count"], 5)
        details = get_drawing_details(drawing["model_name"], debug=True)
        linked = [
            item for item in details["tolerances"]
            if item.get("case_id") and (item.get("geometry_verification") or {}).get("passed")
        ]
        self.assertEqual(len(linked), 5)
        self.assertEqual(
            {item["matched_feature_id"] for item in linked},
            {"hole_04", "shaft_04", "shaft_02", "hole_01", "shaft_01"},
        )


if __name__ == "__main__":
    unittest.main()
