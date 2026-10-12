import unittest
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from auto_2d_drawing.drawing_projection import feature_view_geometry, project_point
from auto_2d_drawing.part_classifier import normalize_part_semantic_type
from auto_2d_drawing.smart_annotation_engine import TemplateManager
from auto_2d_drawing.smart_extractors.smart_rule_engine import SmartDimensionEngine, SmartRuleExtractor


def _views():
    return {
        "front": {"visible": [], "hidden": [], "bbox": (-50.0, -40.0, 50.0, 40.0), "size": (100.0, 80.0)},
        "top": {"visible": [], "hidden": [], "bbox": (-50.0, -20.0, 50.0, 20.0), "size": (100.0, 40.0)},
        "right": {"visible": [], "hidden": [], "bbox": (-20.0, -40.0, 20.0, 40.0), "size": (40.0, 80.0)},
    }


class DrawingProjectionTests(unittest.TestCase):
    def test_view_projection_preserves_off_center_feature_location(self):
        self.assertEqual(project_point((10.0, 20.0, 30.0), "front"), (20.0, -10.0))
        self.assertEqual(project_point((10.0, 20.0, 30.0), "top"), (-30.0, -10.0))
        footprint = feature_view_geometry(
            {"center": [10.0, 20.0, 30.0], "size": [4.0, 6.0, 8.0]},
            "front",
        )
        self.assertEqual(footprint["center"], [20.0, -10.0])
        self.assertEqual(footprint["bbox"], [17.0, -12.0, 23.0, -8.0])


class DrawingPartClassificationTests(unittest.TestCase):
    def test_fan_aliases_share_impeller_taxonomy(self):
        self.assertEqual(normalize_part_semantic_type("FAN"), "FAN_IMPELLER")
        self.assertEqual(normalize_part_semantic_type("GENERIC", "AL0W_BLADE_H-R01"), "FAN_IMPELLER")
        self.assertEqual(normalize_part_semantic_type("SHAFT"), "SHAFT")
        self.assertEqual(normalize_part_semantic_type("GENERIC", "fan_housing_cover"), "FAN_HOUSING")
        self.assertEqual(normalize_part_semantic_type("GENERIC", "stamped_dish_base"), "STAMPED_FAN_BASE")
        self.assertEqual(normalize_part_semantic_type("GENERIC", "mounting_bracket"), "GENERIC")


class SmartDrawingRuleTests(unittest.TestCase):
    @patch("auto_2d_drawing.smart_extractors.smart_rule_engine.extract_canonical_features")
    def test_general_overall_rule_keeps_neutral_identity_and_projected_anchors(self, extract):
        extract.return_value = SimpleNamespace(
            feature_extractor=SimpleNamespace(W=100.0, H=80.0),
            part_type="GENERAL_MECHANICAL",
            records=[{
                "id": "bbox_overall",
                "type": "overall_size",
                "name": "overall",
                "view": "front",
                "role": "assembly",
                "nominal": {"W": 100.0, "H": 80.0, "D": 40.0},
                "geometry": {"center": [10.0, 20.0, 30.0], "size": [100.0, 80.0, 40.0]},
            }],
        )

        rules = SmartRuleExtractor().extract_all_candidate_rules(object(), _views(), part_hint="bracket")

        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertEqual(rule["rule_id"], "bbox_overall")
        self.assertEqual(rule["id"], "bbox_overall")
        self.assertNotIn("軸整體", rule["name"])
        self.assertEqual(rule["part_type"], "GENERAL_MECHANICAL")
        self.assertEqual(rule["geometry_payload"]["start_proj"], [20.0, -60.0])
        self.assertEqual(rule["geometry_payload"]["end_proj"], [20.0, 40.0])

    @patch("auto_2d_drawing.smart_extractors.smart_rule_engine.extract_canonical_features")
    def test_multi_view_diameter_has_independent_feature_footprints(self, extract):
        extract.return_value = SimpleNamespace(
            feature_extractor=SimpleNamespace(W=20.0, H=10.0),
            part_type="SHAFT",
            records=[{
                "id": "journal_main",
                "type": "shaft_or_boss",
                "name": "journal",
                "view": "front",
                "role": "bearing_journal",
                "nominal": {"diameter": 6.0, "length": 12.0},
                "geometry": {"center": [10.0, 20.0, 30.0], "size": [12.0, 6.0, 6.0]},
            }],
        )

        rules = SmartRuleExtractor().extract_all_candidate_rules(object(), _views())
        diameter = next(rule for rule in rules if rule["category"] == "shaft")

        self.assertEqual(diameter["target_views"], ["front", "top"])
        self.assertEqual(diameter["view_geometry"]["front"]["center"], [20.0, -10.0])
        self.assertEqual(diameter["view_geometry"]["top"]["center"], [-30.0, -10.0])
        self.assertNotEqual(
            diameter["view_geometry"]["front"]["start_proj"],
            diameter["view_geometry"]["top"]["start_proj"],
        )

    @patch("auto_2d_drawing.smart_extractors.smart_rule_engine.extract_canonical_features")
    def test_datum_face_does_not_invent_one_millimeter_thickness(self, extract):
        extract.return_value = SimpleNamespace(
            feature_extractor=SimpleNamespace(W=50.0, H=50.0),
            part_type="FAN_HOUSING",
            records=[{
                "id": "datum_primary_face",
                "type": "wall_thickness",
                "name": "Datum B",
                "view": "front",
                "role": "datum_face",
                "nominal": {"datum": "B"},
                "geometry": {"center": [0.0, 0.0, 0.0], "size": [40.0, 40.0, 0.2]},
            }],
        )

        rule = SmartRuleExtractor().extract_all_candidate_rules(object(), _views())[0]

        self.assertFalse(rule["enabled"])
        self.assertEqual(rule["nominal_value"], 0.0)
        self.assertEqual(rule["category"], "datum")

    @patch("auto_2d_drawing.smart_extractors.smart_rule_engine.export_png")
    @patch("auto_2d_drawing.smart_extractors.smart_rule_engine.export_pdf")
    @patch("auto_2d_drawing.smart_extractors.smart_rule_engine.DxfDrawer")
    @patch("auto_2d_drawing.smart_extractors.smart_rule_engine.TitleBlock")
    @patch("auto_2d_drawing.smart_extractors.smart_rule_engine.LayoutEngine")
    @patch("auto_2d_drawing.smart_extractors.smart_rule_engine.setup_document")
    def test_renderer_uses_selected_views_own_projected_center(
        self,
        setup_document,
        layout_engine_cls,
        _title_block,
        _drawer,
        _export_pdf,
        _export_png,
    ):
        document = MagicMock()
        document.modelspace.return_value = MagicMock()
        setup_document.return_value = document
        configured = [{
            "rule_id": "offset_hole",
            "enabled": True,
            "category": "hole",
            "dim_type": "DIAMETER",
            "nominal_value": 6.0,
            "views": ["top"],
            "side": "LEFT",
            "geometry_payload": {"center": [10.0, 20.0], "radius": 3.0},
            "view_geometry": {
                "top": {
                    "center": [10.0, -30.0],
                    "radius": 3.0,
                    "start_proj": [7.0, -30.0],
                    "end_proj": [13.0, -30.0],
                },
            },
        }]
        with tempfile.TemporaryDirectory() as directory:
            SmartDimensionEngine().render_custom_drawing(
                os.path.join(directory, "drawing.dxf"),
                os.path.join(directory, "drawing.pdf"),
                os.path.join(directory, "drawing.png"),
                configured,
                _views(),
            )

        top_render = next(
            call for call in layout_engine_cls.return_value.render.call_args_list
            if call.args[1][0].view_name == "top"
        )
        task = top_render.args[1][0]
        self.assertEqual(task.center, (10.0, -30.0))
        self.assertEqual(task.radius, 3.0)


class DrawingTemplateTests(unittest.TestCase):
    def test_template_applies_complete_drawing_rule_fields(self):
        candidate = {
            "rule_id": "blade_radius_01",
            "category": "fillet",
            "dim_type": "LEADER",
            "name": "Blade tip radius",
            "role": "blade_tip",
            "geometry_payload": {"angle": 45.0},
            "view_geometry": {"front": {"angle": 45.0}},
        }
        template = {
            "rules": [{
                "feature_type": "fillet",
                "role_pattern": "blade_tip",
                "enabled": True,
                "preferred_view": "front",
                "views": ["front", "right"],
                "dim_type": "LEADER",
                "side": "TOP",
                "baseline": "DATUM_A",
                "rank": 3,
                "prefix": "R",
                "leader_angle": 135.0,
                "tolerance": "±0.05",
            }],
        }

        result = TemplateManager().match_and_apply(template, [candidate])[0]

        self.assertEqual(result["views"], ["front", "right"])
        self.assertEqual(result["target_views"], ["front", "right"])
        self.assertEqual(result["sides"], ["TOP"])
        self.assertEqual(result["baseline"], "DATUM_A")
        self.assertEqual(result["rank"], 3)
        self.assertEqual(result["prefix"], "R")
        self.assertEqual(result["geometry_payload"]["angle"], 135.0)
        self.assertEqual(result["view_geometry"]["front"]["angle"], 135.0)


if __name__ == "__main__":
    unittest.main()
