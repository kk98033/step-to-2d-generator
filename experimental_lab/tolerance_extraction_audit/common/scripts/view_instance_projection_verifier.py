"""Experimental dimension-local drawing-view recovery.

The production verifier intentionally trusts the upstream drawing view.  That
is too coarse when one legacy DXF cluster contains several orthographic view
instances.  This subclass keeps the upstream STEP pose but re-registers it to
multiscale DXF regions containing the dimension attachment.  No component or
filename rules are used.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional

from auto_2d_drawing.tolerance.projection_geometry_verifier import (
    ProjectionGeometryVerifier,
)


class ViewInstanceProjectionGeometryVerifier(ProjectionGeometryVerifier):
    """Recover an equivalent local drawing-view instance for a dimension."""

    METHOD = "DXF_LOCAL_VIEW_INSTANCE_ADAPTIVE_STEP_HLR_TOPOLOGY_V1"

    def _select_orientation_for_dimension_feature(self, dimension, node, association, view_match):
        recovery = view_match.get("equivalent_view_instance_recovery")
        selected = super()._select_orientation_for_dimension_feature(
            dimension, node, association, view_match
        )
        if recovery is not None:
            recovery = dict(recovery)
            recovery["selected_dxf_view_id"] = selected.get("dxf_view_id")
            selected["equivalent_view_instance_recovery"] = recovery
        return selected

    def _best_dimension_view_match(
        self,
        dimension,
        dxf_view_id: Optional[str],
        allowed_step_views: Optional[Iterable[str]] = None,
        locked_feature_compatible: bool = False,
        require_locked_feature_compatibility: bool = False,
    ) -> Dict[str, Any]:
        primary = super()._best_dimension_view_match(
            dimension,
            dxf_view_id,
            allowed_step_views=allowed_step_views,
            locked_feature_compatible=locked_feature_compatible,
            require_locked_feature_compatibility=require_locked_feature_compatibility,
        )
        if not getattr(self, "_allow_secondary_view_search", False):
            return primary

        association = self.structure.analyze_dimension(dimension)
        anchor = self._dimension_anchor(dimension, association)
        if anchor is None:
            return primary
        nominal = abs(float(getattr(dimension, "nominal_value", 0.0) or 0.0))
        margin = max(0.35, nominal * 0.025)
        region_ids: List[str] = []
        for region_id, region in getattr(self, "_secondary_dxf_regions", {}).items():
            bbox = tuple(float(value) for value in region.get("bbox") or ())
            if len(bbox) != 4:
                continue
            if not (
                bbox[0] - margin <= anchor[0] <= bbox[2] + margin
                and bbox[1] - margin <= anchor[1] <= bbox[3] + margin
            ):
                continue
            width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            # A ring fragment can contain a diameter anchor but is not a full
            # projected part view.  Demand context larger than the measured
            # feature while leaving the upper size unconstrained for general
            # mechanical drawings.
            if max(width, height) < max(1.0, nominal * 1.15):
                continue
            region_ids.append(str(region_id))

        results = []
        for region_id in dict.fromkeys(region_ids):
            result = self._best_view_match(
                region_id,
                allowed_step_views=allowed_step_views,
                locked_feature_compatible=locked_feature_compatible,
                require_locked_feature_compatibility=False,
            )
            if result.get("transform") and result.get("step_view"):
                results.append(dict(result))
        if not results:
            return primary

        def rank(item: Dict[str, Any]):
            coverage = min(
                float(item.get("source_coverage", 0.0) or 0.0),
                float(item.get("target_coverage", 0.0) or 0.0),
            )
            contour = float(item.get("contour_score", item.get("score", 0.0)) or 0.0)
            extent = float(item.get("extent_score", 0.0) or 0.0)
            return 0.62 * contour + 0.20 * coverage + 0.18 * extent

        local = max(results, key=rank)
        primary_rank = rank(primary)
        local_rank = rank(local)
        primary_bbox = tuple(float(value) for value in primary.get("dxf_bbox") or ())
        anchor_inside_primary = (
            len(primary_bbox) == 4
            and primary_bbox[0] - margin <= anchor[0] <= primary_bbox[2] + margin
            and primary_bbox[1] - margin <= anchor[1] <= primary_bbox[3] + margin
        )
        # Switching view instances needs a material gain; this prevents a
        # small annotation fragment from replacing an already-good upstream
        # registration.  A locked bbox that does not contain the dimension
        # anchor is a different instance even when both records reuse the same
        # legacy view_id; in that case absolute score improvement is neither
        # expected nor required.
        minimum_rank = 0.68 if not anchor_inside_primary else max(0.68, primary_rank + 0.025)
        if local_rank < minimum_rank:
            return primary
        local["equivalent_view_instance_recovery"] = {
            "method": "DIMENSION_ANCHOR_MULTISCALE_LOCAL_VIEW_V1",
            "source_dxf_view_id": dxf_view_id,
            "selected_dxf_view_id": local.get("dxf_view_id"),
            "candidate_count": len(results),
            "primary_rank": round(primary_rank, 6),
            "selected_rank": round(local_rank, 6),
            "anchor_inside_locked_bbox": anchor_inside_primary,
            "absolute_sheet_position_ignored": True,
        }
        alternatives = []
        for result in results:
            alternatives.extend(result.get("orientation_candidates") or [])
        alternatives.sort(key=rank, reverse=True)
        local["orientation_candidates"] = alternatives[:48]
        return local
