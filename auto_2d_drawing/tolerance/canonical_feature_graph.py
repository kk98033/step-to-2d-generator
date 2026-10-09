"""Adapter from the main web feature records to the tolerance FRG interface."""

from typing import Any, Dict, List, Optional

from auto_2d_drawing.canonical_features import (
    CANONICAL_FEATURE_SCHEMA,
    extract_canonical_features,
)
from auto_2d_drawing.tolerance.feature_graph import FeatureNode, FeatureRelationGraph


FEATURE_TYPE_MAP = {
    "shaft_or_boss": "shaft_segment",
    "hole": "hole",
    "groove_or_slot": "retaining_ring_groove",
    "step": "locating_shoulder",
    "cone_or_chamfer": "pilot_chamfer",
    "fillet_or_round": "transition_fillet",
    "overall_size": "overall_dimension",
    "overall_bounds": "overall_dimension",
    "overall": "overall_dimension",
}


def _axis_vector(axis: str) -> List[float]:
    return {
        "X": [1.0, 0.0, 0.0],
        "Y": [0.0, 1.0, 0.0],
        "Z": [0.0, 0.0, 1.0],
    }.get(str(axis or "").upper(), [0.0, 0.0, 1.0])


def _canonical_nominal(record: Dict[str, Any]) -> Dict[str, Any]:
    nominal = dict(record.get("nominal") or {})
    feature_type = str(record.get("type") or "")
    if feature_type == "groove_or_slot":
        if "groove_diameter" not in nominal:
            diameter = nominal.get("diameter", nominal.get("major_diameter"))
            if diameter is not None:
                nominal["groove_diameter"] = diameter
        if "groove_width" not in nominal:
            width = nominal.get("width", nominal.get("length"))
            if width is not None:
                nominal["groove_width"] = width
    elif feature_type == "cone_or_chamfer":
        if "chamfer_height" not in nominal:
            height = nominal.get("chamfer", nominal.get("height"))
            if height is not None:
                nominal["chamfer_height"] = height
        if "angle" not in nominal:
            angle = nominal.get("angle", nominal.get("included_angle"))
            if angle is not None:
                nominal["angle"] = angle
    elif feature_type == "step" and "step_height" not in nominal:
        height = nominal.get("length", nominal.get("depth"))
        if height is not None:
            nominal["step_height"] = height
    return nominal


def _source_info(record: Dict[str, Any], main_axis: str) -> Dict[str, Any]:
    geometry = dict(record.get("geometry") or {})
    kind = str(geometry.get("kind") or record.get("type") or "")
    source = dict(geometry)
    source["type"] = "step" if kind == "axial_step" else kind
    source["canonical_feature_id"] = record.get("id")
    source["canonical_feature_type"] = record.get("type")
    source["canonical_role"] = record.get("role")
    source["canonical_schema"] = CANONICAL_FEATURE_SCHEMA
    source["canonical_record"] = record

    if kind == "cylinder":
        diameter = float(geometry.get("diameter", (record.get("nominal") or {}).get("diameter", 0.0)) or 0.0)
        source.setdefault("radius", diameter / 2.0)
        source.setdefault("length", float(geometry.get("length", (record.get("nominal") or {}).get("length", 0.0)) or 0.0))
        source.setdefault("axis_dir", _axis_vector(geometry.get("axis") or main_axis))
        source.setdefault("is_hole", str(record.get("type") or "") == "hole")
    return source


class CanonicalFeatureGraphExtractor:
    """Build tolerance nodes without changing the IDs exposed by the main UI."""

    def build_graph(self, shape, part_type: Optional[str] = None) -> FeatureRelationGraph:
        feature_set = extract_canonical_features(shape, part_type=part_type)
        summary = feature_set.feature_extractor.summary()
        bbox = dict(summary.get("bounding_box") or {})
        main_axis = str(summary.get("main_axis") or "Z").upper()
        axis_index = {"X": 0, "Y": 1, "Z": 2}.get(main_axis, 2)
        total_length = float(bbox.get({0: "W", 1: "H", 2: "D"}[axis_index], 0.0) or 0.0)
        graph = FeatureRelationGraph(
            part_type=feature_set.part_type,
            main_axis=main_axis.lower(),
            total_length=total_length,
        )

        for record in feature_set.records:
            geometry = dict(record.get("geometry") or {})
            center = list(geometry.get("center") or summary.get("center") or [0.0, 0.0, 0.0])
            while len(center) < 3:
                center.append(0.0)
            size = list(geometry.get("size") or [0.0, 0.0, 0.0])
            while len(size) < 3:
                size.append(0.0)
            center_axial = float(center[axis_index])
            span_length = abs(float(size[axis_index] or 0.0))
            if span_length <= 0.0:
                span_length = abs(float((record.get("nominal") or {}).get("length", 0.0) or 0.0))

            raw_type = str(record.get("type") or "general_feature")
            graph.add_node(FeatureNode(
                id=str(record["id"]),
                feature_type=FEATURE_TYPE_MAP.get(raw_type, raw_type),
                nominal=_canonical_nominal(record),
                axial_span=[
                    round(center_axial - span_length / 2.0, 3),
                    round(center_axial + span_length / 2.0, 3),
                ],
                center_axial=round(center_axial, 3),
                source_info=_source_info(record, main_axis),
                inferred_role=str(record.get("role") or "GENERAL_FEATURE").upper(),
            ))

        self._link_localized_neighbors(graph)
        return graph

    @staticmethod
    def _link_localized_neighbors(graph: FeatureRelationGraph) -> None:
        local_nodes = [
            node for node in graph.nodes
            if (node.source_info or {}).get("type") not in {"bbox", "axis", "plane"}
        ]
        ordered = sorted(local_nodes, key=lambda node: node.center_axial)
        for index, node in enumerate(ordered):
            node.boundary_position = (
                "LEFT_END" if index == 0 else
                "RIGHT_END" if index == len(ordered) - 1 else
                "INTERIOR"
            )
            node.adjacent_left_type = ordered[index - 1].feature_type if index else "NONE"
            node.adjacent_right_type = ordered[index + 1].feature_type if index + 1 < len(ordered) else "NONE"
            node.neighbor_types = sorted({
                other.feature_type
                for other in ordered
                if other.id != node.id and abs(other.center_axial - node.center_axial) <= 12.0
            })
            if node.boundary_position in {"LEFT_END", "RIGHT_END"}:
                node.neighbor_types.append("shaft_end")
