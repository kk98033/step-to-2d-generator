import tempfile
import unittest
from pathlib import Path

from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase
from auto_2d_drawing.tolerance.composite_features import (
    CompositeAxialSpanBuilder,
    build_annular_rim_features,
    build_planar_span_features_from_planes,
    composite_descriptor,
    dimension_endpoints_to_axial_span,
    match_transverse_diameter,
)
from auto_2d_drawing.tolerance.feature_graph import FeatureNode, FeatureRelationGraph


def atomic(node_id, feature_type, start, end, role):
    return FeatureNode(
        id=node_id,
        feature_type=feature_type,
        nominal={"length": end - start},
        axial_span=[start, end],
        center_axial=(start + end) / 2.0,
        source_info={"type": feature_type},
        inferred_role=role,
    )


class CompositeFeatureTests(unittest.TestCase):
    def setUp(self):
        self.graph = FeatureRelationGraph("SHAFT", main_axis="y", total_length=22.1)
        self.graph.add_node(FeatureNode(
            id="shaft_overall", feature_type="overall_dimension",
            nominal={"length": 22.1}, axial_span=[-23.1, -1.0], center_axial=-12.05,
        ))
        self.graph.add_node(atomic("lead_in", "pilot_chamfer", -23.1, -21.586, "LEAD_IN_PILOT"))
        self.graph.add_node(atomic("fillet", "transition_fillet", -21.7, -21.4, "GROOVE_FILLET"))
        self.graph.add_node(atomic("step", "locating_shoulder", -21.514, -21.3, "SHAFT_STEP"))
        self.graph.add_node(atomic("groove", "retaining_ring_groove", -21.3, -20.4, "SNAP_RING_GROOVE"))

    def test_enumerates_expected_end_composite(self):
        composites = CompositeAxialSpanBuilder().enumerate(self.graph)
        match = next(node for node in composites if abs(node.nominal["length"] - 1.8) < 1e-9)
        self.assertEqual(
            match.source_info["member_feature_ids"],
            ["lead_in", "fillet", "step"],
        )
        self.assertEqual(match.inferred_role, "PILOT_END_REGION")
        self.assertEqual(match.boundary_position, "LEFT_END")

    def test_builds_annular_rim_from_coaxial_outer_and_inner_cylinders(self):
        graph = FeatureRelationGraph("GENERAL", main_axis="y", total_length=15.0)
        graph.add_node(FeatureNode(
            id="outer", feature_type="shaft_segment",
            nominal={"diameter": 32.0, "length": 14.5},
            axial_span=[0.0, 14.5], center_axial=7.25,
            source_info={
                "type": "cylinder", "center": [0.0, 7.25, 0.0],
                "axis_dir": [0.0, 1.0, 0.0], "length": 14.5,
            },
        ))
        graph.add_node(FeatureNode(
            id="inner", feature_type="hole",
            nominal={"diameter": 31.0, "length": 14.2},
            axial_span=[0.1, 14.3], center_axial=7.2,
            source_info={
                "type": "cylinder", "center": [0.0, 7.2, 0.0],
                "axis_dir": [0.0, 1.0, 0.0], "length": 14.2,
            },
        ))

        rims = build_annular_rim_features(graph)

        self.assertEqual(len(rims), 1)
        self.assertEqual(rims[0].nominal["outer_diameter"], 32.0)
        self.assertEqual(rims[0].nominal["inner_diameter"], 31.0)
        self.assertEqual(rims[0].source_info["member_feature_ids"], ["outer", "inner"])

    def test_registered_endpoints_resolve_composite(self):
        registration = {
            "projection": {"direction": [-1.0, 0.0, 0.0], "up": [0.0, 1.0, 0.0]},
            "transform": {
                "scale": 1.0, "rotation_degrees": 0.0,
                "translation": [231.756, 141.17], "mirrored": True,
            },
        }
        endpoints = [[254.856, 140.0], [253.056, 140.5]]
        span = dimension_endpoints_to_axial_span(self.graph, endpoints, registration)
        self.assertAlmostEqual(span[0], -23.1, places=3)
        self.assertAlmostEqual(span[1], -21.3, places=3)
        resolved = CompositeAxialSpanBuilder().match_projected_dimension(
            self.graph, endpoints, 1.8, registration
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["node"].source_info["member_feature_ids"], ["lead_in", "fillet", "step"])

    def test_builds_requested_span_between_opposite_coplanar_groups(self):
        graph = FeatureRelationGraph("FAN_HOUSING", main_axis="z", total_length=25.991)
        planes = [
            {"normal": (0.0, 0.0, 1.0), "center_of_mass": (0.2, 0.0, 0.0), "area": 1702.9},
            {"normal": (0.0, 0.0, -1.0), "center_of_mass": (-34.7, 21.5, 15.5), "area": 27.9},
            # Same plane position must collapse instead of creating another feature.
            {"normal": (0.0, 0.0, -1.0), "center_of_mass": (36.0, 19.6, 15.5), "area": 20.7},
            {"normal": (0.0, 0.0, -1.0), "center_of_mass": (0.0, 0.0, 12.0), "area": 100.0},
        ]

        spans = build_planar_span_features_from_planes(graph, planes, [15.5])

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].feature_type, "composite_planar_span")
        self.assertEqual(spans[0].nominal["length"], 15.5)
        self.assertEqual(spans[0].source_info["plane_positions"], [0.0, 15.5])

    def test_case_base_retrieves_same_composite_semantics(self):
        source_node = next(
            node for node in CompositeAxialSpanBuilder().enumerate(self.graph)
            if node.source_info["member_feature_ids"] == ["lead_in", "fillet", "step"]
        )
        with tempfile.TemporaryDirectory() as directory:
            base = FeatureCaseBase(str(Path(directory) / "cases.json"))
            base.add_case(ToleranceCase(
                case_id="AUTO_COMPOSITE_5010_26FA",
                part_type="SHAFT",
                feature_type=source_node.feature_type,
                inferred_role=source_node.inferred_role,
                nominal_dimensions=source_node.nominal,
                neighbor_types=source_node.neighbor_types,
                boundary_position=source_node.boundary_position,
                tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.05},
                confidence=0.95,
                evidence_source="2AL0W5010H-R01.dxf",
                description="Verified composite pilot-end span.",
                verification_status="AUTO_VERIFIED",
                source_metadata={
                    "dimension_category": "LINEAR",
                    "feature_identity_verified": True,
                    "geometry_verification": {"passed": True},
                    "composite_descriptor": composite_descriptor(source_node),
                    "drawing_file": "2AL0W5010H-R01.dxf",
                },
            ))
            query = FeatureNode(
                id="future_model_pilot_end",
                feature_type="compound_axial_span",
                nominal={"length": 1.82},
                axial_span=[0.0, 1.82],
                center_axial=0.91,
                neighbor_types=list(source_node.neighbor_types),
                boundary_position="LEFT_END",
                inferred_role="PILOT_END_REGION",
                source_info={"composite_descriptor": composite_descriptor(source_node)},
            )
            matches = base.search_similar_cases_detailed(
                query, part_type="SHAFT", product_family="AL0W", dimension_category="LINEAR"
            )
            self.assertEqual(matches[0]["case"].case_id, "AUTO_COMPOSITE_5010_26FA")
            self.assertEqual(matches[0]["score_breakdown"]["composite_structure"], 1.0)
            self.assertGreater(matches[0]["similarity"], 0.9)

    def test_endpoint_span_builds_composite_without_member_count_limit(self):
        graph = FeatureRelationGraph("SHAFT", main_axis="z", total_length=8.4)
        graph.add_node(FeatureNode(
            id="overall", feature_type="overall_dimension", nominal={"length": 8.4},
            axial_span=[0.0, 8.4], center_axial=4.2,
        ))
        for index in range(7):
            graph.add_node(atomic(
                f"step_{index}", "locating_shoulder", index * 1.2, (index + 1) * 1.2, "SHAFT_STEP"
            ))
        node = CompositeAxialSpanBuilder().build_from_axial_span(graph, [-0.002, 8.399], 8.4)
        self.assertIsNotNone(node)
        self.assertEqual(node.axial_span, [0.0, 8.4])
        self.assertGreater(node.source_info["composite_descriptor"]["member_count"], 4)

    def test_transverse_linear_dimension_recovers_diameter(self):
        graph = FeatureRelationGraph("SHAFT", main_axis="z", total_length=20.0)
        graph.add_node(FeatureNode(
            id="step_at_six", feature_type="locating_shoulder",
            nominal={"diameter": 10.9, "length": 1.0},
            axial_span=[6.0, 7.0], center_axial=6.5,
            source_info={"type": "step"}, inferred_role="SHAFT_STEP",
        ))
        registration = {
            "projection": {"direction": [1.0, 0.0, 0.0], "up": [0.0, 0.0, 1.0]},
            "transform": {
                "scale": 1.0, "rotation_degrees": 0.0,
                "translation": [100.0, 100.0], "mirrored": False,
            },
        }
        recovered = match_transverse_diameter(
            graph,
            [[106.5, 94.55], [106.5, 105.45]],
            10.9,
            registration,
        )
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["node"].id, "step_at_six")
        self.assertEqual(recovered["nominal_field"], "diameter")


if __name__ == "__main__":
    unittest.main()
