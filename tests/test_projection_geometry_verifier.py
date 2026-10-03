import types
import unittest
from unittest.mock import patch

from auto_2d_drawing.tolerance.feature_graph import FeatureGraphExtractor
from auto_2d_drawing.tolerance.projection_geometry_verifier import ProjectionGeometryVerifier


class _StructureStub:
    def __init__(self, association):
        self.association = association
        self.view_clusters = [
            types.SimpleNamespace(
                view_id="view_001",
                width=20.0,
                height=10.0,
                bbox=(0.0, 0.0, 20.0, 10.0),
                primitive_indexes=[],
            )
        ]

    def analyze_dimension(self, _dimension):
        return self.association


def _verifier(association):
    verifier = ProjectionGeometryVerifier.__new__(ProjectionGeometryVerifier)
    verifier.structure = _StructureStub(association)
    verifier.step_views = {
        "front": {
            "size": (40.0, 20.0),
            "bbox": (-20.0, -10.0, 20.0, 10.0),
            "visible": [
                {"type": "line", "p1": (-20.0, -5.0), "p2": (20.0, -5.0)},
                {"type": "line", "p1": (-20.0, 5.0), "p2": (20.0, 5.0)},
            ],
            "hidden": [],
        }
    }
    return verifier


class ProjectionGeometryVerifierTests(unittest.TestCase):
    def test_housing_with_cylinders_uses_general_graph_not_shaft_graph(self):
        fake_features = types.SimpleNamespace(
            W=30.0,
            H=20.0,
            D=10.0,
            shafts=[{"diameter": 8.0, "length": 4.0}],
            cylinders_raw=[
                {
                    "diameter": 8.0,
                    "length": 4.0,
                    "center": (12.0, 2.0, 1.0),
                    "axis_dir": (1.0, 0.0, 0.0),
                    "is_hole": False,
                },
                {
                    "diameter": 5.0,
                    "length": 3.0,
                    "center": (-4.0, 2.0, 1.0),
                    "axis_dir": (1.0, 0.0, 0.0),
                    "is_hole": True,
                },
            ],
            toruses=[],
            step_segments=[],
            cones=[],
            fillets=[],
        )
        with patch(
            "auto_2d_drawing.tolerance.feature_graph.FeatureExtractor",
            return_value=fake_features,
        ):
            graph = FeatureGraphExtractor().build_graph(object(), part_type="FAN_HOUSING")

        self.assertEqual(
            {node.feature_type for node in graph.nodes},
            {"shaft_segment", "hole"},
        )
        self.assertTrue(all(node.id.startswith("feat_cyl_") for node in graph.nodes))
        self.assertEqual(
            {tuple(node.source_info["center"]) for node in graph.nodes},
            {(12.0, 2.0, 1.0), (-4.0, 2.0, 1.0)},
        )
        self.assertEqual([node.center_axial for node in graph.nodes], [-4.0, 12.0])

    def test_attached_outer_diameter_with_step_projection_passes(self):
        association = {
            "association_status": "GEOMETRIC_ATTACHMENT",
            "association_confidence": 0.95,
            "primary_view_id": "view_001",
            "definition_point_links": [],
            "cross_view_evidence": {
                "matching_visible_pairs": [{"outer_silhouette": True}],
                "matching_hidden_pairs": [],
            },
        }
        dimension = types.SimpleNamespace(
            dimension_category="DIAMETER",
            nominal_value=10.0,
            points={"defpoint": (10.0, 5.0), "defpoint4": (10.0, 5.0)},
        )
        node = types.SimpleNamespace(
            feature_type="shaft_segment",
            source_info={"center": (0.0, 0.0, 0.0)},
        )

        result = _verifier(association).verify(dimension, node, "diameter")

        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], "GEOMETRY_VERIFIED")
        self.assertEqual(
            result["local_feature_evidence"]["signature"],
            "OUTER_VISIBLE_PARALLEL_PAIR",
        )

    def test_same_number_without_attachment_is_rejected(self):
        association = {
            "association_status": "NO_ATTACHMENT",
            "association_confidence": 0.0,
            "primary_view_id": None,
            "definition_point_links": [],
            "cross_view_evidence": {
                "matching_visible_pairs": [],
                "matching_hidden_pairs": [],
            },
        }
        dimension = types.SimpleNamespace(
            dimension_category="DIAMETER",
            nominal_value=10.0,
            points={"defpoint": (10.0, 5.0), "defpoint4": (10.0, 5.0)},
        )
        node = types.SimpleNamespace(
            feature_type="shaft_segment",
            source_info={"center": (0.0, 0.0, 0.0)},
        )

        result = _verifier(association).verify(dimension, node, "diameter")

        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["reliable_dimension_attachment"])

    def test_recovered_diameter_pair_can_verify_unique_groove_location(self):
        association = {
            "association_status": "RECOVERED_DIMENSION_GEOMETRY",
            "association_confidence": 0.90,
            "primary_view_id": "view_001",
            "definition_point_links": [],
            "recovered_dimension_geometry": {
                "kind": "DIAMETER_ENDPOINT_PAIR",
                "center": [10.0, 5.0],
                "view_id": "view_001",
            },
            "cross_view_evidence": {
                "matching_visible_pairs": [],
                "matching_hidden_pairs": [],
            },
        }
        dimension = types.SimpleNamespace(
            dimension_category="DIAMETER",
            nominal_value=10.0,
            points={
                "defpoint": (30.0, 30.0),
                "defpoint2": (5.0, 5.0),
                "defpoint3": (15.0, 5.0),
                "defpoint4": (0.0, 0.0),
            },
        )
        node = types.SimpleNamespace(
            feature_type="retaining_ring_groove",
            source_info={"center": (0.0, 0.0, 0.0)},
        )

        result = _verifier(association).verify(dimension, node, "groove_diameter")

        self.assertTrue(result["passed"])
        self.assertEqual(
            result["local_feature_evidence"]["signature"],
            "RECOVERED_DIAMETER_ENDPOINT_PAIR",
        )

    def test_exact_attached_circle_can_verify_step_classified_hole(self):
        association = {
            "association_status": "GEOMETRIC_ATTACHMENT",
            "association_confidence": 0.95,
            "primary_view_id": "view_001",
            "definition_point_links": [
                {
                    "nearest_geometry": [
                        {
                            "handle": "CIRCLE-1",
                            "entity_type": "CIRCLE",
                            "relation": "EXACT_ATTACHMENT",
                        }
                    ]
                },
                {
                    "nearest_geometry": [
                        {
                            "handle": "CIRCLE-1",
                            "entity_type": "CIRCLE",
                            "relation": "EXACT_ATTACHMENT",
                        }
                    ]
                },
            ],
            "common_attachment_handles": ["CIRCLE-1"],
            "attached_geometry_handles": [],
            "cross_view_evidence": {
                "matching_visible_pairs": [],
                "matching_hidden_pairs": [],
            },
        }
        dimension = types.SimpleNamespace(
            dimension_category="DIAMETER",
            nominal_value=10.0,
            points={"defpoint": (10.0, 5.0), "defpoint4": (10.0, 5.0)},
        )
        node = types.SimpleNamespace(
            feature_type="hole",
            source_info={"center": (0.0, 0.0, 0.0)},
        )

        verifier = _verifier(association)
        verifier.step_views["front"]["hidden"] = verifier.step_views["front"]["visible"]
        verifier.step_views["front"]["visible"] = []
        result = verifier.verify(dimension, node, "diameter")

        self.assertTrue(result["passed"])
        self.assertEqual(
            result["local_feature_evidence"]["signature"],
            "EXACT_ATTACHED_CIRCLE_WITH_3D_CLASSIFICATION",
        )

    def test_view_registration_uses_same_view_as_projection_signature(self):
        verifier = _verifier({})
        verifier.step_views["top"] = {
            "size": (20.0, 10.0),
            "bbox": (-10.0, -5.0, 10.0, 5.0),
            "visible": [],
            "hidden": [],
        }

        result = verifier._best_view_match(
            "view_001",
            allowed_step_views={"front"},
        )

        self.assertEqual(result["step_view"], "front")

    def test_linear_dimension_requires_two_exact_geometry_endpoints(self):
        association = {
            "association_status": "GEOMETRIC_ATTACHMENT",
            "association_confidence": 0.95,
            "primary_view_id": "view_001",
            "definition_point_links": [
                {
                    "nearest_geometry": [
                        {"handle": "A", "relation": "EXACT_ATTACHMENT"}
                    ]
                },
                {
                    "nearest_geometry": [
                        {"handle": "B", "relation": "EXACT_ATTACHMENT"}
                    ]
                },
            ],
            "cross_view_evidence": {
                "matching_visible_pairs": [],
                "matching_hidden_pairs": [],
            },
        }
        dimension = types.SimpleNamespace(
            dimension_category="LINEAR",
            nominal_value=10.0,
            points={"defpoint2": (5.0, 5.0), "defpoint3": (15.0, 5.0)},
        )
        node = types.SimpleNamespace(
            feature_type="shaft_segment",
            source_info={"center": (0.0, 0.0, 0.0)},
        )

        result = _verifier(association).verify(dimension, node, "length")

        self.assertTrue(result["passed"])
        self.assertEqual(
            result["local_feature_evidence"]["signature"],
            "TWO_EXACT_EXTENSION_ENDPOINTS",
        )

    def test_matching_number_at_wrong_projected_location_is_rejected(self):
        association = {
            "association_status": "GEOMETRIC_ATTACHMENT",
            "association_confidence": 0.95,
            "primary_view_id": "view_001",
            "definition_point_links": [],
            "attached_geometry_handles": [],
            "cross_view_evidence": {
                "matching_visible_pairs": [{"outer_silhouette": True}],
                "matching_hidden_pairs": [],
            },
        }
        dimension = types.SimpleNamespace(
            dimension_category="DIAMETER",
            nominal_value=10.0,
            points={"defpoint": (1.0, 1.0), "defpoint4": (1.0, 1.0)},
        )
        node = types.SimpleNamespace(
            feature_type="shaft_segment",
            source_info={"center": (0.0, 0.0, 0.0)},
        )

        result = _verifier(association).verify(dimension, node, "diameter")

        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["dimension_matches_projected_feature_location"])


if __name__ == "__main__":
    unittest.main()
