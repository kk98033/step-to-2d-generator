from experimental_lab.tolerance_extraction_audit.common.scripts.run_multi_projection_instance_index import (
    _drawing_view_key,
    _instance_key,
)


def registration(step_view, rotation, mirrored):
    return {
        "dxf_bbox": [10.0, 20.0, 90.0, 100.0],
        "step_view": step_view,
        "transform": {
            "rotation_degrees": rotation,
            "mirrored": mirrored,
        },
    }


def test_same_bbox_is_one_drawing_view_with_multiple_projection_hypotheses():
    front = registration("front", 0.0, False)
    back = registration("back", 180.0, True)
    assert _drawing_view_key(front["dxf_bbox"]) == _drawing_view_key(back["dxf_bbox"])
    assert _instance_key(front) != _instance_key(back)


def test_distinct_bbox_is_a_distinct_drawing_view_instance():
    left = [10.0, 20.0, 90.0, 100.0]
    right = [110.0, 20.0, 190.0, 100.0]
    assert _drawing_view_key(left) != _drawing_view_key(right)
