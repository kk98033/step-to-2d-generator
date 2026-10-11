import math

from experimental_lab.tolerance_extraction_audit.common.scripts.assembly_feature_resolver import (
    _metric_components,
    classify_relation_kind,
    view_axis_2d,
)


def test_top_view_projects_world_z_to_negative_horizontal_axis():
    axis = view_axis_2d("top", 2)
    assert math.isclose(axis[0], -1.0, abs_tol=1e-9)
    assert math.isclose(axis[1], 0.0, abs_tol=1e-9)


def test_metric_components_separate_axial_and_transverse_measurements():
    axis = (-1.0, 0.0)
    axial, transverse = _metric_components((23.1, 0.0), (5.8, 2.0), axis)
    assert math.isclose(axial, 17.3, abs_tol=1e-9)
    assert math.isclose(transverse, 2.0, abs_tol=1e-9)


def test_direct_distinct_component_owners_form_assembly_relation():
    assert classify_relation_kind("shaft", "blade", "DIRECT", "DIRECT") == "ASSEMBLY_RELATION"


def test_inferred_owner_never_auto_verifies_assembly_relation():
    assert classify_relation_kind("shaft", "blade", "DIRECT", "INFERRED") == "ASSEMBLY_RELATION_CANDIDATE"


def test_direct_same_component_owners_form_component_feature():
    assert classify_relation_kind("hub", "hub", "DIRECT", "DIRECT") == "COMPONENT_FEATURE"
