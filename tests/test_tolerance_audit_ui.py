import unittest
from pathlib import Path

import ezdxf

from auto_2d_drawing.tolerance.audit_visual_assets import _feature_box
from web_app.backend.server import (
    _add_uncertainty_overlay,
    _modelspace_render_box,
    get_tolerance_debug_component_details,
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
        self.assertIn('id="drawingVector"', page)
        self.assertNotIn('id="drawingImage"', page)
        self.assertIn("innerHTML=svg", page)
        self.assertIn("shape-rendering:geometricPrecision", page)
        self.assertIn("zoomDrawingAt", page)
        self.assertIn("addEventListener('wheel'", page)
        self.assertIn("setPointerCapture", page)
        self.assertIn('data-browse="folders"', page)
        self.assertIn("group_by_family", page)
        self.assertIn("product_family", page)
        self.assertIn('data-quality="DEBUG"', page)
        self.assertIn("/api/tolerance/debug/cases", page)
        self.assertIn('id="diagnosticLegend"', page)
        self.assertIn('id="diagnosticLegendStatus"', page)
        self.assertIn('id="debugLayerControls"', page)
        self.assertIn('data-debug-layer="step"', page)
        self.assertIn("show_feature_footprints", page)
        self.assertIn("整體外框一致度", page)
        self.assertIn("雙向覆蓋率", page)
        self.assertIn("完整 STEP HLR 投影輪廓", page)
        self.assertIn("公差實際指向的 DXF 邊", page)
        self.assertIn("邊線決勝", page)
        self.assertIn("debug_component", page)
        self.assertIn("正規化位置距離", page)

    def test_uncertainty_overlay_draws_observed_and_projected_markers(self):
        document = ezdxf.new()
        modelspace = document.modelspace()
        modelspace.add_line((0.0, 0.0), (100.0, 50.0))
        points = _add_uncertainty_overlay(modelspace, {
            "association": {
                "dimension_target_edges": [{
                    "handle": "EDGE-1",
                    "points": [[5.0, 5.0], [5.0, 35.0]],
                    "center": None,
                    "radius": 0.0,
                    "definition_points": [{"name": "defpoint2", "point": [5.0, 5.0]}],
                }],
            },
            "registered_step_projection": {
                "lines": [{"p1": [10.0, 10.0], "p2": [40.0, 10.0], "visibility": "visible"}],
                "circles": [{"center": [25.0, 25.0], "radius": 5.0, "visibility": "visible"}],
            },
            "candidates": [{
                "feature_id": "shaft_01",
                "nominal_field": "diameter",
                "projected_feature_footprint": {
                    "geometry_kind": "cylinder",
                    "points": [[float(index), 20.0] for index in range(40)],
                },
                "geometry_verification": {
                    "score": 0.42,
                    "feature_localization": {
                        "observed_dxf_anchor": [20.0, 15.0],
                        "expected_dxf_point": [35.0, 22.0],
                    },
                },
            }],
        })
        self.assertEqual(points[-2:], [[20.0, 15.0], [35.0, 22.0]])
        self.assertIn([5.0, 5.0], points)
        self.assertGreaterEqual(len(modelspace.query("CIRCLE")), 2)
        self.assertGreaterEqual(len(modelspace.query("LWPOLYLINE")), 1)

        anchor_only_points = _add_uncertainty_overlay(modelspace, {
            "observed_dxf_anchor": [60.0, 30.0],
            "candidates": [],
        })
        self.assertEqual(anchor_only_points, [[60.0, 30.0]])

        hidden_document = ezdxf.new()
        hidden_modelspace = hidden_document.modelspace()
        hidden_points = _add_uncertainty_overlay(
            hidden_modelspace,
            {
                "observed_dxf_anchor": [60.0, 30.0],
                "registered_step_projection": {
                    "lines": [{"p1": [0.0, 0.0], "p2": [1.0, 1.0]}],
                },
                "candidates": [],
            },
            show_step_projection=False,
            show_feature_footprints=False,
            show_target_edges=False,
            show_anchor=False,
            show_links=False,
        )
        self.assertEqual(hidden_points, [])
        self.assertEqual(len(hidden_modelspace), 0)

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
        self.assertEqual(payload["algorithm"], "CANONICAL_COMPONENT_TOLERANCE_REPLAY")
        self.assertEqual(payload["total_count"], 5)
        self.assertEqual(payload["verified_count"], 5)
        self.assertEqual(len(payload["cases"]), 5)
        blade = next(item for item in payload["cases"] if item["model_name"] == "AL0W_BLADE_H-R01")
        self.assertEqual(blade["drawing_verified_extraction_count"], 0)
        details = get_tolerance_debug_component_details(blade["model_name"])
        linked = [
            item for item in details["tolerances"]
            if item.get("case_id") and (item.get("geometry_verification") or {}).get("passed")
        ]
        self.assertEqual(linked, [])
        shaft = next(item for item in payload["cases"] if item["model_name"] == "2AL0W5010H-R01")
        self.assertEqual(shaft["drawing_case_count"], 5)
        self.assertEqual(shaft["drawing_verified_extraction_count"], 2)
        shaft_details = get_tolerance_debug_component_details(shaft["model_name"])
        self.assertEqual(len(shaft_details["tolerances"]), 5)
        shaft_links = {
            item["entity_handle"]: item["matched_feature_id"]
            for item in shaft_details["tolerances"]
            if item.get("case_id")
        }
        self.assertEqual(shaft_links, {"2B11": "journal_main", "2B36": "step_01"})
        wrong_fan = next(item for item in payload["cases"] if item["model_name"] == "2AL0W4010H-R01")
        self.assertEqual(wrong_fan["drawing_verified_extraction_count"], 0)
        self.assertTrue(all(item["model_available"] for item in shaft_details["tolerances"]))
        self.assertTrue(all(item["uncertainty_diagnostic"] for item in shaft_details["tolerances"]))
        self.assertEqual(
            {item["uncertainty_diagnostic"]["status"] for item in shaft_details["tolerances"]},
            {"NO_3D_NOMINAL_CANDIDATE", "INSUFFICIENT_GEOMETRY_EVIDENCE", "AUTO_VERIFIED"},
        )


if __name__ == "__main__":
    unittest.main()
