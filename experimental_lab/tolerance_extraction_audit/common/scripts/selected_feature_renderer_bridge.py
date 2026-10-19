"""Bridge explicit engineer selections to feature-specific render tasks."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

try:
    from .rectangular_hole_renderer import build_rectangular_hole_render_tasks
except ImportError:
    from rectangular_hole_renderer import build_rectangular_hole_render_tasks


METHOD = "SELECTED_FEATURE_RENDERER_BRIDGE_V1"


def build_relational_feature_candidates(
    fan_result: Mapping[str, Any],
    housing_result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidates = []
    center = [float(value) for value in (fan_result.get("center") or (0.0, 0.0))[:2]]
    view = str(fan_result.get("view") or "front")
    for task in fan_result.get("angular_render_tasks") or []:
        candidates.append({
            "feature_id": f"hub_radial_relation.{task['id']}",
            "rule_id": f"hub_radial_relation.{task['id']}",
            "name": f"輪轂徑向關係 {float(task['angle']):.2f}°",
            "semantic": "hub_radial_partition.included_angle",
            "dim_type": "ANGULAR",
            "renderer_type": "ANGULAR_RELATION_RENDERER",
            "verification_status": str(fan_result.get("verification_status") or "REVIEW_CANDIDATE"),
            "confidence": float(fan_result.get("confidence") or 0.0),
            "preferred_view": view,
            "target_views": [view],
            "geometry_payload": {
                "center": center,
                "start_angle": float(task["start_angle"]),
                "end_angle": float(task["end_angle"]),
                "angle": float(task["angle"]),
                "radius": float(task["radius"]),
                "source_relation": copy.deepcopy(task.get("source_relation") or {}),
            },
            "enabled": False,
        })
    if housing_result.get("resolved"):
        candidates.append({
            "feature_id": "mounting_hole_pattern.rectangular_001",
            "rule_id": "mounting_hole_pattern.rectangular_001",
            "name": "矩形四孔：孔徑與水平／垂直中心距",
            "semantic": "mounting_hole_pattern.rectangular",
            "dim_type": "RECTANGULAR_HOLE_PATTERN",
            "renderer_type": "RECTANGULAR_HOLE_PATTERN_RENDERER",
            "verification_status": str(housing_result.get("verification_status") or "REVIEW_CANDIDATE"),
            "confidence": float(housing_result.get("confidence") or 0.0),
            "preferred_view": str(housing_result.get("view") or "front"),
            "target_views": [str(housing_result.get("view") or "front")],
            "geometry_payload": {
                "centers": copy.deepcopy(housing_result.get("centers") or []),
                "diameter": float(housing_result["diameter"]),
                "horizontal_center_distance": float(housing_result["horizontal_center_distance"]),
                "vertical_center_distance": float(housing_result["vertical_center_distance"]),
                "topology_refs": list(housing_result.get("topology_refs") or []),
            },
            "enabled": False,
        })
    return candidates


def bridge_selected_features(
    candidates: Sequence[dict[str, Any]],
    selections: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {str(item["feature_id"]): copy.deepcopy(item) for item in candidates}
    selected_records = []
    render_tasks = []
    rejected = []
    for selection in selections:
        feature_id = str(selection.get("feature_id") or selection.get("rule_id") or "")
        if not selection.get("enabled", True):
            rejected.append({"feature_id": feature_id, "reason": "DISABLED_BY_ENGINEER"})
            continue
        candidate = by_id.get(feature_id)
        if not candidate:
            rejected.append({"feature_id": feature_id, "reason": "UNKNOWN_FEATURE_ID"})
            continue
        candidate["enabled"] = True
        requested_views = selection.get("views") or selection.get("target_views")
        if requested_views:
            candidate["target_views"] = [str(value) for value in requested_views]
        renderer = candidate["renderer_type"]
        if renderer == "ANGULAR_RELATION_RENDERER":
            payload = candidate["geometry_payload"]
            tasks = [{
                "task_id": feature_id,
                "feature_id": feature_id,
                "renderer": renderer,
                "semantic": candidate["semantic"],
                "view": candidate["target_views"][0],
                "center": copy.deepcopy(payload["center"]),
                "start_angle": float(payload["start_angle"]),
                "end_angle": float(payload["end_angle"]),
                "angle": float(payload["angle"]),
                "radius": float(payload["radius"]),
                "label": f"{float(payload['angle']):.2f}°",
                "verification_status": candidate["verification_status"],
            }]
        elif renderer == "RECTANGULAR_HOLE_PATTERN_RENDERER":
            tasks = build_rectangular_hole_render_tasks(candidate["geometry_payload"])
            for task in tasks:
                task["feature_id"] = feature_id
                task["view"] = candidate["target_views"][0]
                task["verification_status"] = candidate["verification_status"]
        else:
            rejected.append({"feature_id": feature_id, "reason": "UNSUPPORTED_RENDERER"})
            continue
        selected_records.append(candidate)
        render_tasks.extend(tasks)
    return {
        "schema_version": 1,
        "method": METHOD,
        "selected_feature_count": len(selected_records),
        "render_task_count": len(render_tasks),
        "selected_features": selected_records,
        "render_tasks": render_tasks,
        "rejected_selections": rejected,
    }
