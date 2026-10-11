from types import SimpleNamespace

from experimental_lab.tolerance_extraction_audit.common.scripts.view_instance_projection_verifier import (
    ViewInstanceProjectionGeometryVerifier,
)


def registration(view_id, bbox, contour, coverage, extent):
    return {
        "score": contour,
        "contour_score": contour,
        "source_coverage": coverage,
        "target_coverage": coverage,
        "extent_score": extent,
        "dxf_view_id": view_id,
        "step_view": "front",
        "dxf_bbox": list(bbox),
        "transform": {
            "scale": 1.0,
            "rotation_degrees": 0.0,
            "translation": [0.0, 0.0],
            "mirrored": False,
        },
        "orientation_candidates": [],
    }


class Harness(ViewInstanceProjectionGeometryVerifier):
    def __init__(self, anchor, matches, regions):
        self.anchor = anchor
        self.matches = matches
        self._allow_secondary_view_search = True
        self._secondary_dxf_regions = regions
        self.structure = SimpleNamespace(analyze_dimension=lambda _dimension: {})

    def _best_view_match(self, dxf_view_id, **_kwargs):
        return dict(self.matches[dxf_view_id])

    def _dimension_anchor(self, _dimension, _association):
        return self.anchor


def test_recovers_local_instance_when_legacy_id_points_to_other_bbox():
    matches = {
        "legacy": registration("legacy", (100, 0, 180, 80), 0.95, 1.0, 0.98),
        "local": registration("local", (0, 0, 80, 80), 0.84, 0.90, 0.76),
    }
    verifier = Harness(
        anchor=(40, 40),
        matches=matches,
        regions={"local": {"bbox": [0, 0, 80, 80]}},
    )
    result = verifier._best_dimension_view_match(SimpleNamespace(nominal_value=27), "legacy")
    assert result["dxf_view_id"] == "local"
    recovery = result["equivalent_view_instance_recovery"]
    assert recovery["anchor_inside_locked_bbox"] is False
    assert recovery["absolute_sheet_position_ignored"] is True


def test_keeps_valid_locked_instance_without_material_local_improvement():
    matches = {
        "legacy": registration("legacy", (0, 0, 80, 80), 0.95, 1.0, 0.98),
        "local": registration("local", (0, 0, 80, 80), 0.84, 0.90, 0.76),
    }
    verifier = Harness(
        anchor=(40, 40),
        matches=matches,
        regions={"local": {"bbox": [0, 0, 80, 80]}},
    )
    result = verifier._best_dimension_view_match(SimpleNamespace(nominal_value=27), "legacy")
    assert result["dxf_view_id"] == "legacy"
    assert "equivalent_view_instance_recovery" not in result


def test_rejects_feature_fragment_as_a_view_instance():
    matches = {
        "legacy": registration("legacy", (100, 0, 180, 80), 0.95, 1.0, 0.98),
        "ring_only": registration("ring_only", (25, 25, 55, 55), 0.99, 1.0, 1.0),
    }
    verifier = Harness(
        anchor=(40, 40),
        matches=matches,
        regions={"ring_only": {"bbox": [25, 25, 55, 55]}},
    )
    result = verifier._best_dimension_view_match(SimpleNamespace(nominal_value=27), "legacy")
    assert result["dxf_view_id"] == "legacy"
