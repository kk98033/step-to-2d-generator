"""Extract reusable drawing-intent cases from native DXF dimensions.

This is an offline experiment.  It never writes the production case base and
does not claim a 3D feature link.  It converts absolute DXF paper coordinates
into view-relative evidence that can later be joined to the locked STEP/DXF
projection and canonical feature records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import ezdxf


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_2d_drawing.tolerance.dxf_structure_2d import (  # noqa: E402
    DxfStructure2DAnalyzer,
    ViewCluster2D,
)
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import (  # noqa: E402
    DxfToleranceExtractor,
    ExtractedDimension,
)


SCHEMA_VERSION = "drawing_rule_case_v1"
METHOD = "NATIVE_DXF_VIEW_RELATIVE_DRAWING_INTENT_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _normalise_point(point: tuple[float, float], bbox: Sequence[float]) -> list[float]:
    width = max(float(bbox[2]) - float(bbox[0]), 1e-9)
    height = max(float(bbox[3]) - float(bbox[1]), 1e-9)
    return [
        round((point[0] - float(bbox[0])) / width, 6),
        round((point[1] - float(bbox[1])) / height, 6),
    ]


def _dimension_point_keys(dimension: ExtractedDimension) -> tuple[str, ...]:
    category = str(dimension.dimension_category or dimension.dim_type or "").upper()
    if category in {"DIAMETER", "RADIUS", "RADIAL"}:
        return "defpoint", "defpoint4"
    if category in {"ANGULAR", "ANGLE"}:
        return "defpoint2", "defpoint3", "defpoint4"
    return "defpoint2", "defpoint3"


def _measurement_axis(dimension: ExtractedDimension) -> str:
    points = dimension.points or {}
    candidates = [_point(points.get(key)) for key in _dimension_point_keys(dimension)]
    valid = [point for point in candidates if point is not None]
    if len(valid) < 2:
        return "UNKNOWN"
    dx = valid[-1][0] - valid[0][0]
    dy = valid[-1][1] - valid[0][1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return "UNKNOWN"
    if abs(dy) / length <= 0.12:
        return "x"
    if abs(dx) / length <= 0.12:
        return "y"
    return "oblique"


def _text_anchor(dimension: ExtractedDimension) -> tuple[float, float] | None:
    points = dimension.points or {}
    for key in ("text_mid", "text_pos", "defpoint"):
        point = _point(points.get(key))
        if point is not None and (point != (0.0, 0.0) or key == "defpoint"):
            return point
    return None


def infer_side_and_offset(
    text_point: tuple[float, float] | None,
    bbox: Sequence[float],
) -> tuple[str, float, float]:
    """Return view-relative side, normalized offset and placement confidence."""
    if text_point is None:
        return "UNKNOWN", 0.0, 0.0
    xmin, ymin, xmax, ymax = (float(value) for value in bbox)
    width = max(xmax - xmin, 1e-9)
    height = max(ymax - ymin, 1e-9)
    x, y = text_point
    outside = {
        "LEFT": max(0.0, xmin - x) / width,
        "RIGHT": max(0.0, x - xmax) / width,
        "BOTTOM": max(0.0, ymin - y) / height,
        "TOP": max(0.0, y - ymax) / height,
    }
    active = [(distance, side) for side, distance in outside.items() if distance > 0.0]
    if active:
        distance, side = max(active)
        return side, round(distance, 6), 1.0

    # Text inside the view is retained for review rather than being forced to
    # a CandidateRule side.  This commonly occurs for diameter leaders and
    # section/detail annotations.
    center_x = (xmin + xmax) / 2.0
    center_y = (ymin + ymax) / 2.0
    relative_x = abs(x - center_x) / width
    relative_y = abs(y - center_y) / height
    if max(relative_x, relative_y) <= 0.08:
        return "IN_VIEW", 0.0, 0.55
    if relative_x >= relative_y:
        return ("RIGHT" if x >= center_x else "LEFT"), 0.0, 0.65
    return ("TOP" if y >= center_y else "BOTTOM"), 0.0, 0.65


def _rule_dim_type(dimension: ExtractedDimension) -> str:
    raw = str(dimension.dim_type or dimension.dimension_category or "LINEAR").upper()
    return {
        "ANGULAR": "ANGLE",
        "RADIUS": "RADIAL",
    }.get(raw, raw)


def _cluster_by_id(analyzer: DxfStructure2DAnalyzer) -> dict[str, ViewCluster2D]:
    return {cluster.view_id: cluster for cluster in analyzer.view_clusters}


def _normalised_points(dimension: ExtractedDimension, bbox: Sequence[float]) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for key, value in (dimension.points or {}).items():
        point = _point(value)
        if point is None:
            continue
        result[key] = _normalise_point(point, bbox)
    return result


def _entity_style(entity: Any) -> dict[str, Any]:
    if entity is None:
        return {}
    dxf = entity.dxf
    style: dict[str, Any] = {
        "dimstyle": str(getattr(dxf, "dimstyle", "") or ""),
        "layer": str(getattr(dxf, "layer", "0") or "0"),
    }
    for name in ("color", "lineweight", "text_rotation", "horizontal_direction"):
        value = getattr(dxf, name, None)
        if value is not None:
            try:
                style[name] = round(float(value), 6)
            except (TypeError, ValueError):
                style[name] = str(value)
    return style


def _assign_ranks(cases: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        placement = case["placement"]
        grouped[(str(case.get("dxf_view_id")), str(placement["side"]))].append(case)
    for items in grouped.values():
        items.sort(key=lambda item: (float(item["placement"]["offset_normalized"]), item["dimension_handle"]))
        previous: float | None = None
        rank = 0
        for item in items:
            offset = float(item["placement"]["offset_normalized"])
            if previous is None or abs(offset - previous) > 0.015:
                rank += 1
                previous = offset
            item["placement"]["rank"] = rank


def extract_drawing_rule_cases(dxf_path: str | Path) -> dict[str, Any]:
    source = Path(dxf_path).resolve()
    document = ezdxf.readfile(source)
    modelspace = document.modelspace()
    dimensions = DxfToleranceExtractor().extract_from_modelspace(
        modelspace,
        source.name,
        include_rejected=True,
        native_dimensions_only=True,
    )
    analyzer = DxfStructure2DAnalyzer(modelspace)
    clusters = _cluster_by_id(analyzer)
    entity_index = {
        str(getattr(entity.dxf, "handle", "") or ""): entity
        for entity in modelspace
        if entity.dxftype() in {"DIMENSION", "ARC_DIMENSION", "RADIAL_DIMENSION", "DIAMETER_DIMENSION"}
    }
    cases: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for dimension in dimensions:
        association = analyzer.analyze_dimension(dimension)
        view_id = association.get("primary_view_id")
        cluster = clusters.get(str(view_id)) if view_id else None
        if cluster is None or float(dimension.nominal_value or 0.0) <= 0.0:
            rejected.append({
                "dimension_handle": dimension.entity_handle,
                "reason": "NO_UNIQUE_VIEW" if cluster is None else "NON_POSITIVE_NOMINAL",
                "candidate_view_ids": association.get("view_ids") or [],
                "association_status": association.get("association_status"),
            })
            continue
        text_point = _text_anchor(dimension)
        side, offset, placement_confidence = infer_side_and_offset(text_point, cluster.bbox)
        attachment_confidence = float(association.get("association_confidence") or 0.0)
        evidence_status = (
            "EXTRACTED_RELIABLE_2D"
            if attachment_confidence >= 0.90 and placement_confidence >= 0.65
            else "REVIEW_CANDIDATE"
        )
        cases.append({
            "schema_version": SCHEMA_VERSION,
            "method": METHOD,
            "source_drawing": source.name,
            "dimension_handle": dimension.entity_handle,
            "canonical_feature_id": None,
            "feature_link_status": "PENDING_LOCKED_STEP_PROJECTION",
            "dxf_view_id": cluster.view_id,
            "view_bbox": [round(float(value), 6) for value in cluster.bbox],
            "dimension": {
                "dim_type": _rule_dim_type(dimension),
                "dimension_category": dimension.dimension_category,
                "nominal_value": float(dimension.nominal_value),
                "prefix": dimension.prefix,
                "raw_text": dimension.raw_text,
                "tolerance_config": dimension.tolerance_config,
                "measurement_axis": _measurement_axis(dimension),
            },
            "placement": {
                "side": side,
                "rank": None,
                "offset_normalized": offset,
                "text_anchor_normalized": _normalise_point(text_point, cluster.bbox) if text_point else None,
                "definition_points_normalized": _normalised_points(dimension, cluster.bbox),
                "confidence": round(placement_confidence, 3),
            },
            "style": _entity_style(entity_index.get(dimension.entity_handle)),
            "attachment_evidence": {
                "status": association.get("association_status"),
                "confidence": attachment_confidence,
                "target_edges": association.get("dimension_target_edges") or [],
                "common_geometry_groups": association.get("common_attachment_geometry_groups") or [],
                "recovered_geometry": association.get("recovered_dimension_geometry"),
            },
            "evidence_status": evidence_status,
        })
    _assign_ranks(cases)
    return {
        "schema_version": SCHEMA_VERSION,
        "method": METHOD,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "production_updated": False,
        "source": {
            "path": str(source),
            "sha256": _sha256(source),
            "bytes": source.stat().st_size,
        },
        "summary": {
            "native_dimension_count": len(dimensions),
            "view_cluster_count": len(analyzer.view_clusters),
            "extracted_rule_count": len(cases),
            "reliable_2d_rule_count": sum(case["evidence_status"] == "EXTRACTED_RELIABLE_2D" for case in cases),
            "review_candidate_count": sum(case["evidence_status"] == "REVIEW_CANDIDATE" for case in cases),
            "rejected_count": len(rejected),
            "feature_linked_count": 0,
        },
        "view_clusters": [
            {
                "view_id": cluster.view_id,
                "bbox": [round(float(value), 6) for value in cluster.bbox],
                "primitive_count": len(cluster.primitive_indexes),
            }
            for cluster in analyzer.view_clusters
        ],
        "drawing_rule_cases": cases,
        "rejected_dimensions": rejected,
    }


def _write_readme(output_dir: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines: Iterable[str] = (
        "# Drawing Rule Extraction Baseline V1",
        "",
        "This directory is an offline experiment. Production drawing and tolerance databases were not updated.",
        "",
        f"- Source: `{report['source']['path']}`",
        f"- Native dimensions: {summary['native_dimension_count']}",
        f"- Extracted view-relative rules: {summary['extracted_rule_count']}",
        f"- Reliable 2D rules: {summary['reliable_2d_rule_count']}",
        f"- Review candidates: {summary['review_candidate_count']}",
        f"- Rejected: {summary['rejected_count']}",
        "- 3D feature links: not attempted in this baseline",
        "",
        "`drawing_rule_cases.json` stores normalized placement rather than absolute paper coordinates.",
    )
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dxf", required=True, help="source annotated DXF")
    parser.add_argument("--output", required=True, help="new experiment output directory")
    args = parser.parse_args()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report = extract_drawing_rule_cases(args.dxf)
    (output_dir / "drawing_rule_cases.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_readme(output_dir, report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
