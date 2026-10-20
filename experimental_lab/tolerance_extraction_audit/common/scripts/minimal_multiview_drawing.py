"""Render engineer-selected features as separate orthographic drawing files."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import ezdxf

from auto_2d_drawing.smart_annotation_engine import SmartAnnotationEngine
from auto_2d_drawing.pdf_exporter import export_pdf, export_png
from auto_2d_drawing.step_reader import load_step
from auto_2d_drawing.view_projector import ViewProjector

try:
    from .angular_dimension_renderer import build_angular_render_tasks
    from .annular_composite_features import extract_annular_composites
    from .dimension_intents import build_dimension_intent_set
    from .engineering_dimension_renderer import (
        diameter_arrow_audit,
        diameter_label_sector,
        render_annular_dimensions,
    )
    from .rectangular_hole_renderer import build_rectangular_hole_render_tasks
    from .radial_band_pattern_features import detect_radial_pattern_bands, build_radial_band_render_tasks
    from .run_view_plan_experiment import build_view_candidates
    from .run_selected_feature_render_experiment import validate_selection
    from .selected_feature_renderer_bridge import (
        bridge_selected_features,
        build_relational_feature_candidates,
    )
    from .relational_dimension_intents import detect_hub_radial_partitions, detect_rectangular_hole_pattern
    from .solid_shaft_features import extract_solid_shaft_features
    from .thin_wall_dimension_pipeline import extract_thin_wall_dimension
    from .thickness_dimension_renderer import build_thickness_render_task
    from .run_fan_half_section_projection_experiment import (
        _central_section_curves,
        _project as _project_section_shape,
        _shape_bounds,
    )
except ImportError:
    from angular_dimension_renderer import build_angular_render_tasks
    from annular_composite_features import extract_annular_composites
    from dimension_intents import build_dimension_intent_set
    from engineering_dimension_renderer import diameter_arrow_audit, diameter_label_sector, render_annular_dimensions
    from rectangular_hole_renderer import build_rectangular_hole_render_tasks
    from radial_band_pattern_features import detect_radial_pattern_bands, build_radial_band_render_tasks
    from run_view_plan_experiment import build_view_candidates
    from run_selected_feature_render_experiment import validate_selection
    from selected_feature_renderer_bridge import bridge_selected_features, build_relational_feature_candidates
    from relational_dimension_intents import detect_hub_radial_partitions, detect_rectangular_hole_pattern
    from solid_shaft_features import extract_solid_shaft_features
    from thin_wall_dimension_pipeline import extract_thin_wall_dimension
    from thickness_dimension_renderer import build_thickness_render_task
    from run_fan_half_section_projection_experiment import (
        _central_section_curves,
        _project as _project_section_shape,
        _shape_bounds,
    )


METHOD = "MINIMAL_ENGINEER_SELECTED_MULTIVIEW_DRAWING_V1"
ORTHOGRAPHIC_VIEWS = ("front", "top", "right")
VIEW_LABELS = {
    "front": "前視圖 FRONT VIEW",
    "top": "俯視圖 TOP VIEW",
    "right": "右側視圖 RIGHT VIEW",
    "half_section": "半剖視圖 HALF SECTION VIEW",
    "central_section": "中央剖面圖 CENTRAL SECTION VIEW",
}


def configure_engineer_selections(
    candidates: Sequence[Mapping[str, Any]],
    selections: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    by_id = {
        str(candidate.get("rule_id") or candidate.get("id")): candidate
        for candidate in candidates
    }
    configured = []
    missing = []
    for selection in selections:
        feature_id = str(selection.get("feature_id") or "")
        source = by_id.get(feature_id)
        if source is None:
            missing.append(feature_id)
            continue
        view_name = str(selection.get("view") or source.get("preferred_view") or "front")
        if view_name not in ORTHOGRAPHIC_VIEWS:
            raise ValueError(f"Unsupported orthographic view: {view_name}")
        rule = copy.deepcopy(source)
        side = str(selection.get("side") or rule.get("side") or "BOTTOM").upper()
        rule.update({
            "enabled": bool(selection.get("enabled", True)),
            "target_views": [view_name],
            "views": [view_name],
            "preferred_view": view_name,
            "side": side,
            "sides": [side],
            "tolerance": str(selection.get("tolerance", rule.get("default_tolerance") or "")),
        })
        configured.append(rule)
    return configured, missing


def choose_half_section_candidate(
    candidates: Sequence[Mapping[str, Any]],
    preferred_spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    half_sections = [candidate for candidate in candidates if candidate.get("kind") == "HALF_SECTION"]
    if not half_sections:
        raise ValueError("No dynamic half-section candidate was generated")
    if preferred_spec:
        matched = [
            candidate for candidate in half_sections
            if candidate.get("section_spec", {}).get("cut_normal_axis") == preferred_spec.get("cut_normal_axis")
            and candidate.get("section_spec", {}).get("kept_side") == preferred_spec.get("kept_side")
            and candidate.get("base_view") == preferred_spec.get("view_direction")
        ]
        if not matched:
            raise ValueError("The prior-verified half-section specification is unavailable")
        selected = copy.deepcopy(max(matched, key=lambda item: float(item.get("quality") or 0.0)))
        if preferred_spec.get("include_hidden") is False:
            selected["view_data"]["hidden"] = []
        selected["selection_source"] = str(preferred_spec.get("source") or "PRIOR_VERIFIED_SPEC")
        return selected
    return copy.deepcopy(max(half_sections, key=lambda item: float(item.get("quality") or 0.0)))


def build_prior_verified_section_candidate(
    shape: Any,
    projector: ViewProjector,
    preferred_spec: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if str((preferred_spec or {}).get("kind") or "").upper() != "CENTRAL_SECTION_CURVES":
        return None
    cut_axis = str(preferred_spec.get("cut_axis") or "Y").upper()
    if cut_axis not in "XYZ":
        raise ValueError(f"Unsupported central section axis: {cut_axis}")
    base_view = str(preferred_spec.get("view_direction") or "top")
    shape_bounds = _shape_bounds(shape)
    section_shape = _central_section_curves(shape, shape_bounds, "XYZ".index(cut_axis))
    view_data = _project_section_shape(projector, section_shape, base_view)
    if preferred_spec.get("include_hidden") is False:
        view_data["hidden"] = []
    return {
        "kind": "CENTRAL_SECTION_CURVES",
        "base_view": base_view,
        "view_data": view_data,
        "section_spec": {
            "view_type": "CENTRAL_SECTION_CURVES",
            "cut_normal_axis": cut_axis,
            "plane_origin": [
                round((shape_bounds[index] + shape_bounds[index + 3]) / 2.0, 6)
                for index in range(3)
            ],
            "view_direction": base_view,
            "include_hidden": bool(preferred_spec.get("include_hidden", False)),
            "derivation": "BND_BOX_CENTER_PLANE_SECTION",
        },
        "quality": float(preferred_spec.get("selection_score") or 0.0),
        "selection_source": str(preferred_spec.get("source") or "PRIOR_VERIFIED_SPEC"),
    }


def _normalise_view(view: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(view))
    bbox = result["bbox"]
    result["size"] = (
        max(float(bbox[2]) - float(bbox[0]), 1e-9),
        max(float(bbox[3]) - float(bbox[1]), 1e-9),
    )
    return result


def _audit_dxf(path: Path) -> dict[str, Any]:
    document = ezdxf.readfile(path)
    auditor = document.audit()
    full_span_diameters = []
    for dimension in document.modelspace().query("DIMENSION"):
        if int(dimension.dxf.dimtype) & 7 != 3:
            continue
        result = diameter_arrow_audit(dimension)
        if result["two_arrowheads"]:
            full_span_diameters.append(result)
    return {
        "dimension_count": len(document.modelspace().query("DIMENSION")),
        "audit_error_count": len(auditor.errors),
        "full_span_diameter_count": len(full_span_diameters),
        "full_span_diameter_arrows_inward": all(
            item["arrowheads_point_inward"] for item in full_span_diameters
        ),
    }


def _adapt_rules_to_single_view(
    rules: Sequence[dict[str, Any]],
    source_view: str,
) -> list[dict[str, Any]]:
    adapted = []
    for source_rule in rules:
        rule = copy.deepcopy(source_rule)
        payload = copy.deepcopy(
            (rule.get("view_geometry") or {}).get(source_view)
            or rule.get("geometry_payload")
            or {}
        )
        rule.update({
            "target_views": ["front"],
            "views": ["front"],
            "preferred_view": "front",
            "geometry_payload": payload,
            "view_geometry": {"front": payload},
        })
        adapted.append(rule)
    return adapted


def _visible_extents(modelspace) -> tuple[float, float, float, float]:
    points = []
    for entity in modelspace.query('*[layer=="VISIBLE"]'):
        if entity.dxftype() == "LINE":
            points.extend(((entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)))
        elif entity.dxftype() == "LWPOLYLINE":
            points.extend((float(point[0]), float(point[1])) for point in entity.get_points("xy"))
    if not points:
        raise ValueError("Rendered drawing has no visible geometry")
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _coordinate_mapper(modelspace, view: Mapping[str, Any]):
    drawing_bbox = _visible_extents(modelspace)
    source_bbox = view["bbox"]
    source_width = max(float(source_bbox[2]) - float(source_bbox[0]), 1e-9)
    source_height = max(float(source_bbox[3]) - float(source_bbox[1]), 1e-9)
    scale = min(
        (drawing_bbox[2] - drawing_bbox[0]) / source_width,
        (drawing_bbox[3] - drawing_bbox[1]) / source_height,
    )

    def map_point(point: Sequence[float]) -> tuple[float, float]:
        return (
            drawing_bbox[0] + (float(point[0]) - float(source_bbox[0])) * scale,
            drawing_bbox[1] + (float(point[1]) - float(source_bbox[1])) * scale,
        )

    return map_point, scale, drawing_bbox


def _inject_angular_dimensions(document, tasks: Sequence[Mapping[str, Any]], view: Mapping[str, Any]) -> None:
    if not tasks:
        return
    modelspace = document.modelspace()
    map_point, scale, _drawing_bbox = _coordinate_mapper(modelspace, view)

    for task in tasks:
        center = map_point(task["center"])
        radius = float(task["radius"]) * scale
        start_angle = math.radians(float(task["start_angle"]))
        end_angle = math.radians(float(task["end_angle"]))
        p1 = (center[0] + radius * math.cos(start_angle), center[1] + radius * math.sin(start_angle))
        p2 = (center[0] + radius * math.cos(end_angle), center[1] + radius * math.sin(end_angle))
        if task.get("extend_to_center"):
            modelspace.add_line(center, p1, dxfattribs={"layer": "DIM"})
            modelspace.add_line(center, p2, dxfattribs={"layer": "DIM"})
        mid_angle = start_angle + math.radians(float(task["angle"])) / 2.0
        base_radius = radius * float(task.get("base_radius_factor", 0.72))
        base = (
            center[0] + base_radius * math.cos(mid_angle),
            center[1] + base_radius * math.sin(mid_angle),
        )
        dimension = modelspace.add_angular_dim_3p(
            base=base,
            center=center,
            p1=p1,
            p2=p2,
            text=str(task["label"]),
            dimstyle="EZ_CURVED",
        )
        dimension.render()


def _inject_annular_dimensions(document, tasks: Sequence[Mapping[str, Any]], view: Mapping[str, Any]) -> None:
    if not tasks:
        return
    modelspace = document.modelspace()
    map_point, _scale, drawing_bbox = _coordinate_mapper(modelspace, view)
    render_annular_dimensions(
        document,
        tasks,
        map_point=map_point,
        drawing_bbox=drawing_bbox,
    )


def _side_vector(side: str) -> tuple[float, float]:
    return {
        "TOP": (0.0, 1.0),
        "BOTTOM": (0.0, -1.0),
        "LEFT": (-1.0, 0.0),
        "RIGHT": (1.0, 0.0),
    }.get(str(side).upper(), (0.0, 1.0))


def _inject_specialized_dimensions(
    document,
    tasks: Sequence[Mapping[str, Any]],
    view: Mapping[str, Any],
) -> None:
    if not tasks:
        return
    modelspace = document.modelspace()
    map_point, scale, drawing_bbox = _coordinate_mapper(modelspace, view)
    span = max(drawing_bbox[2] - drawing_bbox[0], drawing_bbox[3] - drawing_bbox[1])
    for index, task in enumerate(tasks):
        lane = max(int(task.get("lane", index)), 0)
        side = str(task.get("side") or "TOP").upper()
        renderer = str(task.get("renderer") or "LINEAR_DIMENSION_RENDERER")
        label = str(task.get("label") or f"{float(task['nominal_value']):.3f}")
        if renderer == "DIAMETER_RENDERER":
            if task.get("anchors"):
                anchors = [map_point(point) for point in task["anchors"]]
                center = ((anchors[0][0] + anchors[1][0]) / 2.0, (anchors[0][1] + anchors[1][1]) / 2.0)
                radius = math.dist(anchors[0], anchors[1]) / 2.0
            else:
                center = map_point(task["center"])
                radius = float(task["radius"]) * scale
            angle = {"RIGHT": 0.0, "TOP": 90.0, "LEFT": 180.0, "BOTTOM": 270.0}.get(side, 135.0)
            location_radius = radius + span * (0.045 + lane * 0.025)
            location = (
                center[0] + location_radius * math.cos(math.radians(angle)),
                center[1] + location_radius * math.sin(math.radians(angle)),
            )
            dimension = modelspace.add_diameter_dim(
                center=center,
                radius=radius,
                angle=angle,
                location=location,
                text=label,
                dimstyle="EZ_RADIUS",
                dxfattribs={"layer": "DIM"},
            )
        else:
            source_anchors = task.get("anchors") or (task.get("p1"), task.get("p2"))
            p1, p2 = [map_point(point) for point in source_anchors]
            delta_x, delta_y = p2[0] - p1[0], p2[1] - p1[1]
            length = max(math.hypot(delta_x, delta_y), 1e-9)
            normal = (-delta_y / length, delta_x / length)
            side_vector = _side_vector(side)
            sign = 1.0 if normal[0] * side_vector[0] + normal[1] * side_vector[1] >= 0.0 else -1.0
            distance = sign * span * (0.055 + lane * 0.035)
            dimension = modelspace.add_aligned_dim(
                p1=p1,
                p2=p2,
                distance=distance,
                text=label,
                dimstyle="EZDXF",
                dxfattribs={"layer": "DIM"},
            )
        dimension.render()


def _replace_single_view_label(
    path: Path,
    label: str,
    pdf_path: Path,
    png_path: Path,
    angular_tasks: Sequence[Mapping[str, Any]],
    annular_tasks: Sequence[Mapping[str, Any]],
    specialized_tasks: Sequence[Mapping[str, Any]],
    view: Mapping[str, Any],
) -> None:
    document = ezdxf.readfile(path)
    for entity in document.modelspace().query("TEXT MTEXT"):
        text = entity.dxf.text if entity.dxftype() == "TEXT" else entity.text
        if "FRONT VIEW" not in text:
            continue
        replacement = label
        if entity.dxftype() == "TEXT":
            entity.dxf.text = replacement
        else:
            entity.text = replacement
    _inject_angular_dimensions(document, angular_tasks, view)
    _inject_annular_dimensions(document, annular_tasks, view)
    _inject_specialized_dimensions(document, specialized_tasks, view)
    document.saveas(path)
    export_pdf(document, document.modelspace(), str(pdf_path), dark_bg=True)
    export_png(document, document.modelspace(), str(png_path), dark_bg=True)


def _render_one(
    engine: SmartAnnotationEngine,
    output_dir: Path,
    output_name: str,
    view_name: str,
    view: Mapping[str, Any],
    rules: Sequence[dict[str, Any]],
    title_info: Mapping[str, Any],
    label_key: str | None = None,
    angular_tasks: Sequence[Mapping[str, Any]] = (),
    annular_tasks: Sequence[Mapping[str, Any]] = (),
    specialized_tasks: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    drawing_dir = output_dir / output_name
    drawing_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "dxf": drawing_dir / f"{output_name}.dxf",
        "pdf": drawing_dir / f"{output_name}.pdf",
        "png": drawing_dir / f"{output_name}.png",
    }
    engine.render_custom_drawing(
        dxf_path=str(paths["dxf"]),
        pdf_path=str(paths["pdf"]),
        png_path=str(paths["png"]),
        feature_records=_adapt_rules_to_single_view(rules, view_name),
        view_data={"front": _normalise_view(view)},
        title_info=dict(title_info),
    )
    _replace_single_view_label(
        paths["dxf"],
        VIEW_LABELS[label_key or view_name],
        paths["pdf"],
        paths["png"],
        angular_tasks,
        annular_tasks,
        specialized_tasks,
        view,
    )
    audit = _audit_dxf(paths["dxf"])
    expected_full_span_diameters = sum(
        str(task.get("dim_type") or "").upper() == "DIAMETER" for task in annular_tasks
    )
    audit["expected_full_span_diameter_count"] = expected_full_span_diameters
    audit["full_span_diameter_contract_passed"] = (
        audit["full_span_diameter_count"] == expected_full_span_diameters
        and audit["full_span_diameter_arrows_inward"]
    )
    return {
        "view": view_name,
        "selected_feature_ids": [str(rule.get("rule_id") or rule.get("id")) for rule in rules],
        "selected_annotation_count": len(rules),
        "relational_annotation_count": len(angular_tasks),
        "annular_annotation_count": len(annular_tasks),
        "specialized_annotation_count": len(specialized_tasks),
        "files": {name: str(path.resolve()) for name, path in paths.items()},
        **audit,
    }


def render_minimal_multiview_drawing(
    step_path: Path,
    selections: Sequence[Mapping[str, Any]],
    output_dir: Path,
    *,
    part_hint: str = "",
    include_half_section: bool = False,
    relational_selections: Sequence[Mapping[str, Any]] = (),
    pattern_angle_selections: Sequence[Mapping[str, Any]] = (),
    radial_band_angle_selections: Sequence[Mapping[str, Any]] = (),
    annular_selections: Sequence[Mapping[str, Any]] = (),
    rectangular_hole_selections: Sequence[Mapping[str, Any]] = (),
    shaft_dimension_selections: Sequence[Mapping[str, Any]] = (),
    thickness_selections: Sequence[Mapping[str, Any]] = (),
    half_section_selection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    shape = load_step(str(step_path))
    projector = ViewProjector()
    standard_views = projector.project_all_views(shape, view_names=list(ORTHOGRAPHIC_VIEWS))
    engine = SmartAnnotationEngine()
    candidates = engine.get_candidate_rules(shape, standard_views, part_hint=part_hint)
    configured, missing = configure_engineer_selections(candidates, selections)
    if missing:
        raise ValueError(f"Unknown selected feature IDs: {', '.join(missing)}")

    accepted_rules = []
    rejected_rules = []
    for rule in configured:
        view_name = str(rule["target_views"][0])
        validation = validate_selection(rule, view_name, standard_views[view_name])
        record = {
            "feature_id": str(rule.get("rule_id") or rule.get("id")),
            "view": view_name,
            "status": validation["status"],
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        }
        if validation["status"] == "FAIL":
            rejected_rules.append(record)
        else:
            accepted_rules.append(rule)

    radial_results = []
    for view_name, view in standard_views.items():
        result = detect_hub_radial_partitions(view)
        result["view"] = view_name
        radial_results.append(result)
    best_radial = max(
        radial_results,
        key=lambda item: (
            int(bool(item.get("resolved"))),
            len(item.get("boundary_angles") or []),
            float(item.get("confidence") or 0.0),
        ),
    )
    angular_tasks = []
    relational_rejections = []
    if relational_selections and best_radial.get("resolved"):
        view = standard_views[str(best_radial["view"])]
        bbox = view["bbox"]
        radius = min(float(bbox[2]) - float(bbox[0]), float(bbox[3]) - float(bbox[1])) * 0.32
        unique_relations = {}
        for relation in (
            best_radial.get("center_pitch_relations")
            or best_radial.get("center_pitches")
            or []
        ):
            rounded = int(round(float(relation["angle"])))
            unique_relations.setdefault(rounded, relation)
        render_tasks = [
            {
                "id": f"angle_{rounded}",
                "start_angle": float(relation["start_angle"]),
                "end_angle": float(relation["end_angle"]),
                "angle": float(relation["angle"]),
                "radius": radius,
                "source_relation": relation,
            }
            for rounded, relation in sorted(unique_relations.items())
        ]
        fan_result = {
            **best_radial,
            "angular_render_tasks": render_tasks,
            "verification_status": "AUTO_VERIFIED",
        }
        relational_candidates = build_relational_feature_candidates(fan_result, {})
        relational_plan = bridge_selected_features(relational_candidates, relational_selections)
        angular_tasks = relational_plan["render_tasks"]
        relational_rejections = relational_plan["rejected_selections"]

    pattern_rejections = []
    if pattern_angle_selections:
        if not best_radial.get("resolved"):
            pattern_rejections = [
                {"feature_id": str(item.get("id") or ""), "reason": "RADIAL_PATTERN_UNRESOLVED"}
                for item in pattern_angle_selections
            ]
        else:
            center_angles = sorted(float(item["center_angle"]) for item in best_radial.get("elements") or [])
            relations = list(best_radial.get("pairwise_center_relations") or [])
            relations.extend({
                "start_angle": 0.0,
                "end_angle": angle,
                "angle": min(angle % 360.0, (-angle) % 360.0),
                "semantic": "hub_radial_partition.datum_to_center_angle",
            } for angle in center_angles)
            radial_view = standard_views[str(best_radial["view"])]
            bbox = radial_view["bbox"]
            span = min(float(bbox[2]) - float(bbox[0]), float(bbox[3]) - float(bbox[1]))
            pattern_tasks = build_angular_render_tasks(
                relations,
                [dict(item) for item in pattern_angle_selections],
                base_radius=span * 0.30,
                lane_spacing=span * 0.045,
            )
            for task in pattern_tasks:
                task["center"] = list(best_radial["center"])
                task["view"] = str(best_radial["view"])
            angular_tasks.extend(pattern_tasks)

    radial_band_rejections = []
    radial_band_placement_trace: list[dict[str, Any]] = []
    if radial_band_angle_selections:
        band_results = []
        for view_name, view in standard_views.items():
            result = detect_radial_pattern_bands(view)
            result["view"] = view_name
            band_results.append(result)
        best_bands = max(
            band_results,
            key=lambda item: (int(bool(item.get("resolved"))), int(item.get("pattern_count") or 0)),
        )
        if not best_bands.get("resolved"):
            radial_band_rejections = [
                {"feature_id": str(item.get("feature_id") or item.get("id") or ""), "reason": "RADIAL_BANDS_UNRESOLVED"}
                for item in radial_band_angle_selections
            ]
        else:
            radial_view = standard_views[str(best_bands["view"])]
            bbox = radial_view["bbox"]
            span = min(float(bbox[2]) - float(bbox[0]), float(bbox[3]) - float(bbox[1]))
            diameter_count = sum(
                1
                for item in annular_selections
                if "diameter" in str(item.get("feature_id") or item.get("id") or "").lower()
                and str(item.get("view") or best_bands["view"]) == str(best_bands["view"])
            )
            reserved_sector = diameter_label_sector(diameter_count)
            band_tasks, radial_band_rejections = build_radial_band_render_tasks(
                best_bands,
                [dict(item) for item in radial_band_angle_selections],
                view_name=str(best_bands["view"]),
                view_span=span,
                reserved_sectors=[reserved_sector] if reserved_sector else [],
                placement_trace=radial_band_placement_trace,
            )
            angular_tasks.extend(band_tasks)

    annular_tasks = []
    annular_rejections = []
    if annular_selections:
        annular_result = extract_annular_composites(shape)
        annular_by_id = {str(item["id"]): item for item in annular_result["dimensions"]}
        for selection in annular_selections:
            feature_id = str(selection.get("feature_id") or selection.get("id") or "")
            candidate = annular_by_id.get(feature_id)
            if candidate is None:
                annular_rejections.append({"feature_id": feature_id, "reason": "UNKNOWN_ANNULAR_DIMENSION"})
                continue
            task = copy.deepcopy(candidate)
            task["feature_id"] = feature_id
            task["view"] = str(selection.get("view") or task["view"])
            task["side"] = str(selection.get("side") or task.get("side") or "TOP").upper()
            task["lane"] = int(selection.get("lane", len(annular_tasks)))
            if selection.get("label"):
                task["label"] = str(selection["label"])
            annular_tasks.append(task)

    specialized_tasks = []
    specialized_rejections = []
    hole_result = detect_rectangular_hole_pattern(shape)
    hole_by_id = {
        str(item["task_id"]): item
        for item in build_rectangular_hole_render_tasks(hole_result)
    } if hole_result.get("resolved") else {}
    shaft_result = extract_solid_shaft_features(shape)
    shaft_by_id = {str(item["id"]): item for item in shaft_result.get("dimensions") or []}
    thin_wall_result = extract_thin_wall_dimension(shape)
    thin_wall_intents = build_dimension_intent_set(
        thin_wall_result=thin_wall_result if thin_wall_result.get("resolved") else None,
    ).get("intents") or []
    thickness_by_id = {str(item["intent_id"]): item for item in thin_wall_intents}
    selection_groups = (
        (rectangular_hole_selections, hole_by_id, "UNKNOWN_RECTANGULAR_HOLE_DIMENSION", "hole"),
        (shaft_dimension_selections, shaft_by_id, "UNKNOWN_SHAFT_DIMENSION", "shaft"),
        (thickness_selections, thickness_by_id, "UNKNOWN_THICKNESS_DIMENSION", "thickness"),
    )
    for group, candidates_by_id, rejection_reason, family in selection_groups:
        for lane, selection in enumerate(group):
            feature_id = str(selection.get("feature_id") or selection.get("id") or "")
            candidate = candidates_by_id.get(feature_id)
            if candidate is None:
                specialized_rejections.append({"feature_id": feature_id, "reason": rejection_reason})
                continue
            if family == "thickness":
                preferred_view = str(candidate["projection"]["preferred_view"])
                task = build_thickness_render_task(candidate, standard_views[preferred_view]["bbox"], lane=lane)
                task["anchors"] = task.pop("source_anchors")
                task["nominal_value"] = candidate["measurement"]["nominal_value"]
                task["view"] = preferred_view
            else:
                task = copy.deepcopy(candidate)
                task["feature_id"] = feature_id
                task["view"] = str(selection.get("view") or task.get("view") or hole_result.get("view") or "front")
                if family == "shaft":
                    is_diameter = str(task["dim_type"]).upper() == "DIAMETER"
                    task["renderer"] = "DIAMETER_RENDERER" if is_diameter else "LINEAR_DIMENSION_RENDERER"
                    prefix = "%%c" if is_diameter else ""
                    task["label"] = f"{prefix}{float(task['nominal_value']):.3f}"
            task["side"] = str(selection.get("side") or task.get("side") or "TOP").upper()
            task["lane"] = int(selection.get("lane", lane))
            if selection.get("label"):
                task["label"] = str(selection["label"])
            specialized_tasks.append(task)

    output_dir.mkdir(parents=True, exist_ok=True)
    title_base = {
        "part_name": part_hint or step_path.stem,
        "revision": "EXPERIMENT",
        "model_code": METHOD,
    }
    drawings = []
    for view_name in ORTHOGRAPHIC_VIEWS:
        view_rules = [rule for rule in accepted_rules if view_name in rule["target_views"]]
        view_angular_tasks = [task for task in angular_tasks if task.get("view") == view_name]
        view_annular_tasks = [task for task in annular_tasks if task.get("view") == view_name]
        view_specialized_tasks = [task for task in specialized_tasks if task.get("view") == view_name]
        drawings.append(_render_one(
            engine,
            output_dir,
            view_name,
            view_name,
            standard_views[view_name],
            view_rules,
            {**title_base, "drawing_no": f"EXP-{step_path.stem}-{view_name.upper()}"},
            angular_tasks=view_angular_tasks,
            annular_tasks=view_annular_tasks,
            specialized_tasks=view_specialized_tasks,
        ))

    half_section = None
    if include_half_section:
        selected_section = build_prior_verified_section_candidate(shape, projector, half_section_selection)
        if selected_section is None:
            view_candidates = build_view_candidates(shape, standard_views, needs_section=True)
            selected_section = choose_half_section_candidate(view_candidates, half_section_selection)
        base_view = str(selected_section["base_view"])
        section_rules = [rule for rule in accepted_rules if base_view in rule["target_views"]]
        section_angular_tasks = [task for task in angular_tasks if task.get("view") == base_view]
        section_annular_tasks = [task for task in annular_tasks if task.get("view") == base_view]
        section_specialized_tasks = [task for task in specialized_tasks if task.get("view") == base_view]
        half_section = _render_one(
            engine,
            output_dir,
            "half_section",
            base_view,
            selected_section["view_data"],
            section_rules,
            {**title_base, "drawing_no": f"EXP-{step_path.stem}-HALF-SECTION"},
            label_key=(
                "central_section"
                if selected_section.get("kind") == "CENTRAL_SECTION_CURVES"
                else "half_section"
            ),
            angular_tasks=section_angular_tasks,
            annular_tasks=section_annular_tasks,
            specialized_tasks=section_specialized_tasks,
        )
        half_section["section_spec"] = selected_section["section_spec"]
        half_section["quality"] = selected_section["quality"]
        half_section["selection_source"] = selected_section.get("selection_source", "GEOMETRY_QUALITY")

    all_drawings = drawings + ([half_section] if half_section else [])
    placement_groups: dict[str, float] = {}
    for placement in radial_band_placement_trace:
        placement_groups.setdefault(
            str(placement["placement_group"]),
            float(placement["mid_angle"]),
        )
    placement_midpoints = list(placement_groups.values())
    placement_separations = [
        min(
            abs(first - second) % 360.0,
            360.0 - abs(first - second) % 360.0,
        )
        for index, first in enumerate(placement_midpoints)
        for second in placement_midpoints[index + 1:]
    ]
    radial_band_collision_avoidance_passed = (
        not radial_band_angle_selections
        or len(placement_midpoints) <= 1
        or min(placement_separations) >= 45.0
    )
    report = {
        "schema_version": 1,
        "method": METHOD,
        "source_step": str(step_path.resolve()),
        "selection_authority": "EXPLICIT_ENGINEER_SELECTION",
        "requested_selection_count": len(selections) + len(relational_selections) + len(pattern_angle_selections) + len(radial_band_angle_selections) + len(annular_selections) + len(rectangular_hole_selections) + len(shaft_dimension_selections) + len(thickness_selections),
        "configured_selection_count": len(accepted_rules) + len(angular_tasks) + len(annular_tasks) + len(specialized_tasks),
        "rejected_feature_selections": rejected_rules,
        "rejected_relational_selections": relational_rejections,
        "rejected_pattern_angle_selections": pattern_rejections,
        "rejected_radial_band_angle_selections": radial_band_rejections,
        "rejected_annular_selections": annular_rejections,
        "rejected_specialized_selections": specialized_rejections,
        "radial_band_angle_placement": {
            "strategy": "GROUP_RELATED_ANGLES_THEN_MAXIMIZE_POLAR_CLEARANCE",
            "placements": radial_band_placement_trace,
            "group_separations_deg": placement_separations,
            "minimum_group_separation_deg": min(placement_separations) if placement_separations else None,
            "collision_avoidance_passed": radial_band_collision_avoidance_passed,
        },
        "diameter_renderer_contract": {
            "full_span": True,
            "arrowheads_at_both_ends": True,
            "arrowheads_face_inward": True,
        },
        "orthographic_drawings": drawings,
        "half_section_drawing": half_section,
        "success": (
            not rejected_rules
            and not relational_rejections
            and not pattern_rejections
            and not radial_band_rejections
            and not annular_rejections
            and not specialized_rejections
            and radial_band_collision_avoidance_passed
            and len(accepted_rules) + len(angular_tasks) + len(annular_tasks) + len(specialized_tasks)
            == len(selections) + len(relational_selections) + len(pattern_angle_selections) + len(radial_band_angle_selections) + len(annular_selections) + len(rectangular_hole_selections) + len(shaft_dimension_selections) + len(thickness_selections)
            and all(item["audit_error_count"] == 0 for item in all_drawings)
            and all(item["full_span_diameter_contract_passed"] for item in all_drawings)
        ),
        "production_integrated": False,
    }
    (output_dir / "minimal_multiview_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return report
