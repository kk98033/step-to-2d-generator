"""Pure selection helpers for historical DXF view comparison experiments."""

from __future__ import annotations

from typing import Any, Sequence


def bbox_iou(left: Sequence[float], right: Sequence[float]) -> float:
    ix = max(0.0, min(float(left[2]), float(right[2])) - max(float(left[0]), float(right[0])))
    iy = max(0.0, min(float(left[3]), float(right[3])) - max(float(left[1]), float(right[1])))
    intersection = ix * iy
    area_left = max(float(left[2]) - float(left[0]), 0.0) * max(float(left[3]) - float(left[1]), 0.0)
    area_right = max(float(right[2]) - float(right[0]), 0.0) * max(float(right[3]) - float(right[1]), 0.0)
    return intersection / max(area_left + area_right - intersection, 1e-9)


def select_distinct_view_matches(
    rows: Sequence[dict[str, Any]],
    *,
    maximum_views: int = 3,
    minimum_score: float = 0.46,
    relative_to_best: float = 0.65,
) -> list[dict[str, Any]]:
    """Greedily keep different STEP axes and spatially distinct DXF regions.

    The returned count is data-driven from 1..maximum_views.  A single-view
    drawing therefore remains a single-view drawing instead of being forced
    into a three-view template.
    """
    eligible = [
        item for item in rows
        if float(item.get("scale", 1.0) or 0.0) >= 0.55
        and float(item.get("scale", 1.0) or 0.0) <= 1.80
        and float(item.get("source_coverage", 1.0) or 0.0) >= 0.42
        and float(item.get("target_coverage", 1.0) or 0.0) >= 0.62
        and float(item.get("aspect_similarity", 1.0) or 0.0) >= 0.48
        and float(item.get("sheet_area_fraction", 0.0) or 0.0) <= 0.72
        and int(item.get("primitive_count", 4) or 0) >= 4
    ]
    ranked = sorted(eligible, key=lambda item: float(item.get("comparison_score") or 0.0), reverse=True)
    if not ranked:
        return []
    threshold = max(float(minimum_score), float(ranked[0]["comparison_score"]) * float(relative_to_best))
    selected: list[dict[str, Any]] = []
    used_step_views: set[str] = set()
    for row in ranked:
        if len(selected) >= maximum_views:
            break
        score = float(row.get("comparison_score") or 0.0)
        if selected and score < threshold:
            continue
        step_view = str(row.get("step_view_group") or row["step_view"])
        if step_view in used_step_views:
            continue
        box = row["candidate_bbox"]
        if any(bbox_iou(box, item["candidate_bbox"]) >= 0.35 for item in selected):
            continue
        selected.append(dict(row))
        used_step_views.add(step_view)
    return selected


def expanded_crop_bounds(
    candidate_bbox: Sequence[float], sheet_bbox: Sequence[float], margin_fraction: float = 0.18,
) -> list[float]:
    """Expand a geometry region to retain its nearby native dimensions."""
    x0, y0, x1, y1 = (float(value) for value in candidate_bbox)
    sx0, sy0, sx1, sy1 = (float(value) for value in sheet_bbox)
    margin_x = max(x1 - x0, 1e-9) * float(margin_fraction)
    margin_y = max(y1 - y0, 1e-9) * float(margin_fraction)
    return [max(sx0, x0 - margin_x), max(sy0, y0 - margin_y), min(sx1, x1 + margin_x), min(sy1, y1 + margin_y)]
