import copy
import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.dimension_intents import (
    apply_projection_bounds_validation,
    build_dimension_intent_set,
    deduplicate_dimension_intents,
    normalise_annular_intents,
    normalise_solid_shaft_intents,
    normalise_thin_wall_intents,
)


class DimensionIntentTests(unittest.TestCase):
    def test_normalises_annular_contract_and_topology(self):
        result = {
            "features": [{"id": "annular_1", "solid_index": 2, "inner_face_index": 4, "outer_face_index": 7, "axis": [0, 0, 1]}],
            "dimensions": [{"id": "annular_1.outer", "composite_feature_id": "annular_1", "semantic": "annular_section.outer_diameter", "dim_type": "DIAMETER", "nominal_value": 10, "view": "top", "anchors": [[-5, 0], [5, 0]], "confidence": .95, "verification_status": "AUTO_VERIFIED"}],
        }
        intent = normalise_annular_intents(result)[0]
        self.assertEqual(intent["measurement"]["role"], "OUTER_DIAMETER")
        self.assertEqual(intent["feature"]["topology_refs"], ["face:7"])
        self.assertEqual(intent["renderer_contract"]["renderer"], "DIAMETER_RENDERER")
        self.assertEqual(intent["verification"]["status"], "AUTO_VERIFIED")

    def test_normalises_shaft_overall_relation(self):
        result = {
            "chains": [{"id": "shaft_1", "solid_index": 1, "axis": [1, 0, 0], "segments": [{"id": "seg_1", "face_index": 9}], "shoulders": []}],
            "dimensions": [{"id": "shaft_1.overall", "shaft_chain_id": "shaft_1", "segment_id": None, "semantic": "solid_shaft.overall_length", "dim_type": "LINEAR", "nominal_value": 12, "view": "front", "anchors": [[0, 0], [12, 0]], "confidence": .98, "verification_status": "AUTO_VERIFIED"}],
        }
        intent = normalise_solid_shaft_intents(result)[0]
        self.assertEqual(intent["measurement"]["role"], "AXIAL_OVERALL_LENGTH")
        self.assertEqual(intent["feature"]["material_relation"], "SOLID_VERTEX_EXTENT")
        self.assertEqual(intent["relationships"]["related_feature_ids"], ["seg_1"])

    def test_merges_duplicate_topological_measurement(self):
        base = normalise_annular_intents({
            "features": [{"id": "a", "solid_index": 1, "inner_face_index": 2, "outer_face_index": 3, "axis": [0, 0, 1]}],
            "dimensions": [{"id": "d1", "composite_feature_id": "a", "semantic": "annular_section.outer_diameter", "dim_type": "DIAMETER", "nominal_value": 10, "view": "top", "anchors": [[-5, 0], [5, 0]], "confidence": .9, "verification_status": "AUTO_VERIFIED"}],
        })[0]
        duplicate = copy.deepcopy(base)
        duplicate["source_candidates"][0]["dimension_id"] = "d2"
        merged, duplicates, conflicts = deduplicate_dimension_intents([base, duplicate])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["deduplication"]["merged_source_count"], 2)
        self.assertEqual(merged[0]["relationships"]["parent_feature_ids"], ["a"])
        self.assertEqual(len(duplicates), 1)
        self.assertFalse(conflicts)

    def test_preserves_and_flags_conflicting_nominals(self):
        base = normalise_annular_intents({
            "features": [{"id": "a", "solid_index": 1, "inner_face_index": 2, "outer_face_index": 3, "axis": [0, 0, 1]}],
            "dimensions": [{"id": "d1", "composite_feature_id": "a", "semantic": "annular_section.outer_diameter", "dim_type": "DIAMETER", "nominal_value": 10, "view": "top", "anchors": [[-5, 0], [5, 0]], "confidence": .9, "verification_status": "AUTO_VERIFIED"}],
        })[0]
        conflict = copy.deepcopy(base)
        conflict["measurement"]["nominal_value"] = 10.2
        conflict["source_candidates"][0]["dimension_id"] = "d2"
        merged, _, conflicts = deduplicate_dimension_intents([base, conflict])
        self.assertEqual(len(merged), 2)
        self.assertEqual({item["verification"]["status"] for item in merged}, {"REVIEW_CONFLICT"})
        self.assertEqual(len({item["intent_id"] for item in merged}), 2)
        self.assertEqual(len(conflicts), 1)

    def test_builder_reports_invalid_anchor_mismatch(self):
        result = {
            "features": [{"id": "a", "solid_index": 1, "inner_face_index": 2, "outer_face_index": 3, "axis": [0, 0, 1]}],
            "dimensions": [{"id": "d1", "composite_feature_id": "a", "semantic": "annular_section.inner_diameter", "dim_type": "DIAMETER", "nominal_value": 8, "view": "top", "anchors": [[0, 0], [2, 0]], "confidence": .9, "verification_status": "AUTO_VERIFIED"}],
        }
        built = build_dimension_intent_set(annular_result=result)
        self.assertEqual(built["status_counts"]["INVALID"], 1)
        self.assertIn("ANCHOR_NOMINAL_MISMATCH", built["intents"][0]["verification"]["issues"])

    def test_rejects_anchors_outside_owning_solid_projection(self):
        result = {
            "features": [{"id": "a", "solid_index": 2, "inner_face_index": 2, "outer_face_index": 3, "axis": [0, 0, 1]}],
            "dimensions": [{"id": "d1", "composite_feature_id": "a", "semantic": "annular_section.axial_thickness", "dim_type": "LINEAR", "nominal_value": 2, "view": "top", "anchors": [[50, 0], [52, 0]], "confidence": .9, "verification_status": "AUTO_VERIFIED"}],
        }
        built = build_dimension_intent_set(
            annular_result=result,
            projection_bounds_by_solid={2: {"top": [0, -5, 20, 5]}},
        )
        intent = built["intents"][0]
        self.assertEqual(intent["verification"]["status"], "INVALID")
        self.assertIn("ANCHORS_OUTSIDE_OWNING_SOLID_PROJECTION", intent["verification"]["issues"])

    def test_accepts_anchors_inside_owning_solid_projection(self):
        result = {
            "features": [{"id": "a", "solid_index": 1, "inner_face_index": 2, "outer_face_index": 3, "axis": [0, 0, 1]}],
            "dimensions": [{"id": "d1", "composite_feature_id": "a", "semantic": "annular_section.axial_thickness", "dim_type": "LINEAR", "nominal_value": 2, "view": "top", "anchors": [[10, 0], [12, 0]], "confidence": .9, "verification_status": "AUTO_VERIFIED"}],
        }
        built = build_dimension_intent_set(
            annular_result=result,
            projection_bounds_by_solid={1: {"top": [0, -5, 20, 5]}},
        )
        check = next(item for item in built["intents"][0]["verification"]["checks"] if item["name"] == "anchors_inside_owning_solid_projection")
        self.assertTrue(check["passed"])

    def test_normalises_generic_thin_wall_and_preserves_tolerance_provenance(self):
        result = {
            "features": [{
                "id": "thin_wall_1",
                "solid_index": 0,
                "topology_refs": ["section_curve:4", "section_curve:5"],
                "axis": [0, 0, 1],
            }],
            "dimensions": [{
                "id": "thin_wall_1.thickness",
                "feature_id": "thin_wall_1",
                "semantic": "thin_wall.normal_thickness",
                "dim_type": "LINEAR",
                "nominal_value": 0.2513,
                "display_value": 0.25,
                "tolerance": {"mode": "SYMMETRIC", "upper": 0.03, "lower": -0.03},
                "tolerance_source": "HISTORICAL_CASE:test",
                "view": "section_0.5",
                "anchors": [[1, 2], [1.2513, 2]],
                "confidence": 0.97,
                "verification_status": "AUTO_VERIFIED",
            }],
        }
        intent = normalise_thin_wall_intents(result)[0]
        self.assertEqual(intent["measurement"]["role"], "NORMAL_WALL_THICKNESS")
        self.assertEqual(intent["feature"]["family"], "THIN_WALL")
        self.assertEqual(intent["renderer_contract"]["renderer"], "THICKNESS_RENDERER")
        self.assertEqual(intent["measurement"]["display_value"], 0.25)
        self.assertEqual(intent["measurement"]["tolerance_source"], "HISTORICAL_CASE:test")
        self.assertEqual(intent["verification"]["status"], "AUTO_VERIFIED")

    def test_variable_thin_wall_remains_review_candidate(self):
        result = {
            "features": [{
                "id": "thin_wall_1", "solid_index": 0,
                "topology_refs": ["curve:1", "curve:2"], "axis": [0, 0, 1],
            }],
            "dimensions": [{
                "id": "thin_wall_1.profile", "feature_id": "thin_wall_1",
                "semantic": "thin_wall.thickness_profile", "dim_type": "LINEAR",
                "nominal_value": 0.27, "display_text": "T 0.250~0.281 mm [REVIEW]",
                "view": "section_0.3", "anchors": [[0, 0], [0.27, 0]],
                "confidence": 0.7, "verification_status": "REVIEW_CANDIDATE",
            }],
        }
        built = build_dimension_intent_set(thin_wall_result=result)
        intent = built["intents"][0]
        self.assertEqual(intent["measurement"]["role"], "THICKNESS_PROFILE")
        self.assertEqual(intent["verification"]["status"], "REVIEW_CANDIDATE")
        self.assertEqual(intent["renderer_contract"]["renderer"], "THICKNESS_RENDERER")


if __name__ == "__main__":
    unittest.main()
