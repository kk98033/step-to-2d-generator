import math

from experimental_lab.tolerance_extraction_audit.common.scripts.run_blade_curve_tolerance_experiment import (
    _geometry_constrained_envelope_phase,
)


def rectangle_view(width=10.0, height=2.0):
    x = width / 2.0
    y = height / 2.0
    return {
        "bbox": (-x, -y, x, y),
        "visible": [
            {"p1": (-x, -y), "p2": (x, -y)},
            {"p1": (x, -y), "p2": (x, y)},
            {"p1": (x, y), "p2": (-x, y)},
            {"p1": (-x, y), "p2": (-x, -y)},
        ],
        "hidden": [],
    }


def payload(score, rotation):
    return {
        "score": score,
        "extent_score": 0.9,
        "transform": {
            "scale": 1.0,
            "rotation_degrees": rotation,
            "translation": [0.0, 0.0],
            "mirrored": False,
        },
    }


def test_span_is_measured_after_projection_phase_along_dimension_axis():
    view = rectangle_view()
    registration = payload(0.9, 45.0)
    expected = 10.0 * math.cos(math.pi / 4.0) + 2.0 * math.sin(math.pi / 4.0)
    result = _geometry_constrained_envelope_phase(
        registration,
        view,
        (0.0, 0.0),
        [(-5.0, 0.0), (5.0, 0.0)],
        expected,
        expected - 0.01,
        expected + 0.01,
    )
    assert abs(result["physical_span_mm"] - expected) < 1e-9
    assert result["inside_tolerance_interval"]


def test_dimension_cannot_override_a_non_tied_geometry_phase():
    view = rectangle_view()
    registration = payload(0.9, 0.0)
    registration["alternatives"] = [payload(0.7, 90.0)]
    result = _geometry_constrained_envelope_phase(
        registration,
        view,
        (0.0, 0.0),
        [(-5.0, 0.0), (5.0, 0.0)],
        2.0,
        1.9,
        2.1,
    )
    assert result["geometry_score"] == 0.9
    assert abs(result["physical_span_mm"] - 10.0) < 1e-9
    assert not result["inside_tolerance_interval"]
