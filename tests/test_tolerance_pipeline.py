import unittest
import os
import tempfile

import ezdxf

from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor, ExtractedDimension
from auto_2d_drawing.tolerance.feature_inference_2d import FeatureInference2DEngine
from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
from auto_2d_drawing.tolerance.feature_graph import FeatureNode, FeatureRelationGraph
from auto_2d_drawing.tolerance.feature_graph import candidate_feature_types_for_dimension
from auto_2d_drawing.tolerance.ingest_historical_data import HistoricalDataIngestor
from auto_2d_drawing.tolerance.tolerance_decision_service import ToleranceDecisionService


class ToleranceCaseQualityTests(unittest.TestCase):
    @staticmethod
    def _case(status="AUTO_VERIFIED", feature_verified=True, geometry_passed=True):
        return ToleranceCase(
            case_id="quality-case",
            part_type="GENERAL",
            feature_type="hole",
            inferred_role="HOLE_DIAMETER",
            nominal_dimensions={"diameter": 5.0},
            neighbor_types=[],
            boundary_position="INTERIOR",
            tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.02},
            confidence=0.95,
            evidence_source="company.dxf",
            description="quality test",
            verification_status=status,
            source_metadata={
                "feature_identity_verified": feature_verified,
                "geometry_verification": {"passed": geometry_passed},
            },
        )

    def test_auto_verified_extraction_requires_feature_and_geometry(self):
        self.assertTrue(self._case().is_verified_extraction())
        self.assertFalse(self._case(feature_verified=False).is_verified_extraction())
        self.assertFalse(self._case(geometry_passed=False).is_verified_extraction())

    def test_engineer_verified_feature_is_a_verified_extraction(self):
        self.assertTrue(
            self._case(status="ENGINEER_VERIFIED", geometry_passed=False).is_verified_extraction()
        )

    def test_auto_extracted_case_is_not_a_verified_extraction(self):
        self.assertFalse(self._case(status="AUTO_EXTRACTED").is_verified_extraction())


class DxfToleranceExtractorTests(unittest.TestCase):
    def setUp(self):
        self.extractor = DxfToleranceExtractor()
        self.doc = ezdxf.new()
        self.msp = self.doc.modelspace()

    def test_tolerance_only_text_is_drawing_default(self):
        entity = self.msp.add_text("%%P0.25", dxfattribs={"layer": "TITLE_BLOCK"})
        item = self.extractor._parse_text_entity(entity, "sample.dxf")

        self.assertIsNotNone(item)
        self.assertEqual(item.validation_status, "DRAWING_DEFAULT")
        self.assertFalse(item.is_feature_dimension)
        self.assertEqual(item.nominal_value, 0.0)

    def test_note_with_unit_is_rejected(self):
        entity = self.msp.add_text("2.73Kg%%P10%%%")
        item = self.extractor._parse_text_entity(entity, "sample.dxf")

        self.assertIsNotNone(item)
        self.assertEqual(item.validation_status, "REJECTED")
        self.assertFalse(item.is_feature_dimension)

    def test_dimension_geometry_and_dimstyle_tolerance_are_read(self):
        override = self.msp.add_linear_dim(
            base=(0, 2),
            p1=(0, 0),
            p2=(10, 0),
            override={"dimtol": 1, "dimtp": 0.2, "dimtm": 0.1},
        )
        override.render()
        item = self.extractor._parse_dimension_entity(override.dimension, "sample.dxf")

        self.assertAlmostEqual(item.nominal_value, 10.0)
        self.assertEqual(item.tolerance_config["source"], "DXF_DIMSTYLE")
        self.assertAlmostEqual(item.tolerance_config["upper_dev"], 0.2)
        self.assertAlmostEqual(item.tolerance_config["lower_dev"], -0.1)
        self.assertEqual(item.validation_status, "AUTO_VALIDATED")
        self.assertEqual(item.points["defpoint2"], [0.0, 0.0])
        self.assertEqual(item.points["defpoint3"], [10.0, 0.0])


class FeatureInference2DTests(unittest.TestCase):
    @staticmethod
    def _dimension(category, nominal, raw_text="", tolerance=None, points=None):
        return ExtractedDimension(
            dim_type=category,
            nominal_value=nominal,
            raw_text=raw_text,
            prefix="",
            tolerance_config=tolerance or {"mode": "CUSTOM_SYMMETRIC", "dev": 0.01},
            points=points or {"defpoint2": [0.0, 0.0], "defpoint3": [nominal, 0.0]},
            layer="DIM",
            drawing_file="sample.dxf",
            dimension_category=category,
        )

    def test_radius_is_fillet_candidate_until_tangency_is_proven(self):
        result = FeatureInference2DEngine().infer(self._dimension("RADIUS", 0.5, "R0.5"))

        self.assertEqual(result["status"], "REVIEW_CANDIDATE")
        self.assertIsNone(result["feature_type"])
        self.assertEqual(result["candidates"][0]["feature_type"], "transition_fillet")
        self.assertFalse(result["feature_identity_verified"])
        self.assertFalse(result["retrieval_eligible"])

    def test_fit_letter_case_distinguishes_hole_and_shaft(self):
        hole = self._dimension("DIAMETER", 10.0, "Ø10 H7", {"mode": "FIT", "fit_class": "H7"})
        shaft = self._dimension("DIAMETER", 10.0, "Ø10 h6", {"mode": "FIT", "fit_class": "h6"})

        self.assertEqual(FeatureInference2DEngine().infer(hole)["feature_type"], "hole")
        self.assertEqual(FeatureInference2DEngine().infer(shaft)["feature_type"], "shaft_segment")

    def test_concentric_closed_contour_does_not_guess_hole_or_shaft(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_circle((0, 0), 5)
        msp.add_circle((0, 0), 10)
        dimension = self._dimension(
            "DIAMETER",
            10.0,
            points={"defpoint": [5.0, 0.0], "defpoint4": [-5.0, 0.0]},
        )

        result = FeatureInference2DEngine(msp).infer(dimension)

        self.assertEqual(result["status"], "REVIEW_CANDIDATE")
        self.assertIsNone(result["feature_type"])
        scores = {item["feature_type"]: item["confidence"] for item in result["candidates"]}
        self.assertEqual(scores["hole"], scores["shaft_segment"])
        self.assertTrue(result["geometry_context"]["matching_circles"][0]["has_larger_concentric_circle"])

    def test_plain_linear_dimension_remains_unresolved(self):
        result = FeatureInference2DEngine().infer(self._dimension("LINEAR", 10.0))

        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertIsNone(result["feature_type"])

    def test_definition_points_recover_lost_associative_geometry(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        line = msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((0, 0), (0, 5), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((10, 0), (10, 5), dxfattribs={"layer": "VISIBLE"})
        override = msp.add_linear_dim(base=(0, 8), p1=(0, 0), p2=(10, 0))
        override.render()
        dimension = DxfToleranceExtractor()._parse_dimension_entity(override.dimension, "sample.dxf")

        result = DxfStructure2DAnalyzer(msp).analyze_dimension(dimension)

        self.assertFalse(result["native_association_available"])
        self.assertEqual(result["association_status"], "GEOMETRIC_ATTACHMENT")
        self.assertIn(line.dxf.handle, result["attached_geometry_handles"])

    def test_split_arc_circle_is_one_geometric_attachment(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        for start_angle in (0.0, 90.0, 180.0, 270.0):
            msp.add_arc(
                center=(20.0, 30.0),
                radius=5.0,
                start_angle=start_angle,
                end_angle=start_angle + 90.0,
                dxfattribs={"layer": "VISIBLE"},
            )
        dimension = self._dimension(
            "DIAMETER",
            10.0,
            points={"defpoint": [25.0, 30.0], "defpoint4": [15.0, 30.0]},
        )

        result = DxfStructure2DAnalyzer(msp).analyze_dimension(dimension)

        self.assertEqual(result["association_status"], "GEOMETRIC_ATTACHMENT")
        self.assertEqual(len(result["common_attachment_geometry_groups"]), 1)
        self.assertEqual(len(result["circular_attachment_profiles"]), 1)
        profile = result["circular_attachment_profiles"][0]
        self.assertEqual(profile["definition_point_count"], 2)
        self.assertTrue(profile["represented_by_split_arcs"])
        self.assertAlmostEqual(profile["diameter"], 10.0)

    def test_degraded_diameter_recovers_only_exact_virtual_endpoint_pair(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (20, 0), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((20, 0), (20, 10), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((20, 10), (0, 10), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((0, 10), (0, 0), dxfattribs={"layer": "VISIBLE"})
        dimension = self._dimension(
            "DIAMETER",
            10.0,
            points={
                "defpoint": [30.0, 30.0],
                "defpoint2": [5.0, 5.0],
                "defpoint3": [15.0, 5.0],
                "defpoint4": [0.0, 0.0],
            },
        )

        result = DxfStructure2DAnalyzer(msp).analyze_dimension(dimension)

        self.assertEqual(result["association_status"], "RECOVERED_DIMENSION_GEOMETRY")
        self.assertEqual(result["association_confidence"], 0.9)
        self.assertEqual(result["recovered_dimension_geometry"]["center"], [10.0, 5.0])
        self.assertEqual(result["primary_view_id"], "view_001")

    def test_degraded_diameter_does_not_recover_wrong_endpoint_distance(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (20, 0), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((20, 0), (20, 10), dxfattribs={"layer": "VISIBLE"})
        dimension = self._dimension(
            "DIAMETER",
            10.0,
            points={
                "defpoint": [30.0, 30.0],
                "defpoint2": [5.0, 5.0],
                "defpoint3": [14.0, 5.0],
                "defpoint4": [0.0, 0.0],
            },
        )

        result = DxfStructure2DAnalyzer(msp).analyze_dimension(dimension)

        self.assertEqual(result["association_status"], "NO_ATTACHMENT")
        self.assertIsNone(result["recovered_dimension_geometry"])

    def test_geometry_inside_insert_block_is_available_for_attachment(self):
        doc = ezdxf.new()
        block = doc.blocks.new(name="PART_VIEW")
        block.add_line((0, 0), (10, 0), dxfattribs={"layer": "VISIBLE"})
        block.add_line((0, 0), (0, 5), dxfattribs={"layer": "VISIBLE"})
        block.add_line((10, 0), (10, 5), dxfattribs={"layer": "VISIBLE"})
        msp = doc.modelspace()
        msp.add_blockref("PART_VIEW", (100, 50))
        dimension = self._dimension(
            "LINEAR",
            10.0,
            points={"defpoint2": [100.0, 50.0], "defpoint3": [110.0, 50.0]},
        )

        result = DxfStructure2DAnalyzer(msp).analyze_dimension(dimension)

        self.assertEqual(result["association_status"], "GEOMETRIC_ATTACHMENT")
        self.assertEqual(result["primary_view_id"], "view_001")
        self.assertTrue(result["attached_geometry_handles"])

    def test_recovered_diameter_prefers_local_view_inside_sheet_frame(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        for start, end in (
            ((0, 0), (400, 0)),
            ((400, 0), (400, 400)),
            ((400, 400), (0, 400)),
            ((0, 400), (0, 0)),
        ):
            msp.add_line(start, end)
        msp.add_line((190, 195), (210, 195), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((190, 205), (210, 205), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((190, 195), (190, 205), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((210, 195), (210, 205), dxfattribs={"layer": "VISIBLE"})
        dimension = self._dimension(
            "DIAMETER",
            10.0,
            points={
                "defpoint": [300.0, 300.0],
                "defpoint2": [195.0, 200.0],
                "defpoint3": [205.0, 200.0],
                "defpoint4": [0.0, 0.0],
            },
        )

        result = DxfStructure2DAnalyzer(msp).analyze_dimension(dimension)

        self.assertEqual(result["association_status"], "RECOVERED_DIMENSION_GEOMETRY")
        selected = next(
            view for view in DxfStructure2DAnalyzer(msp).view_clusters
            if view.view_id == result["primary_view_id"]
        )
        self.assertLess(selected.width * selected.height, 1000.0)

    def test_long_outline_connects_view_clusters_along_its_path(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (90, 0), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((0, 0), (0, 5), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((90, 0), (90, 5), dxfattribs={"layer": "VISIBLE"})
        for start_x in (0, 5, 80, 85):
            msp.add_line((start_x, 5), (start_x + 5, 5), dxfattribs={"layer": "VISIBLE"})

        analyzer = DxfStructure2DAnalyzer(msp)

        self.assertEqual(len(analyzer.view_clusters), 1)
        self.assertEqual(len(analyzer.view_clusters[0].primitive_indexes), 7)

    def test_cross_view_visible_pair_identifies_shaft_candidate(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_circle((0, 0), 5, dxfattribs={"layer": "VISIBLE"})
        msp.add_circle((0, 0), 10, dxfattribs={"layer": "VISIBLE"})
        msp.add_line((30, -5), (50, -5), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((30, 5), (50, 5), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((30, -5), (30, 5), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((50, -5), (50, 5), dxfattribs={"layer": "VISIBLE"})
        dimension = self._dimension(
            "DIAMETER",
            10.0,
            points={"defpoint": [5.0, 0.0], "defpoint4": [-5.0, 0.0]},
        )

        result = FeatureInference2DEngine(msp).infer(dimension)

        self.assertEqual(result["status"], "AUTO_INFERRED_2D")
        self.assertEqual(result["feature_type"], "shaft_segment")
        pairs = result["structure_context"]["cross_view_evidence"]["matching_visible_pairs"]
        self.assertTrue(pairs)
        self.assertTrue(pairs[0]["outer_silhouette"])

    def test_cross_view_hidden_pair_identifies_hole_candidate(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_circle((0, 0), 5, dxfattribs={"layer": "VISIBLE"})
        msp.add_line((30, -5), (50, -5), dxfattribs={"layer": "HIDDEN"})
        msp.add_line((30, 5), (50, 5), dxfattribs={"layer": "HIDDEN"})
        msp.add_line((30, -5), (30, 5), dxfattribs={"layer": "VISIBLE"})
        msp.add_line((50, -5), (50, 5), dxfattribs={"layer": "VISIBLE"})
        dimension = self._dimension(
            "DIAMETER",
            10.0,
            points={"defpoint": [5.0, 0.0], "defpoint4": [-5.0, 0.0]},
        )

        result = FeatureInference2DEngine(msp).infer(dimension)

        self.assertEqual(result["status"], "AUTO_INFERRED_2D")
        self.assertEqual(result["feature_type"], "hole")
        self.assertTrue(result["structure_context"]["cross_view_evidence"]["matching_hidden_pairs"])


class CaseRetrievalTests(unittest.TestCase):
    def test_revision_selection_keeps_only_highest_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for name in ("2FQ6V4030H-R00.dxf", "2FQ6V4030H-R02.dxf", "2FQ6V4030H-R01.dxf"):
                open(os.path.join(temp_dir, name), "w").close()

            discovery = HistoricalDataIngestor().discover_sources([temp_dir])

        self.assertEqual(list(discovery["dxf_map"]), ["2fq6v4030h-r02"])
        self.assertEqual(discovery["revision_selection"]["dxf"]["superseded_or_duplicate_files"], 2)

    def test_revision_selection_collapses_descriptive_a_revisions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for name in (
                "烤漆組合-1M242172010H-A01.dxf",
                "烤漆組合-1M242172010H-A03.dxf",
                "烤漆組合-1M242172010H-A02.dxf",
            ):
                open(os.path.join(temp_dir, name), "w").close()

            discovery = HistoricalDataIngestor().discover_sources([temp_dir])

        self.assertEqual(len(discovery["dxf_map"]), 1)
        self.assertTrue(next(iter(discovery["dxf_map"])).endswith("-a03"))
        self.assertEqual(discovery["revision_selection"]["dxf"]["superseded_or_duplicate_files"], 2)

    def test_unversioned_part_number_with_a_and_digits_is_not_a_revision(self):
        identities = [
            HistoricalDataIngestor._revision_identity(name)
            for name in ("0AJ0A00009.dxf", "0AJ0A00010.dxf")
        ]

        self.assertEqual(identities[0][0], "0aj0a00009")
        self.assertIsNone(identities[0][1])
        self.assertEqual(identities[1][0], "0aj0a00010")
        self.assertIsNone(identities[1][1])

    def test_dimension_deduplication_requires_same_geometry(self):
        common = dict(
            dim_type="LINEAR",
            nominal_value=10.0,
            raw_text="",
            prefix="",
            tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.1},
            layer="DIM",
            drawing_file="sample.dxf",
            dimension_category="LINEAR",
        )
        first = ExtractedDimension(**common, entity_handle="A", points={"defpoint2": [0, 0], "defpoint3": [10, 0]})
        duplicate = ExtractedDimension(**common, entity_handle="B", points={"defpoint2": [10, 0], "defpoint3": [0, 0]})
        distinct = ExtractedDimension(**common, entity_handle="C", points={"defpoint2": [0, 0], "defpoint3": [0, 10]})

        dimensions, suppressed = HistoricalDataIngestor._deduplicate_dimensions([first, duplicate, distinct])

        self.assertEqual(suppressed, 1)
        self.assertEqual([item.entity_handle for item in dimensions], ["A", "C"])
        self.assertEqual(dimensions[0].duplicate_entity_handles, ["B"])

    def test_raw_dxf_dimensions_remain_unresolved_in_feature_graph_taxonomy(self):
        item = DxfToleranceExtractor()._parse_dimension_entity(
            self._native_dimension_with_tolerance(),
            "sample.dxf",
        )
        feature_type, role, nominal, status = HistoricalDataIngestor._raw_feature_identity(item)

        self.assertEqual(feature_type, "unresolved_feature")
        self.assertEqual(role, "UNRESOLVED_LINEAR")
        self.assertEqual(nominal, {"length": 10.0})
        self.assertEqual(status, "AUTO_EXTRACTED")
        self.assertEqual(
            candidate_feature_types_for_dimension("RADIUS"),
            ["transition_fillet"],
        )

    @staticmethod
    def _native_dimension_with_tolerance():
        doc = ezdxf.new()
        override = doc.modelspace().add_linear_dim(
            base=(0, 2),
            p1=(0, 0),
            p2=(10, 0),
            override={"dimtol": 1, "dimtp": 0.1, "dimtm": 0.1},
        )
        override.render()
        return override.dimension

    def test_retrieved_candidate_is_not_marked_adopted_on_fallback(self):
        case = ToleranceCase(
            case_id="low-similarity",
            part_type="SHAFT",
            feature_type="shaft_segment",
            inferred_role="SHAFT_SEGMENT_DIAMETER",
            nominal_dimensions={"diameter": 10.0, "length": 2.0},
            neighbor_types=[],
            boundary_position="INTERIOR",
            tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.02},
            confidence=0.95,
            evidence_source="company.dxf",
            description="verified company case",
            verification_status="AUTO_VERIFIED",
        )
        match = {
            "case": case,
            "similarity": 0.59,
            "score_breakdown": {},
            "verification_status": "AUTO_VERIFIED",
            "product_family": "TEST",
            "same_product_family": True,
        }

        class StubCaseBase:
            cases = [case]

            @staticmethod
            def search_similar_cases_detailed(*_args, **_kwargs):
                return [match]

        node = FeatureNode(
            id="shaft-1",
            feature_type="shaft_segment",
            nominal={"diameter": 10.0, "length": 2.0},
            axial_span=[0.0, 2.0],
            center_axial=1.0,
            inferred_role="BEARING_JOURNAL",
        )
        result = ToleranceDecisionService(case_base=StubCaseBase())._evaluate_recommendation(
            "journal_main", "shaft", True, 10.0, node, "SHAFT"
        ).to_dict()

        self.assertEqual(result["retrieval_trace"]["decision_source"], "GENERAL_FALLBACK")
        self.assertFalse(result["evidence_cases"][0]["used_for_decision"])
        self.assertFalse(result["evidence_cases"][0]["used_as_context"])
        self.assertEqual(result["evidence_cases"][0]["evidence_role"], "RETRIEVED_CANDIDATE")

    def test_high_similarity_historical_case_is_explicitly_marked_adopted(self):
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        base.cases = [
            ToleranceCase(
                case_id="adopted-case",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 10.0, "length": 8.0},
                neighbor_types=["locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.02},
                confidence=0.95,
                evidence_source="1FQ6H3010H-R01.dxf",
                description="verified company case",
                verification_status="ENGINEER_VERIFIED",
            )
        ]
        node = FeatureNode(
            id="shaft-1",
            feature_type="shaft_segment",
            nominal={"diameter": 10.0, "length": 8.0},
            axial_span=[0.0, 8.0],
            center_axial=4.0,
            neighbor_types=["locating_shoulder"],
            inferred_role="BEARING_JOURNAL",
        )
        result = ToleranceDecisionService(case_base=base)._evaluate_recommendation(
            "journal_main", "shaft", True, 10.0, node, "SHAFT", product_family="FQ6H"
        ).to_dict()

        self.assertEqual(result["retrieval_trace"]["decision_source"], "HISTORICAL_CASE")
        self.assertEqual(result["retrieval_trace"]["adopted_case_id"], "adopted-case")
        self.assertTrue(result["evidence_cases"][0]["used_for_decision"])
        self.assertEqual(result["evidence_cases"][0]["evidence_role"], "ADOPTED_HISTORICAL_CASE")

    def test_custom_limits_from_different_nominal_are_context_only(self):
        case = ToleranceCase(
            case_id="diameter-35",
            part_type="FAN_HOUSING",
            feature_type="shaft_segment",
            inferred_role="MAIN_SHAFT_BODY",
            nominal_dimensions={"diameter": 35.0, "length": 16.0},
            neighbor_types=[],
            boundary_position="INTERIOR",
            tolerance_config={"mode": "CUSTOM_LIMITS", "upper_dev": 0.2, "lower_dev": 0.0},
            confidence=0.97,
            evidence_source="1AL0W5000H-R03.dxf",
            description="verified 35 mm case",
            verification_status="AUTO_VERIFIED",
        )
        match = {
            "case": case,
            "similarity": 0.92,
            "score_breakdown": {},
            "verification_status": "AUTO_VERIFIED",
            "product_family": "AL0W",
            "same_product_family": True,
        }

        class StubCaseBase:
            cases = [case]

            @staticmethod
            def search_similar_cases_detailed(*_args, **_kwargs):
                return [match]

        node = FeatureNode(
            id="shaft-32",
            feature_type="shaft_segment",
            nominal={"diameter": 32.0, "length": 14.5},
            axial_span=[0.0, 14.5],
            center_axial=7.25,
            inferred_role="MAIN_SHAFT_BODY",
        )
        result = ToleranceDecisionService(case_base=StubCaseBase())._evaluate_recommendation(
            "shaft_32", "shaft", True, 32.0, node, "FAN_HOUSING", product_family="AL0W"
        ).to_dict()

        self.assertEqual(result["decision_status"], "REVIEW_REQUIRED")
        self.assertEqual(result["retrieval_trace"]["compatible_case_count"], 0)
        self.assertFalse(result["evidence_cases"][0]["nominal_transfer_compatible"])
        self.assertFalse(result["evidence_cases"][0]["used_for_decision"])

    def test_hole_rule_matches_hole_feature_node(self):
        graph = FeatureRelationGraph(part_type="FAN_HOUSING")
        hole = FeatureNode(
            id="hole-34",
            feature_type="hole",
            nominal={"diameter": 34.25, "length": 3.5},
            axial_span=[0.0, 0.0],
            center_axial=0.0,
        )
        graph.add_node(hole)

        matched = ToleranceDecisionService(case_base=FeatureCaseBase.__new__(FeatureCaseBase))._match_rule_to_node(
            {"category": "hole", "nominal_value": 34.25},
            graph,
        )

        self.assertIs(matched, hole)

    def test_only_verified_or_strictly_auto_verified_cases_are_eligible(self):
        base_kwargs = dict(
            case_id="status-check",
            part_type="SHAFT",
            feature_type="shaft_segment",
            inferred_role="SHAFT_DIAMETER",
            nominal_dimensions={"diameter": 10.0},
            neighbor_types=[],
            boundary_position="INTERIOR",
            tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.02},
            confidence=0.9,
            evidence_source="company.dxf",
            description="status check",
        )
        for status in ("ENGINEER_VERIFIED", "AUTO_VERIFIED"):
            self.assertTrue(ToleranceCase(**base_kwargs, verification_status=status).is_retrieval_eligible())
        for status in ("AUTO_EXTRACTED", "UNVERIFIED", "SEED_REFERENCE"):
            self.assertFalse(ToleranceCase(**base_kwargs, verification_status=status).is_retrieval_eligible())

    def test_retrieval_normalizes_feature_type_and_excludes_unverified(self):
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        base.cases = [
            ToleranceCase(
                case_id="verified",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 3.0, "length": 8.0},
                neighbor_types=["locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "h6"},
                confidence=0.9,
                evidence_source="drawing-a",
                description="verified case",
                verification_status="ENGINEER_VERIFIED",
            ),
            ToleranceCase(
                case_id="unverified",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 3.0, "length": 8.0},
                neighbor_types=["locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "p6"},
                confidence=0.99,
                evidence_source="drawing-b",
                description="unverified case",
            ),
        ]
        node = FeatureNode(
            id="cylinder-1",
            feature_type="cylinder",
            nominal={"diameter": 3.0, "length": 8.0},
            axial_span=[0.0, 8.0],
            center_axial=4.0,
            neighbor_types=["locating_shoulder"],
            inferred_role="BEARING_JOURNAL",
        )

        matches = base.search_similar_cases_detailed(node, part_type="SHAFT")

        self.assertEqual([item["case"].case_id for item in matches], ["verified"])
        self.assertEqual(matches[0]["score_breakdown"]["feature_type"], 1.0)

    def test_step_length_does_not_adopt_bearing_fit_case(self):
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        base.cases = [
            ToleranceCase(
                case_id="bearing-diameter",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 3.0, "length": 0.9},
                neighbor_types=["locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "h6"},
                confidence=0.95,
                evidence_source="drawing-bearing",
                description="bearing diameter case",
                verification_status="ENGINEER_VERIFIED",
            )
        ]
        node = FeatureNode(
            id="shaft-1",
            feature_type="shaft_segment",
            nominal={"diameter": 3.0, "length": 0.9},
            axial_span=[0.0, 0.9],
            center_axial=0.45,
            neighbor_types=["locating_shoulder"],
            inferred_role="BEARING_JOURNAL",
        )
        service = ToleranceDecisionService(case_base=base)

        result = service._evaluate_recommendation(
            "step_len_1", "step", False, 0.9, node, "SHAFT"
        ).to_dict()

        self.assertEqual(result["tier_level"], "TIER_3_GENERAL_FALLBACK")
        self.assertEqual(result["recommended_mode"], "NONE")
        self.assertEqual(result["confidence"], 0.35)
        self.assertEqual(result["retrieval_trace"]["retrieved_case_count"], 1)
        self.assertEqual(result["retrieval_trace"]["compatible_case_count"], 0)
        self.assertFalse(result["evidence_cases"][0]["used_for_decision"])

    def test_same_product_family_is_ranked_before_cross_family(self):
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        common = dict(
            part_type="SHAFT",
            feature_type="shaft_segment",
            inferred_role="BEARING_JOURNAL",
            nominal_dimensions={"diameter": 3.0, "length": 8.0},
            neighbor_types=["locating_shoulder"],
            boundary_position="INTERIOR",
            tolerance_config={"mode": "FIT", "fit_class": "h6"},
            confidence=0.8,
            description="company case",
        )
        base.cases = [
            ToleranceCase(case_id="cross", evidence_source="1AJ0A3010H-R01.dxf", **common),
            ToleranceCase(case_id="same", evidence_source="1FQ6H3010H-R01.dxf", **common),
        ]
        node = FeatureNode(
            id="shaft-1",
            feature_type="shaft_segment",
            nominal={"diameter": 3.0, "length": 8.0},
            axial_span=[0.0, 8.0],
            center_axial=4.0,
            neighbor_types=["locating_shoulder"],
            inferred_role="BEARING_JOURNAL",
        )

        matches = base.search_similar_cases_detailed(
            node,
            part_type="SHAFT",
            include_unverified=True,
            product_family="FQ6H",
        )

        self.assertEqual(matches[0]["case"].case_id, "same")
        self.assertTrue(matches[0]["same_product_family"])
        self.assertGreater(
            matches[0]["score_breakdown"]["product_family"],
            matches[1]["score_breakdown"]["product_family"],
        )

    def test_retrieval_does_not_mix_linear_and_diameter_cases(self):
        base = FeatureCaseBase.__new__(FeatureCaseBase)
        common = dict(
            part_type="SHAFT",
            feature_type="shaft_segment",
            inferred_role="SHAFT_SEGMENT",
            nominal_dimensions={"diameter": 10.0, "length": 8.0},
            neighbor_types=[],
            boundary_position="INTERIOR",
            tolerance_config={"mode": "CUSTOM_LIMITS", "upper_dev": 0.1, "lower_dev": -0.1},
            confidence=0.9,
            description="company case",
            verification_status="AUTO_VERIFIED",
        )
        base.cases = [
            ToleranceCase(
                case_id="diameter",
                evidence_source="diameter.dxf",
                source_metadata={"dimension_category": "DIAMETER"},
                **common,
            ),
            ToleranceCase(
                case_id="linear",
                evidence_source="linear.dxf",
                source_metadata={"dimension_category": "LINEAR"},
                **common,
            ),
        ]
        node = FeatureNode(
            id="shaft-1",
            feature_type="shaft_segment",
            nominal={"diameter": 10.0, "length": 8.0},
            axial_span=[0.0, 8.0],
            center_axial=4.0,
        )
        matches = base.search_similar_cases_detailed(
            node,
            dimension_category="DIAMETER",
        )
        self.assertEqual([item["case"].case_id for item in matches], ["diameter"])


if __name__ == "__main__":
    unittest.main()
