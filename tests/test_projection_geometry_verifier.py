import types
import unittest
from unittest.mock import patch

from auto_2d_drawing.tolerance.feature_graph import FeatureGraphExtractor
from auto_2d_drawing.tolerance.projection_geometry_verifier import ProjectionGeometryVerifier
from auto_2d_drawing.tolerance.projection_registration_v2 import ProjectionRegistrationEngine


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

    def test_locked_upstream_registration_prevents_view_reselection(self):
        verifier = _verifier({})
        verifier._locked_view_registration = {
            "dxf_view_id": "view_001",
            "step_view": "front",
            "score": 0.91,
            "transform": {
                "scale": 2.0,
                "rotation_degrees": 30.0,
                "translation": [4.0, 5.0],
                "mirrored": True,
            },
        }

        locked = verifier._best_view_match("view_001", allowed_step_views={"front"})
        rejected = verifier._best_view_match("view_001", allowed_step_views={"top"})

        self.assertTrue(locked["upstream_projection_locked"])
        self.assertEqual(locked["step_view"], "front")
        self.assertEqual(locked["orientation_candidates"], [])
        self.assertIsNone(rejected["step_view"])
        self.assertEqual(
            rejected["registration_status"],
            "LOCKED_UPSTREAM_VIEW_MISMATCH",
        )

    def test_locked_projection_accepts_geometry_backed_axis_compatibility(self):
        verifier = _verifier({})
        verifier._locked_view_registration = {
            "dxf_view_id": "view_001",
            "step_view": "top",
            "score": 0.93,
            "projection": {"direction": [0.0, 1.0, 0.0], "up": [1.0, 0.0, 0.0]},
            "transform": {
                "scale": 1.0,
                "rotation_degrees": 0.0,
                "translation": [0.0, 0.0],
                "mirrored": False,
            },
        }
        dimension = types.SimpleNamespace(dimension_category="DIAMETER")
        node = types.SimpleNamespace(source_info={"axis_dir": [0.0, 1.0, 0.0]})
        association = {
            "dimension_target_edges": [{
                "entity_type": "CIRCLE",
                "relation": "EXACT_ATTACHMENT",
                "radius": 16.0,
            }],
        }

        compatible = verifier._locked_projection_supports_feature(
            dimension, node, "diameter", association
        )
        result = verifier._best_view_match(
            "view_001",
            allowed_step_views={"front", "back", "left", "right"},
            locked_feature_compatible=compatible,
        )

        self.assertTrue(compatible)
        self.assertEqual(result["step_view"], "top")
        self.assertEqual(
            result["registration_status"],
            "LOCKED_UPSTREAM_VECTOR_COMPATIBLE",
        )

    def test_secondary_dimension_view_can_search_without_replacing_primary_lock(self):
        verifier = _verifier({})
        verifier._locked_view_registration = {
            "dxf_view_id": "view_001",
            "step_view": "front",
            "score": 0.91,
        }
        verifier._allow_secondary_view_search = True
        verifier.structure.view_clusters.append(types.SimpleNamespace(
            view_id="view_002",
            width=20.0,
            height=10.0,
            bbox=(0.0, 0.0, 20.0, 10.0),
            primitive_indexes=[],
        ))

        result = verifier._best_view_match(
            "view_002",
            allowed_step_views={"front"},
        )

        self.assertEqual(result["dxf_view_id"], "view_002")
        self.assertEqual(result["step_view"], "front")

    def test_dimension_target_edge_resolves_mirrored_orientation(self):
        association = {
            "primary_view_id": "view_001",
            "dimension_target_edges": [{
                "handle": "EDGE-LEFT",
                "points": [[2.0, 0.0], [2.0, 10.0]],
                "center": None,
                "radius": 0.0,
            }],
        }
        dimension = types.SimpleNamespace(
            dimension_category="DIAMETER",
            points={"defpoint": (2.0, 5.0), "defpoint4": (2.0, 5.0)},
        )
        node = types.SimpleNamespace(
            id="shaft_right",
            feature_type="shaft_segment",
            nominal={"diameter": 2.0, "length": 2.0},
            source_info={
                "type": "cylinder",
                "center": (0.0, 8.0, 0.0),
                "axis_dir": (0.0, 0.0, 1.0),
                "radius": 1.0,
                "length": 2.0,
            },
        )
        base = {
            "score": 0.9,
            "contour_score": 0.9,
            "dxf_view_id": "view_001",
            "step_view": "front",
            "dxf_bbox": [0.0, 0.0, 20.0, 10.0],
            "registration_method": ProjectionRegistrationEngine.METHOD,
            "normalized_chamfer": 0.01,
            "inlier_ratio": 0.9,
            "ranked_hausdorff": 0.05,
        }
        view_match = {
            **base,
            "orientation_candidates": [
                {
                    **base,
                    "mirrored": False,
                    "rotation_degrees": 0.0,
                    "transform": {
                        "scale": 1.0,
                        "rotation_degrees": 0.0,
                        "translation": [10.0, 5.0],
                        "mirrored": False,
                    },
                },
                {
                    **base,
                    "mirrored": True,
                    "rotation_degrees": 0.0,
                    "transform": {
                        "scale": 1.0,
                        "rotation_degrees": 0.0,
                        "translation": [10.0, 5.0],
                        "mirrored": True,
                    },
                },
            ],
        }

        selected = _verifier(association)._select_orientation_for_dimension_feature(
            dimension,
            node,
            association,
            view_match,
        )

        self.assertTrue(selected["mirrored"])
        self.assertTrue(
            selected["orientation_disambiguation"]["selected_by_annotation_geometry"]
        )

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

    def test_local_circle_topology_does_not_override_bad_global_registration(self):
        association = {
            "association_status": "GEOMETRIC_ATTACHMENT",
            "association_confidence": 0.96,
            "primary_view_id": "view_001",
        }
        verifier = _verifier(association)
        verifier.topology_mapper = types.SimpleNamespace(
            map_dimension=lambda *args, **kwargs: {
                "passed": True,
                "score": 0.98,
                "mode": "LOCAL_CIRCULAR_PROFILE",
                "status": "TOPOLOGY_FACE_MAPPED",
            }
        )
        verifier._local_feature_evidence = lambda *args, **kwargs: {"passed": True}
        verifier._step_projection_evidence = lambda *args, **kwargs: {
            "passed": True,
            "matching_views": [{"view": "front"}],
        }
        verifier._best_view_match = lambda *args, **kwargs: {
            "score": 0.82,
            "contour_score": 0.82,
            "dxf_view_id": "view_001",
            "dxf_bbox": [0.0, 0.0, 20.0, 10.0],
            "step_view": "front",
            "registration_method": ProjectionRegistrationEngine.METHOD,
            "normalized_chamfer": 0.018,
            "inlier_ratio": 0.50,
            "source_coverage": 0.68,
            "target_coverage": 0.31,
            "ranked_hausdorff": 0.07,
            "extent_score": 0.80,
            "span_ratio": 0.95,
            "bbox_edge_error": 0.02,
            "transform": {
                "scale": 1.0,
                "rotation_degrees": 0.0,
                "translation": [0.0, 0.0],
                "mirrored": False,
            },
        }
        verifier._node_projection_localization = lambda *args, **kwargs: {
            "passed": False,
            "score": 0.15,
            "status": "PROJECTED_NODE_MISMATCH",
        }
        dimension = types.SimpleNamespace(
            dimension_category="DIAMETER",
            nominal_value=32.0,
            points={},
        )
        node = types.SimpleNamespace(
            id="shaft_01",
            feature_type="shaft_segment",
            source_info={"type": "cylinder", "center": [0.0, 0.0, 0.0]},
        )

        result = verifier.verify(dimension, node, "diameter")

        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["drawing_view_matches_step_projection"])
        self.assertFalse(result["checks"]["dimension_matches_projected_feature_location"])
        self.assertEqual(result["feature_localization"]["status"], "PROJECTED_NODE_MISMATCH")

    def test_v3_registration_quality_gate_rejects_weak_overlap(self):
        evidence = {
            "registration_method": ProjectionRegistrationEngine.METHOD,
            "contour_score": 0.66,
            "inlier_ratio": 0.74,
            "normalized_chamfer": 0.028,
            "ranked_hausdorff": 0.09,
        }
        self.assertFalse(ProjectionGeometryVerifier._registration_is_confident(evidence))

    def test_v3_registration_quality_gate_accepts_balanced_evidence(self):
        evidence = {
            "registration_method": ProjectionRegistrationEngine.METHOD,
            "contour_score": 0.82,
            "inlier_ratio": 0.86,
            "normalized_chamfer": 0.018,
            "ranked_hausdorff": 0.07,
            "source_coverage": 0.82,
            "target_coverage": 0.84,
            "extent_score": 0.90,
            "span_ratio": 0.95,
            "bbox_edge_error": 0.02,
        }
        self.assertTrue(ProjectionGeometryVerifier._registration_is_confident(evidence))

    def test_secondary_v2_regions_do_not_replace_existing_atomic_feature_view(self):
        verifier = ProjectionGeometryVerifier.__new__(ProjectionGeometryVerifier)
        verifier._locked_view_registration = {"dxf_view_id": "locked_view"}
        verifier._secondary_dxf_regions = {
            "local_view": {
                "view_id": "local_view",
                "bbox": [0.0, 0.0, 20.0, 20.0],
                "points": [[0.0, 0.0], [20.0, 20.0]],
            }
        }
        calls = []

        def best(view_id, **_kwargs):
            calls.append(view_id)
            return {"score": 0.72, "dxf_view_id": view_id, "step_view": "front"}

        verifier._best_view_match = best
        dimension = types.SimpleNamespace(
            nominal_value=15.0,
            points={"defpoint2": [1.0, 1.0], "defpoint3": [16.0, 1.0]},
        )

        result = verifier._best_dimension_view_match(
            dimension,
            "existing_view",
            allowed_step_views={"front"},
            require_locked_feature_compatibility=False,
        )

        self.assertEqual(result["dxf_view_id"], "existing_view")
        self.assertEqual(calls, ["existing_view"])


if __name__ == "__main__":
    unittest.main()
