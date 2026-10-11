"""Rebuild the historical tolerance case base from company CAD evidence.

The v2 pipeline deliberately separates two facts which the old importer mixed:

* ``AUTO_EXTRACTED`` means the tolerance came from a real DXF dimension entity,
  but its 3D feature identity is not proven.
* ``AUTO_VERIFIED`` means an exact-name STEP/DXF pair contains one and only one
  geometrically compatible 3D feature with the same nominal value.

No seed or generated cases are created by this module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import ezdxf

_current_dir = os.path.dirname(os.path.abspath(__file__))
_ws_root = os.path.abspath(os.path.join(_current_dir, "..", ".."))
if _ws_root not in sys.path:
    sys.path.insert(0, _ws_root)

from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPControl import STEPControl_Reader

from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import (
    DxfToleranceExtractor,
    ExtractedDimension,
)
from auto_2d_drawing.tolerance.feature_graph import (
    CANONICAL_FEATURE_TYPES,
    FeatureNode,
    candidate_feature_types_for_dimension,
)
from auto_2d_drawing.tolerance.canonical_feature_graph import CanonicalFeatureGraphExtractor
from auto_2d_drawing.tolerance.feature_inference_2d import FeatureInference2DEngine
from auto_2d_drawing.tolerance.assembly_components import (
    build_component_pair_manifest,
    extract_leaf_components,
    load_component_shape,
)
from auto_2d_drawing.tolerance.historical_manifest import build_pair_manifest
from auto_2d_drawing.tolerance.global_geometry_search import GlobalGeometryPairSearcher
from auto_2d_drawing.tolerance.projection_geometry_verifier import ProjectionGeometryVerifier
from auto_2d_drawing.tolerance.composite_features import (
    build_annular_rim_features,
    build_planar_span_features,
)
from auto_2d_drawing.tolerance.projection_registration_v2 import SimilarityTransform2D
from auto_2d_drawing.tolerance.projected_feature_mapper import (
    FeatureFootprintProjector,
    GlobalFeatureAssignmentResolver,
)


DEFAULT_SOURCE_DIRS = (
    r"D:\School\力致\力致_ref",
    r"D:\School\力致\new_data",
)
DEFAULT_REPORT_PATH = os.path.join(_current_dir, "data", "feature_case_base_rebuild_report.json")
DEFAULT_MANIFEST_PATH = os.path.join(_current_dir, "data", "historical_pair_manifest.json")


def _tolerance_is_plausible_dimension(dim: ExtractedDimension) -> bool:
    config = dim.tolerance_config or {}
    mode = config.get("mode", "NONE")
    if mode == "NONE" or not (0.0 < dim.nominal_value < 100000.0):
        return False
    if dim.source_entity_type not in {
        "DIMENSION",
        "ARC_DIMENSION",
        "RADIAL_DIMENSION",
        "DIAMETER_DIMENSION",
    }:
        return False
    if not dim.is_feature_dimension or dim.validation_status != "AUTO_VALIDATED":
        return False

    if mode == "FIT":
        return bool(config.get("fit_class"))
    if mode == "CUSTOM_SYMMETRIC":
        deviation = abs(float(config.get("dev", 0.0) or 0.0))
        return 0.0 < deviation <= max(5.0, dim.nominal_value * 0.5)
    if mode in {"CUSTOM_LIMITS", "GROOVE"}:
        upper = abs(float(config.get("upper_dev", 0.0) or 0.0))
        lower = abs(float(config.get("lower_dev", 0.0) or 0.0))
        return max(upper, lower) > 0.0 and max(upper, lower) <= max(5.0, dim.nominal_value * 0.5)
    return False


def _extract_dxf_worker(dxf_path: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Process-safe DXF reader used only for the independent 2D extraction."""
    logging.getLogger("ezdxf").setLevel(logging.ERROR)
    extractor = DxfToleranceExtractor()
    try:
        doc = ezdxf.readfile(dxf_path)
        modelspace = doc.modelspace()
    except Exception:
        return dxf_path, []
    dimensions = extractor.extract_from_modelspace(
        modelspace,
        os.path.basename(dxf_path),
        include_rejected=True,
        native_dimensions_only=True,
    )
    plausible_dimensions = [item for item in dimensions if _tolerance_is_plausible_dimension(item)]
    needs_geometry = any(item.dimension_category == "DIAMETER" for item in plausible_dimensions)
    inference_engine = FeatureInference2DEngine(modelspace if needs_geometry else None)
    for item in plausible_dimensions:
        item.feature_inference_2d = inference_engine.infer(item)
    return dxf_path, [item.to_dict() for item in dimensions]


class HistoricalDataIngestor:
    """Create a clean, provenance-first company tolerance database."""

    def __init__(self, case_base: Optional[FeatureCaseBase] = None):
        self.case_base = case_base or FeatureCaseBase()
        self.frg_extractor = CanonicalFeatureGraphExtractor()
        self.dxf_extractor = DxfToleranceExtractor()

    @staticmethod
    def _normalise_stem(filename: str) -> str:
        stem = os.path.splitext(os.path.basename(filename))[0].lower().strip()
        return stem.replace("new_", "").replace("old_", "")

    @classmethod
    def _revision_identity(cls, filename: str) -> Tuple[str, Optional[int], str]:
        """Return (document identity, revision number, normalized full stem).

        Both ``RNN`` part revisions and ``ANN`` assembly/process drawing
        revisions are company conventions.  The identity deliberately keeps
        any descriptive prefix, so ``烤漆組合-1M...-A01/A02`` collapses to the
        same document lineage while unrelated drawings do not.
        """
        stem = cls._normalise_stem(filename)
        # The separator is mandatory.  Company part numbers themselves can
        # contain ``A`` followed by digits (for example ``0AJ0A00009``); an
        # optional separator would collapse unrelated unversioned parts.
        match = re.match(r"^(.*?)[-_][ra](\d+)$", stem, re.IGNORECASE)
        if not match:
            return stem, None, stem
        identity = match.group(1).rstrip("-_")
        return identity, int(match.group(2)), stem

    @staticmethod
    def _select_latest_revisions(candidates: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[str, int]]:
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for candidate in candidates:
            grouped[candidate["identity"]].append(candidate)

        selected: Dict[str, str] = {}
        superseded = 0
        multi_revision_groups = 0
        duplicate_selected_revision = 0
        for items in grouped.values():
            revisions = sorted({item["revision"] for item in items if item["revision"] is not None})
            if revisions:
                if len(revisions) > 1:
                    multi_revision_groups += 1
                latest_revision = revisions[-1]
                finalists = [item for item in items if item["revision"] == latest_revision]
            else:
                finalists = items
            finalists.sort(key=lambda item: (item["mtime"], item["path"].lower()))
            winner = finalists[-1]
            selected[winner["full_stem"]] = winner["path"]
            superseded += len(items) - 1
            duplicate_selected_revision += max(0, len(finalists) - 1)
        return selected, {
            "discovered_files": len(candidates),
            "selected_latest_files": len(selected),
            "superseded_or_duplicate_files": superseded,
            "multi_revision_groups": multi_revision_groups,
            "duplicate_latest_revision_files": duplicate_selected_revision,
        }

    def discover_sources(self, search_dirs: Sequence[str]) -> Dict[str, Any]:
        step_candidates: List[Dict[str, Any]] = []
        dxf_candidates: List[Dict[str, Any]] = []
        step_name_counts: Counter[str] = Counter()
        dxf_name_counts: Counter[str] = Counter()

        for source_dir in search_dirs:
            if not os.path.isdir(source_dir):
                continue
            for root, _dirs, files in os.walk(source_dir):
                for filename in files:
                    extension = os.path.splitext(filename)[1].lower()
                    if extension not in {".stp", ".step", ".dxf"}:
                        continue
                    full_path = os.path.abspath(os.path.join(root, filename))
                    identity, revision, full_stem = self._revision_identity(filename)
                    candidate = {
                        "identity": identity,
                        "revision": revision,
                        "full_stem": full_stem,
                        "path": full_path,
                        "mtime": os.path.getmtime(full_path),
                    }
                    if extension in {".stp", ".step"}:
                        step_candidates.append(candidate)
                        step_name_counts[full_stem] += 1
                    else:
                        dxf_candidates.append(candidate)
                        dxf_name_counts[full_stem] += 1

        step_map, step_revision_stats = self._select_latest_revisions(step_candidates)
        dxf_map, dxf_revision_stats = self._select_latest_revisions(dxf_candidates)

        pair_keys = sorted(set(step_map) & set(dxf_map))
        pair_manifest = build_pair_manifest(step_map.values(), dxf_map.values())
        leaf_components = []
        leaf_component_errors = []
        for step_path in sorted(step_map.values(), key=str.lower):
            try:
                leaf_components.extend(extract_leaf_components(step_path))
            except Exception as exc:
                leaf_component_errors.append({"step_path": step_path, "error": str(exc)})
        component_manifest = build_component_pair_manifest(
            pair_manifest["unpaired_step_paths"],
            pair_manifest["unpaired_dxf_paths"],
            pre_extracted_components=leaf_components,
        )
        component_manifest["errors"].extend(leaf_component_errors)
        component_manifest["statistics"]["read_error_count"] = len(component_manifest["errors"])
        pair_manifest["component_manifest"] = component_manifest
        pair_manifest["verified_pairs"].extend(component_manifest["verified_pairs"])
        pair_manifest["statistics"]["component_pair_count"] = len(component_manifest["verified_pairs"])
        pair_manifest["statistics"]["total_verification_pair_count"] = len(pair_manifest["verified_pairs"])
        return {
            "step_map": step_map,
            "dxf_map": dxf_map,
            "pair_keys": pair_keys,
            "verified_pairs": pair_manifest["verified_pairs"],
            "pair_manifest": pair_manifest,
            "leaf_components": leaf_components,
            "duplicate_steps": sum(max(0, count - 1) for count in step_name_counts.values()),
            "duplicate_dxfs": sum(max(0, count - 1) for count in dxf_name_counts.values()),
            "revision_selection": {
                "step": step_revision_stats,
                "dxf": dxf_revision_stats,
            },
        }

    @staticmethod
    def _tolerance_is_plausible(dim: ExtractedDimension) -> bool:
        return _tolerance_is_plausible_dimension(dim)

    @staticmethod
    def _evidence_key(dxf_path: str, dim: ExtractedDimension) -> Tuple[str, str]:
        return os.path.normcase(os.path.abspath(dxf_path)), str(dim.entity_handle or "")

    @classmethod
    def _dimension_geometry_signature(cls, dim: ExtractedDimension) -> Tuple[Any, ...]:
        """Identify only dimensions that describe the same value at the same geometry.

        Equal nominal values at different positions or orientations are intentionally
        retained; engineering drawings routinely repeat a value on different features.
        """
        if dim.dimension_category in {"DIAMETER", "RADIUS"}:
            point_names = ("defpoint", "defpoint4")
        else:
            point_names = ("defpoint2", "defpoint3")
        endpoints = []
        for name in point_names:
            point = (dim.points or {}).get(name) or [0.0, 0.0]
            endpoints.append(tuple(round(float(value), 3) for value in point[:2]))
        return (
            dim.dimension_category,
            round(float(dim.nominal_value), 6),
            cls._tolerance_signature(dim.tolerance_config),
            tuple(sorted(endpoints)),
        )

    @classmethod
    def _deduplicate_dimensions(cls, dimensions: Sequence[ExtractedDimension]) -> Tuple[List[ExtractedDimension], int]:
        unique: Dict[Tuple[Any, ...], ExtractedDimension] = {}
        suppressed = 0
        for dim in dimensions:
            signature = cls._dimension_geometry_signature(dim)
            existing = unique.get(signature)
            if existing is None:
                unique[signature] = dim
                continue
            if dim.entity_handle:
                existing.duplicate_entity_handles.append(dim.entity_handle)
            suppressed += 1
        return list(unique.values()), suppressed

    @staticmethod
    def _case_id(prefix: str, dxf_path: str, suffix: str) -> str:
        digest = hashlib.sha1(os.path.normcase(os.path.abspath(dxf_path)).encode("utf-8")).hexdigest()[:8]
        stem = os.path.splitext(os.path.basename(dxf_path))[0]
        safe_suffix = str(suffix or "NOHANDLE").replace(" ", "_")
        return f"{prefix}_{stem}_{digest}_{safe_suffix}"

    @staticmethod
    def _raw_feature_identity(dim: ExtractedDimension) -> Tuple[str, str, Dict[str, float], str]:
        category = dim.dimension_category
        if category == "DIAMETER":
            nominal = {"diameter": dim.nominal_value}
        elif category == "RADIUS":
            nominal = {"radius": dim.nominal_value}
        elif category == "CHAMFER":
            nominal = {"chamfer_height": dim.nominal_value}
        elif category == "ANGULAR":
            nominal = {"angle": dim.nominal_value}
        else:
            nominal = {"length": dim.nominal_value}
        return "unresolved_feature", f"UNRESOLVED_{category}", nominal, "AUTO_EXTRACTED"

    def _make_raw_case(self, dxf_path: str, dim: ExtractedDimension) -> ToleranceCase:
        feature_type, role, nominal, status = self._raw_feature_identity(dim)
        family = FeatureCaseBase.infer_product_family(os.path.basename(dxf_path))
        checks = [
            "native_dxf_dimension_entity",
            "explicit_tolerance_present",
            "nominal_and_deviation_range_valid",
        ]
        inference = dict(dim.feature_inference_2d or {})
        inferred_candidates = inference.get("candidates", [])
        candidates = [
            item.get("feature_type")
            for item in inferred_candidates
            if item.get("feature_type") in CANONICAL_FEATURE_TYPES
        ] or candidate_feature_types_for_dimension(dim.dimension_category)
        return ToleranceCase(
            case_id=self._case_id("DXF2", dxf_path, dim.entity_handle),
            part_type="GENERAL",
            feature_type=feature_type,
            inferred_role=role,
            nominal_dimensions=nominal,
            neighbor_types=[],
            boundary_position="UNKNOWN",
            tolerance_config=dim.tolerance_config,
            confidence=0.92 if status == "AUTO_VERIFIED" else 0.78,
            evidence_source=os.path.basename(dxf_path),
            description=(
                f"Company drawing {os.path.basename(dxf_path)} entity {dim.entity_handle}: "
                f"{dim.dimension_category} {dim.nominal_value:g} with explicit tolerance."
            ),
            verification_status=status,
            source_metadata={
                "schema_version": 2,
                "drawing_file": os.path.basename(dxf_path),
                "dxf_path": os.path.abspath(dxf_path),
                "product_family": family,
                "entity_handle": dim.entity_handle,
                "duplicate_entity_handles": list(dim.duplicate_entity_handles),
                "source_entity_type": dim.source_entity_type,
                "dimension_category": dim.dimension_category,
                "raw_text": dim.raw_text,
                "layer": dim.layer,
                "points": dim.points,
                "parser_confidence": dim.extraction_confidence,
                "verification_method": "DXF_NATIVE_ENTITY",
                "verification_checks": checks,
                "feature_taxonomy": "FeatureGraphExtractor",
                "candidate_feature_types": candidates,
                "feature_inference_2d": inference,
                "feature_identity_verified": False,
                "functional_role_verified": False,
            },
        )

    @staticmethod
    def _node_targets(node: FeatureNode) -> Iterable[Tuple[str, float, Tuple[str, ...]]]:
        nominal = node.nominal or {}
        if node.feature_type in {"shaft_segment", "hole"}:
            yield "diameter", float(nominal.get("diameter", 0.0) or 0.0), ("DIAMETER",)
            yield "length", float(nominal.get("length", 0.0) or 0.0), ("LINEAR",)
        elif node.feature_type == "retaining_ring_groove":
            yield "groove_diameter", float(nominal.get("groove_diameter", 0.0) or 0.0), ("DIAMETER",)
            yield "groove_width", float(nominal.get("groove_width", 0.0) or 0.0), ("LINEAR",)
        elif node.feature_type == "pilot_chamfer":
            yield "chamfer_height", float(nominal.get("chamfer_height", 0.0) or 0.0), ("CHAMFER", "LINEAR")
            yield "angle", float(nominal.get("angle", 0.0) or 0.0), ("ANGULAR",)
        elif node.feature_type == "transition_fillet":
            yield "radius", float(nominal.get("radius", 0.0) or 0.0), ("RADIUS",)
        elif node.feature_type == "locating_shoulder":
            yield "diameter", float(nominal.get("diameter", 0.0) or 0.0), ("DIAMETER",)
            yield "step_height", float(nominal.get("step_height", nominal.get("length", 0.0)) or 0.0), ("LINEAR",)
        elif node.feature_type in {"overall_dimension", "composite_planar_span"}:
            yield "length", float(nominal.get("length", 0.0) or 0.0), ("LINEAR",)
        elif node.feature_type == "annular_rim":
            yield "outer_diameter", float(nominal.get("outer_diameter", 0.0) or 0.0), ("DIAMETER",)
            yield "inner_diameter", float(nominal.get("inner_diameter", 0.0) or 0.0), ("DIAMETER",)
            yield "length", float(nominal.get("length", 0.0) or 0.0), ("LINEAR",)

    @staticmethod
    def _candidate_semantic_prior(dim, node: FeatureNode, evidence: Dict[str, Any]) -> float:
        """Small deterministic tie-breaker after full geometry verification.

        Duplicate canonical records may describe the same cylindrical surface
        as both a shaft and a step.  Visible outer profiles should prefer the
        functional outer feature; hidden circular profiles should prefer the
        bore.  The prior is intentionally too small to rescue failed geometry.
        """
        if not evidence.get("passed"):
            return 0.0
        category = str(getattr(dim, "dimension_category", "") or "").upper()
        association = dict(evidence.get("association") or {})
        edges = list(association.get("dimension_target_edges") or [])
        if category == "LINEAR":
            exact_edges = [
                item for item in edges
                if str(item.get("relation") or "") == "EXACT_ATTACHMENT"
            ]
            if len(exact_edges) < 2:
                return 0.0
            hidden = all(
                "HID" in str(item.get("layer") or "").upper()
                or "HIDDEN" in str(item.get("linetype") or "").upper()
                for item in exact_edges
            )
            if hidden and node.feature_type == "hole":
                return 0.06
            if not hidden and node.feature_type in {"locating_shoulder", "composite_planar_span"}:
                return 0.06
            return 0.0
        if category != "DIAMETER":
            return 0.0
        exact_circles = [
            item for item in edges
            if str(item.get("relation") or "") == "EXACT_ATTACHMENT"
            and str(item.get("entity_type") or "") in {"CIRCLE", "ARC"}
        ]
        if not exact_circles:
            return 0.0
        hidden = all(
            "HID" in str(item.get("layer") or "").upper()
            or "HIDDEN" in str(item.get("linetype") or "").upper()
            for item in exact_circles
        )
        role = str(node.inferred_role or "").upper()
        if hidden and node.feature_type == "annular_rim":
            return 0.09
        if hidden and node.feature_type == "hole":
            return 0.06
        if not hidden and node.feature_type == "annular_rim":
            return 0.09
        if not hidden and (node.feature_type == "shaft_segment" or "OUTER" in role):
            return 0.06
        return 0.0

    @staticmethod
    def _match_threshold(value: float) -> float:
        # STEP tessellation/chamfer recovery can turn a nominal 3.000 mm end
        # step into 2.994 mm.  Spatial verification decides between the wider
        # candidate set, so keep a conservative 0.01 mm geometry allowance.
        return max(0.01, min(0.02, abs(value) * 0.0005))

    @staticmethod
    def _tolerance_signature(config: Dict[str, Any]) -> str:
        return json.dumps(config or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _load_step_shape(step_path: str):
        reader = STEPControl_Reader()
        if reader.ReadFile(step_path) != IFSelect_RetDone:
            return None
        reader.TransferRoots()
        return reader.OneShape()

    def _load_feature_graph(self, step_path: str):
        shape = self._load_step_shape(step_path)
        return self.frg_extractor.build_graph(shape) if shape is not None else None

    def _verify_pair(
        self,
        step_path: str,
        dxf_path: str,
        dimensions: Sequence[ExtractedDimension],
        pair_evidence: Optional[Dict[str, Any]] = None,
        collect_diagnostics: bool = False,
    ) -> Tuple[List[Tuple[ToleranceCase, List[Tuple[str, str]]]], Dict[str, Any]]:
        component_label_entry = str((pair_evidence or {}).get("component_label_entry") or "")
        shape = (
            load_component_shape(step_path, component_label_entry)
            if component_label_entry
            else self._load_step_shape(step_path)
        )
        graph = self.frg_extractor.build_graph(shape) if shape is not None else None
        if graph is None or not graph.nodes:
            return [], {"pair_load_failures": 1}
        for composite_node in build_annular_rim_features(graph):
            graph.add_node(composite_node)
        requested_linear_lengths = [
            float(dim.nominal_value)
            for dim in dimensions
            if str(dim.dimension_category or "").upper() == "LINEAR"
        ]
        atomic_linear_values = [
            abs(float(value))
            for node in graph.nodes
            for _field, value, categories in self._node_targets(node)
            if "LINEAR" in categories and abs(float(value)) > 1e-9
        ]
        requested_linear_lengths = [
            value for value in requested_linear_lengths
            if not any(
                abs(abs(float(value)) - atomic) <= self._match_threshold(value)
                for atomic in atomic_linear_values
            )
        ]
        for composite_node in build_planar_span_features(
            shape, graph, requested_linear_lengths
        ):
            graph.add_node(composite_node)
        try:
            modelspace = ezdxf.readfile(dxf_path).modelspace()
            geometry_verifier = ProjectionGeometryVerifier(
                modelspace,
                shape=shape,
                feature_nodes=graph.nodes,
                locked_view_registration=(pair_evidence or {}).get("locked_view_registration"),
                allow_secondary_view_search=bool(
                    (pair_evidence or {}).get("allow_secondary_view_search", False)
                ),
                secondary_dxf_regions=(pair_evidence or {}).get("secondary_dxf_regions"),
            )
        except Exception:
            return [], {"projection_verifier_failures": 1}

        diagnostic_order = [self._evidence_key(dxf_path, dim) for dim in dimensions]
        dimension_diagnostics: Dict[Tuple[str, str], Dict[str, Any]] = {}
        projection_overlays: Dict[str, Dict[str, Any]] = {}
        if collect_diagnostics:
            for dim in dimensions:
                dimension_key = self._evidence_key(dxf_path, dim)
                association = geometry_verifier.structure.analyze_dimension(dim)
                geometry_verifier._association_cache[id(dim)] = association
                observed_anchor = geometry_verifier._dimension_anchor(dim, association)
                dimension_diagnostics[dimension_key] = {
                    "entity_handle": str(dim.entity_handle or ""),
                    "dimension_category": dim.dimension_category,
                    "nominal_value": dim.nominal_value,
                    "status": "PENDING",
                    "candidate_count": 0,
                    "association": association,
                    "observed_dxf_anchor": (
                        [round(float(value), 4) for value in observed_anchor]
                        if observed_anchor is not None
                        else None
                    ),
                    "candidates": [],
                }

        def update_diagnostic_candidate(
            dim: ExtractedDimension,
            node: FeatureNode,
            field_name: str,
            difference: float,
            threshold: float,
            evidence: Optional[Dict[str, Any]] = None,
        ) -> None:
            if not collect_diagnostics:
                return
            diagnostic = dimension_diagnostics[self._evidence_key(dxf_path, dim)]
            candidates = diagnostic["candidates"]
            candidate = next(
                (
                    item for item in candidates
                    if item["feature_id"] == node.id and item["nominal_field"] == field_name
                ),
                None,
            )
            if candidate is None:
                candidate = {
                    "feature_id": node.id,
                    "feature_type": node.feature_type,
                    "nominal_field": field_name,
                    "value_difference_mm": round(difference, 6),
                    "match_threshold_mm": round(threshold, 6),
                }
                candidates.append(candidate)
            if evidence is not None:
                association = dict(evidence.get("association") or {})
                localization = dict(evidence.get("feature_localization") or {})
                candidate["geometry_verification"] = {
                    "method": evidence.get("method"),
                    "status": evidence.get("status"),
                    "passed": bool(evidence.get("passed")),
                    "score": evidence.get("score"),
                    "checks": dict(evidence.get("checks") or {}),
                    "association": {
                        key: association.get(key)
                        for key in (
                            "association_status",
                            "association_confidence",
                            "primary_view_id",
                            "attached_geometry_handles",
                            "common_attachment_handles",
                            "dimension_target_edges",
                        )
                    },
                    "view_registration": dict(evidence.get("view_registration") or {}),
                    "feature_localization": {
                        key: localization.get(key)
                        for key in (
                            "passed",
                            "score",
                            "status",
                            "step_view",
                            "node_center_3d",
                            "projected_step_point",
                            "expected_dxf_point",
                            "observed_dxf_anchor",
                            "expected_normalized_point",
                            "observed_normalized_anchor",
                            "normalized_distance",
                            "along_axis_error",
                            "cross_axis_error",
                        )
                    },
                }

        node_by_id = {str(node.id): node for node in graph.nodes}

        def build_registered_step_projection(registration: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            view_name = str(registration.get("step_view") or "")
            transform_payload = registration.get("transform")
            view = geometry_verifier.step_views.get(view_name)
            if not view or not isinstance(transform_payload, dict):
                return None
            transform = SimilarityTransform2D(
                scale=float(transform_payload.get("scale", 1.0) or 1.0),
                rotation_degrees=float(transform_payload.get("rotation_degrees", 0.0) or 0.0),
                translation=tuple(transform_payload.get("translation") or (0.0, 0.0)),
                mirrored=bool(transform_payload.get("mirrored", False)),
            )
            lines: List[Dict[str, Any]] = []
            circles: List[Dict[str, Any]] = []
            for visibility in ("visible", "hidden"):
                for edge in view.get(visibility) or []:
                    if edge.get("type") == "line" and edge.get("p1") and edge.get("p2"):
                        p1 = transform.apply_point(tuple(edge["p1"][:2]))
                        p2 = transform.apply_point(tuple(edge["p2"][:2]))
                        lines.append({
                            "p1": [round(float(value), 4) for value in p1],
                            "p2": [round(float(value), 4) for value in p2],
                            "visibility": visibility,
                        })
                    elif edge.get("type") == "circle" and edge.get("center") and edge.get("radius"):
                        center = transform.apply_point(tuple(edge["center"][:2]))
                        circles.append({
                            "center": [round(float(value), 4) for value in center],
                            "radius": round(abs(float(edge["radius"])) * abs(transform.scale), 4),
                            "visibility": visibility,
                        })
            if len(lines) > 1200:
                stride = len(lines) / 1200.0
                lines = [lines[min(int(index * stride), len(lines) - 1)] for index in range(1200)]
            return {
                "step_view": view_name,
                "dxf_view_id": registration.get("dxf_view_id"),
                "lines": lines,
                "circles": circles,
            }

        def build_feature_footprint(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            node = node_by_id.get(str(candidate.get("feature_id") or ""))
            evidence = candidate.get("geometry_verification") or {}
            registration = evidence.get("view_registration") or {}
            view_name = str(registration.get("step_view") or "")
            view = geometry_verifier.step_views.get(view_name)
            if node is None or not view or not isinstance(registration.get("transform"), dict):
                return None
            footprint = FeatureFootprintProjector().project(
                node,
                view_name,
                view,
                registration.get("transform"),
            )
            return footprint.to_dict(include_points=True)

        allowed_dxf_views = {
            str(value)
            for value in ((pair_evidence or {}).get("matched_dxf_view_ids") or [])
            if value
        }
        view_filtered_dimensions = 0
        if allowed_dxf_views and not bool(
            (pair_evidence or {}).get("allow_secondary_view_search", False)
        ):
            retained_dimensions = []
            for dim in dimensions:
                association = geometry_verifier.structure.analyze_dimension(dim)
                geometry_verifier._association_cache[id(dim)] = association
                if association.get("primary_view_id") in allowed_dxf_views:
                    retained_dimensions.append(dim)
                else:
                    view_filtered_dimensions += 1
                    if collect_diagnostics:
                        dimension_diagnostics[self._evidence_key(dxf_path, dim)]["status"] = "OUTSIDE_MATCHED_VIEW"
            dimensions = retained_dimensions

        unique_matches: List[Tuple[ExtractedDimension, FeatureNode, str, float, float]] = []
        ambiguous_candidates: List[Dict[str, Any]] = []
        geometry_cache: Dict[Tuple[Tuple[str, str], str, str], Dict[str, Any]] = {}
        ambiguous = 0
        geometry_resolved_ambiguous = 0
        no_match = 0
        for dim in dimensions:
            candidates: List[Tuple[FeatureNode, str, float, float]] = []
            for node in graph.nodes:
                for field_name, target, categories in self._node_targets(node):
                    if target <= 0.0 or dim.dimension_category not in categories:
                        continue
                    difference = abs(dim.nominal_value - target)
                    threshold = self._match_threshold(target)
                    if difference <= threshold:
                        candidates.append((node, field_name, difference, threshold))
            candidate_keys = {(item[0].id, item[1]) for item in candidates}
            if collect_diagnostics:
                diagnostic = dimension_diagnostics[self._evidence_key(dxf_path, dim)]
                diagnostic["candidate_count"] = len(candidate_keys)
                for node, field_name, difference, threshold in candidates:
                    update_diagnostic_candidate(dim, node, field_name, difference, threshold)
            if len(candidate_keys) == 1:
                node, field_name, difference, threshold = min(candidates, key=lambda item: item[2])
                unique_matches.append((dim, node, field_name, difference, threshold))
            elif candidate_keys:
                ambiguous += 1
                best_by_key: Dict[Tuple[str, str], Tuple[FeatureNode, str, float, float]] = {}
                for candidate in candidates:
                    key = (candidate[0].id, candidate[1])
                    if key not in best_by_key or candidate[2] < best_by_key[key][2]:
                        best_by_key[key] = candidate
                dimension_key = self._evidence_key(dxf_path, dim)
                for node, field_name, difference, threshold in best_by_key.values():
                    evidence = geometry_verifier.verify(dim, node, field_name)
                    update_diagnostic_candidate(dim, node, field_name, difference, threshold, evidence)
                    cache_key = (dimension_key, node.id, field_name)
                    geometry_cache[cache_key] = evidence
                    primary_view = str((evidence.get("association") or {}).get("primary_view_id") or "NONE")
                    registration_score = float(
                        (evidence.get("view_registration") or {}).get("score", 0.0) or 0.0
                    )
                    localization_score = float(
                        (evidence.get("feature_localization") or {}).get("score", 0.0) or 0.0
                    )
                    overall_score = float(evidence.get("score", 0.0) or 0.0)
                    semantic_prior = self._candidate_semantic_prior(dim, node, evidence)
                    # Candidate identity is primarily a spatial question.  A
                    # high overall verification score must not hide a weak
                    # feature location merely because the nominal value and
                    # local dimension syntax agree.
                    assignment_score = (
                        0.68 * localization_score
                        + 0.22 * registration_score
                        + 0.10 * overall_score
                        + semantic_prior
                    )
                    ambiguous_candidates.append({
                        "dimension_key": "|".join(dimension_key),
                        "feature_key": f"{node.id}|{field_name}|{primary_view}",
                        "score": round(assignment_score, 6),
                        "score_components": {
                            "feature_localization": round(localization_score, 6),
                            "view_registration": round(registration_score, 6),
                            "overall_verification": round(overall_score, 6),
                            "semantic_prior": round(semantic_prior, 6),
                        },
                        "passed": bool(evidence.get("passed")),
                        "payload": (dim, node, field_name, difference, threshold),
                    })
            else:
                no_match += 1
                if collect_diagnostics:
                    dimension_diagnostics[self._evidence_key(dxf_path, dim)]["status"] = "NO_3D_NOMINAL_CANDIDATE"

        assignment = GlobalFeatureAssignmentResolver().resolve(ambiguous_candidates)
        for selected in assignment["selected"]:
            unique_matches.append(selected["payload"])
            geometry_resolved_ambiguous += 1
        ambiguous = max(0, ambiguous - geometry_resolved_ambiguous)

        grouped: Dict[Tuple[str, str], List[Tuple[ExtractedDimension, FeatureNode, float, float]]] = defaultdict(list)
        for dim, node, field_name, difference, threshold in unique_matches:
            grouped[(node.id, field_name)].append((dim, node, difference, threshold))

        verified: List[Tuple[ToleranceCase, List[Tuple[str, str]]]] = []
        conflicts = 0
        insufficient_geometry = 0
        rejected_evidence: List[Dict[str, Any]] = []
        for (_node_id, field_name), matches in grouped.items():
            matches.sort(key=lambda item: item[2])
            geometry_results = [
                (
                    match,
                    geometry_cache.get(
                        (self._evidence_key(dxf_path, match[0]), match[1].id, field_name)
                    ) or geometry_verifier.verify(match[0], match[1], field_name),
                )
                for match in matches
            ]
            if collect_diagnostics:
                for (candidate_dim, candidate_node, difference, threshold), evidence in geometry_results:
                    update_diagnostic_candidate(
                        candidate_dim,
                        candidate_node,
                        field_name,
                        difference,
                        threshold,
                        evidence,
                    )
            accepted_geometry = [item for item in geometry_results if item[1].get("passed")]
            rejected_geometry = [item for item in geometry_results if not item[1].get("passed")]
            for (rejected_dim, rejected_node, difference, threshold), evidence in rejected_geometry:
                if collect_diagnostics:
                    dimension_diagnostics[self._evidence_key(dxf_path, rejected_dim)]["status"] = "INSUFFICIENT_GEOMETRY_EVIDENCE"
                rejected_evidence.append({
                    "evidence_key": list(self._evidence_key(dxf_path, rejected_dim)),
                    "candidate_feature_id": rejected_node.id,
                    "candidate_feature_type": rejected_node.feature_type,
                    "candidate_nominal_field": field_name,
                    "value_difference_mm": round(difference, 6),
                    "match_threshold_mm": round(threshold, 6),
                    "geometry_verification": evidence,
                })
            if not accepted_geometry:
                insufficient_geometry += len(matches)
                continue
            signatures = {
                self._tolerance_signature(item[0][0].tolerance_config)
                for item in accepted_geometry
            }
            if len(signatures) != 1:
                conflicts += len(accepted_geometry)
                if collect_diagnostics:
                    for (dim, node, difference, threshold), evidence in accepted_geometry:
                        dimension_diagnostics[self._evidence_key(dxf_path, dim)]["status"] = "CONFLICTING_TOLERANCE_DEFINITIONS"
                        update_diagnostic_candidate(
                            dim,
                            node,
                            field_name,
                            difference,
                            threshold,
                            evidence,
                        )
                continue
            accepted_geometry.sort(key=lambda item: (-float(item[1].get("score", 0.0)), item[0][2]))
            (primary_dim, node, difference, threshold), geometry_evidence = accepted_geometry[0]
            if node.feature_type not in CANONICAL_FEATURE_TYPES:
                conflicts += len(matches)
                continue
            accepted_matches = [item[0] for item in accepted_geometry]
            evidence_keys = [self._evidence_key(dxf_path, item[0]) for item in accepted_matches]
            supporting_handles = sorted({
                handle
                for item in accepted_matches
                for handle in [item[0].entity_handle, *item[0].duplicate_entity_handles]
                if handle
            })
            pair_method = str((pair_evidence or {}).get("pair_method") or "EXACT_FILENAME")
            pair_checks = list((pair_evidence or {}).get("verification_checks") or ["exact_step_dxf_filename"])
            confidence_ceiling = {
                "EXACT_FILENAME": 0.97,
                "EMBEDDED_PART_EXACT_REVISION": 0.96,
                "XCAF_COMPONENT_EXACT_REVISION": 0.96,
                "XCAF_COMPONENT_UNVERSIONED_LATEST_DXF": 0.93,
                GlobalGeometryPairSearcher.METHOD: 0.94,
            }
            maximum_confidence = confidence_ceiling.get(pair_method, 0.92)
            confidence = max(0.90, min(maximum_confidence, maximum_confidence - (difference / max(threshold, 1e-9)) * 0.06))
            if pair_method == "EXACT_FILENAME":
                evidence_label = "Exact-name STEP/DXF"
            elif pair_method == GlobalGeometryPairSearcher.METHOD:
                evidence_label = "Global geometry-matched component/DXF"
            else:
                evidence_label = "Same-part same-revision STEP/DXF"
            case = ToleranceCase(
                case_id=self._case_id("HIST2", dxf_path, f"{node.id}_{field_name}"),
                part_type=graph.part_type,
                feature_type=node.feature_type,
                inferred_role=f"{node.feature_type.upper()}_{field_name.upper()}",
                nominal_dimensions=node.nominal,
                neighbor_types=node.neighbor_types,
                boundary_position=node.boundary_position,
                tolerance_config=primary_dim.tolerance_config,
                confidence=round(confidence, 3),
                evidence_source=os.path.basename(dxf_path),
                description=(
                    f"{evidence_label} evidence: {node.feature_type} {field_name} "
                    f"{primary_dim.nominal_value:g} in {os.path.basename(dxf_path)}."
                ),
                verification_status="AUTO_VERIFIED",
                source_metadata={
                    "schema_version": 2,
                    "drawing_file": os.path.basename(dxf_path),
                    "dxf_path": os.path.abspath(dxf_path),
                    "step_path": os.path.abspath(step_path),
                    "product_family": FeatureCaseBase.infer_product_family(os.path.basename(dxf_path)),
                    "entity_handle": primary_dim.entity_handle,
                    "supporting_entity_handles": supporting_handles,
                    "source_entity_type": primary_dim.source_entity_type,
                    "dimension_category": primary_dim.dimension_category,
                    "raw_text": primary_dim.raw_text,
                    "layer": primary_dim.layer,
                    "points": primary_dim.points,
                    "matched_feature_id": node.id,
                    "matched_nominal_field": field_name,
                    "matched_feature_center_axial": node.center_axial,
                    "matched_feature_source_info": node.source_info,
                    "value_difference_mm": round(difference, 6),
                    "match_threshold_mm": round(threshold, 6),
                    "pair_id": (pair_evidence or {}).get("pair_id"),
                    "part_number": (pair_evidence or {}).get("part_number"),
                    "revision": (pair_evidence or {}).get("revision"),
                    "pair_method": pair_method,
                    "component_name": (pair_evidence or {}).get("component_name"),
                    "component_revision": (pair_evidence or {}).get("component_revision"),
                    "component_label_entry": (pair_evidence or {}).get("component_label_entry"),
                    "component_fingerprint": (pair_evidence or {}).get("component_fingerprint"),
                    "matched_dxf_view_ids": (pair_evidence or {}).get("matched_dxf_view_ids", []),
                    "matched_step_view": (pair_evidence or {}).get("matched_step_view"),
                    "global_geometry_score": (pair_evidence or {}).get("global_geometry_score"),
                    "global_geometry_pair_evidence": (pair_evidence or {}).get("geometry_search_evidence"),
                    "verification_method": f"{pair_method}_UNIQUE_STEP_DXF_VALUE_TYPE_MATCH",
                    "geometry_verification": geometry_evidence,
                    "feature_taxonomy": "web_feature_records_v1",
                    "canonical_feature_id": node.id,
                    "verification_checks": [
                        *pair_checks,
                        "native_dxf_dimension_entity",
                        "explicit_tolerance_present",
                        "dimension_type_matches_feature_type",
                        "nominal_value_matches_step_geometry",
                        "unique_step_feature_candidate",
                        "dxf_dimension_geometry_attachment",
                        "local_2d_feature_semantics",
                        "step_hlr_projection_signature",
                        "drawing_view_step_projection_registration",
                        "dimension_matches_projected_feature_location",
                        "dxf_dimension_endpoints_to_brep_topology",
                        "consistent_duplicate_dimension_tolerances",
                    ],
                    "feature_identity_verified": True,
                    "functional_role_verified": False,
                },
            )
            verified.append((case, evidence_keys))
            if collect_diagnostics:
                for matched_dim, _matched_node, _difference, _threshold in accepted_matches:
                    dimension_diagnostics[self._evidence_key(dxf_path, matched_dim)]["status"] = "AUTO_VERIFIED"

        if collect_diagnostics:
            for diagnostic in dimension_diagnostics.values():
                if diagnostic["status"] == "PENDING":
                    diagnostic["status"] = "AMBIGUOUS_3D_FEATURE_CANDIDATES"
                diagnostic["candidates"].sort(
                    key=lambda item: (
                        -float((item.get("geometry_verification") or {}).get("score", 0.0) or 0.0),
                        float(item.get("value_difference_mm", 0.0) or 0.0),
                        str(item.get("feature_id") or ""),
                    )
                )
                diagnostic["candidates"] = diagnostic["candidates"][:8]
                for candidate in diagnostic["candidates"]:
                    footprint = build_feature_footprint(candidate)
                    if footprint is not None:
                        candidate["projected_feature_footprint"] = footprint
                first_registered = next(
                    (
                        candidate.get("geometry_verification", {}).get("view_registration")
                        for candidate in diagnostic["candidates"]
                        if candidate.get("geometry_verification", {}).get("view_registration", {}).get("transform")
                    ),
                    None,
                )
                if first_registered:
                    overlay_key_payload = {
                        "dxf_view_id": first_registered.get("dxf_view_id"),
                        "step_view": first_registered.get("step_view"),
                        "transform": first_registered.get("transform"),
                    }
                    overlay_id = hashlib.sha1(
                        json.dumps(overlay_key_payload, sort_keys=True).encode("utf-8")
                    ).hexdigest()[:16]
                    if overlay_id not in projection_overlays:
                        overlay = build_registered_step_projection(first_registered)
                        if overlay is not None:
                            projection_overlays[overlay_id] = overlay
                    if overlay_id in projection_overlays:
                        diagnostic["registered_step_projection_id"] = overlay_id

        return verified, {
            "unique_dimension_matches": len(unique_matches),
            "ambiguous_dimension_matches": ambiguous,
            "geometry_resolved_ambiguous_matches": geometry_resolved_ambiguous,
            "unmatched_dimensions": no_match,
            "conflicting_tolerance_matches": conflicts,
            "insufficient_geometry_evidence": insufficient_geometry,
            "global_view_filtered_dimensions": view_filtered_dimensions,
            "_rejected_evidence": rejected_evidence,
            "_dimension_diagnostics": [
                dimension_diagnostics[key] for key in diagnostic_order
                if key in dimension_diagnostics
            ],
            "_projection_overlays": projection_overlays,
        }

    @staticmethod
    def _atomic_write_json(path: str, payload: Any) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        with open(temp_path, "r", encoding="utf-8") as handle:
            json.load(handle)
        os.replace(temp_path, path)

    def rebuild_database(
        self,
        search_dirs: Sequence[str],
        output_path: Optional[str] = None,
        report_path: Optional[str] = None,
        manifest_path: Optional[str] = None,
        max_pairs: Optional[int] = None,
        max_dxf_files: Optional[int] = None,
        workers: int = 4,
        enable_global_geometry: bool = True,
        global_max_components: Optional[int] = None,
        global_retrieval_top_k: int = 12,
        global_refine_top_drawings: int = 8,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        discovery = self.discover_sources(search_dirs)
        dxf_map: Dict[str, str] = discovery["dxf_map"]
        step_map: Dict[str, str] = discovery["step_map"]
        verified_pairs: List[Dict[str, Any]] = list(discovery["verified_pairs"])

        dimensions_by_path: Dict[str, List[ExtractedDimension]] = {}
        raw_cases: Dict[Tuple[str, str], ToleranceCase] = {}
        extraction_statuses: Counter[str] = Counter()
        inference_statuses: Counter[str] = Counter()
        inferred_feature_types: Counter[str] = Counter()
        auto_inferred_examples: List[Dict[str, Any]] = []
        parse_errors = 0
        duplicate_dimension_entities_suppressed = 0
        dxf_paths = sorted(dxf_map.values())
        if max_dxf_files is not None:
            dxf_paths = dxf_paths[:max_dxf_files]
        with ProcessPoolExecutor(max_workers=max(1, workers)) as executor:
            extracted_results = executor.map(_extract_dxf_worker, dxf_paths, chunksize=1)
            for index, (dxf_path, extracted_dicts) in enumerate(extracted_results, start=1):
                try:
                    extracted = [ExtractedDimension(**item) for item in extracted_dicts]
                    extracted, suppressed = self._deduplicate_dimensions(extracted)
                    duplicate_dimension_entities_suppressed += suppressed
                    dimensions_by_path[os.path.normcase(os.path.abspath(dxf_path))] = extracted
                    extraction_statuses.update(item.validation_status for item in extracted)
                    for dim in extracted:
                        if self._tolerance_is_plausible(dim):
                            raw_cases[self._evidence_key(dxf_path, dim)] = self._make_raw_case(dxf_path, dim)
                            inference_status = str((dim.feature_inference_2d or {}).get("status", "NOT_RUN"))
                            inference_statuses[inference_status] += 1
                            inferred_type = (dim.feature_inference_2d or {}).get("feature_type")
                            if inferred_type:
                                inferred_feature_types[str(inferred_type)] += 1
                                if len(auto_inferred_examples) < 20:
                                    top_candidate = ((dim.feature_inference_2d or {}).get("candidates") or [{}])[0]
                                    structure = (dim.feature_inference_2d or {}).get("structure_context") or {}
                                    auto_inferred_examples.append({
                                        "drawing_file": os.path.basename(dxf_path),
                                        "entity_handle": dim.entity_handle,
                                        "dimension_category": dim.dimension_category,
                                        "nominal_value": dim.nominal_value,
                                        "feature_type": inferred_type,
                                        "confidence": (dim.feature_inference_2d or {}).get("confidence"),
                                        "evidence": top_candidate.get("evidence", []),
                                        "association_status": structure.get("association_status"),
                                        "attached_geometry_handles": structure.get("attached_geometry_handles", []),
                                        "cross_view_evidence": structure.get("cross_view_evidence", {}),
                                    })
                except Exception:
                    parse_errors += 1
                if index % 200 == 0:
                    print(f"Parsed {index}/{len(dxf_paths)} DXF drawings...", flush=True)

        global_geometry_manifest: Dict[str, Any] = {
            "schema_version": 1,
            "method": GlobalGeometryPairSearcher.METHOD,
            "verified_pairs": [],
            "candidates": [],
            "statistics": {"status": "DISABLED"},
        }
        if enable_global_geometry:
            excluded_pairs = [
                (
                    pair.get("step_path", ""),
                    pair.get("component_label_entry", ""),
                    pair.get("dxf_path", ""),
                )
                for pair in verified_pairs
                if pair.get("component_label_entry")
            ]
            searcher = GlobalGeometryPairSearcher(
                retrieval_top_k=global_retrieval_top_k,
                refine_top_drawings=global_refine_top_drawings,
            )
            global_geometry_manifest = searcher.search(
                list(step_map.values()),
                dxf_paths,
                max_components=global_max_components,
                excluded_pairs=excluded_pairs,
                pre_extracted_components=discovery.get("leaf_components"),
            )
            known_pair_keys = {
                (
                    os.path.normcase(os.path.abspath(pair.get("step_path", ""))),
                    str(pair.get("component_label_entry") or ""),
                    os.path.normcase(os.path.abspath(pair.get("dxf_path", ""))),
                )
                for pair in verified_pairs
            }
            for pair in global_geometry_manifest.get("verified_pairs", []):
                pair_key = (
                    os.path.normcase(os.path.abspath(pair.get("step_path", ""))),
                    str(pair.get("component_label_entry") or ""),
                    os.path.normcase(os.path.abspath(pair.get("dxf_path", ""))),
                )
                if pair_key not in known_pair_keys:
                    verified_pairs.append(pair)
                    known_pair_keys.add(pair_key)
            discovery["pair_manifest"]["global_geometry_manifest"] = global_geometry_manifest
            discovery["pair_manifest"]["statistics"]["global_geometry_pair_count"] = len(
                global_geometry_manifest.get("verified_pairs", [])
            )
            discovery["pair_manifest"]["statistics"]["total_verification_pair_count"] = len(verified_pairs)
            print(
                "Global geometry search: "
                f"{len(global_geometry_manifest.get('verified_pairs', []))} verified, "
                f"{len(global_geometry_manifest.get('candidates', []))} candidates",
                flush=True,
            )

        total_verification_pair_count = len(verified_pairs)
        if max_pairs is not None:
            verified_pairs = verified_pairs[:max_pairs]

        linked_cases: List[ToleranceCase] = []
        consumed_evidence: set[Tuple[str, str]] = set()
        pair_stats: Counter[str] = Counter()
        pair_errors: List[Dict[str, str]] = []
        for index, pair in enumerate(verified_pairs, start=1):
            step_path = pair["step_path"]
            dxf_path = pair["dxf_path"]
            dimensions = [
                item
                for item in dimensions_by_path.get(os.path.normcase(os.path.abspath(dxf_path)), [])
                if self._tolerance_is_plausible(item)
            ]
            try:
                verified, stats = self._verify_pair(step_path, dxf_path, dimensions, pair_evidence=pair)
                rejected_evidence = list(stats.pop("_rejected_evidence", []))
                pair_stats.update(stats)
                for rejected in rejected_evidence:
                    evidence_key = tuple(rejected.pop("evidence_key"))
                    raw_case = raw_cases.get(evidence_key)
                    if raw_case is not None:
                        raw_case.source_metadata["verification_candidate"] = {
                            key: value for key, value in rejected.items()
                            if key != "geometry_verification"
                        }
                        raw_case.source_metadata["geometry_verification"] = rejected["geometry_verification"]
                        raw_case.source_metadata["feature_identity_verified"] = False
                for case, evidence_keys in verified:
                    linked_cases.append(case)
                    consumed_evidence.update(evidence_keys)
            except Exception as exc:
                pair_errors.append({"pair": pair["pair_id"], "error": str(exc)})
            print(
                f"Verified STEP/DXF pair {index}/{len(verified_pairs)}: {pair['pair_id']}",
                flush=True,
            )

        final_cases = [case for key, case in raw_cases.items() if key not in consumed_evidence]
        final_cases.extend(linked_cases)
        final_cases.sort(key=lambda case: (case.evidence_source.upper(), case.case_id))

        verification_counts = Counter(case.effective_verification_status() for case in final_cases)
        feature_counts = Counter(case.feature_type for case in final_cases)
        def global_pair_summary(pair: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "pair_id": pair.get("pair_id"),
                "status": pair.get("status"),
                "component_name": pair.get("component_name"),
                "component_fingerprint": pair.get("component_fingerprint"),
                "drawing_file": os.path.basename(str(pair.get("dxf_path") or "")),
                "matched_dxf_view_ids": pair.get("matched_dxf_view_ids", []),
                "matched_step_view": pair.get("matched_step_view"),
                "global_geometry_score": pair.get("global_geometry_score"),
                "reciprocal_margin": pair.get("reciprocal_margin"),
            }
        report: Dict[str, Any] = {
            "schema_version": 2,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_directories": [os.path.abspath(path) for path in search_dirs],
            "output_path": os.path.abspath(output_path or self.case_base.db_path),
            "dry_run": dry_run,
            "discovery": {
                "unique_step_files": len(step_map),
                "unique_dxf_files": len(dxf_map),
                "processed_dxf_files": len(dxf_paths),
                "exact_name_pairs": len(discovery["pair_keys"]),
                "conservative_name_pairs": len(discovery["verified_pairs"]),
                "auto_verification_pairs": total_verification_pair_count,
                "processed_pairs": len(verified_pairs),
                "global_geometry_search": global_geometry_manifest.get("statistics", {}),
                "global_geometry_verified_examples": [
                    global_pair_summary(pair)
                    for pair in global_geometry_manifest.get("verified_pairs", [])[:20]
                ],
                "global_geometry_candidate_examples": [
                    global_pair_summary(pair)
                    for pair in global_geometry_manifest.get("candidates", [])[:20]
                ],
                "pair_manifest_statistics": discovery["pair_manifest"]["statistics"],
                "duplicate_step_names": discovery["duplicate_steps"],
                "duplicate_dxf_names": discovery["duplicate_dxfs"],
                "revision_selection": discovery["revision_selection"],
            },
            "extraction": {
                "all_candidate_statuses": dict(extraction_statuses),
                "parse_errors": parse_errors,
                "duplicate_dimension_entities_suppressed": duplicate_dimension_entities_suppressed,
                "native_tolerance_entities": len(raw_cases),
                "consumed_by_feature_links": len(consumed_evidence),
                "feature_inference_2d": {
                    "method": FeatureInference2DEngine.METHOD,
                    "status_counts": dict(inference_statuses),
                    "auto_inferred_feature_counts": dict(inferred_feature_types),
                    "auto_inferred_examples": auto_inferred_examples,
                    "retrieval_eligible": 0,
                },
            },
            "verification": {
                "counts": dict(verification_counts),
                "pair_match_stats": dict(pair_stats),
                "pair_errors": pair_errors,
            },
            "database": {
                "total_cases": len(final_cases),
                "feature_counts": dict(feature_counts),
                "seed_cases": 0,
            },
        }

        if not dry_run:
            target_path = output_path or self.case_base.db_path
            self._atomic_write_json(target_path, [case.to_dict() for case in final_cases])
            self.case_base.db_path = target_path
            self.case_base.cases = final_cases
            self._atomic_write_json(report_path or DEFAULT_REPORT_PATH, report)
            self._atomic_write_json(manifest_path or DEFAULT_MANIFEST_PATH, discovery["pair_manifest"])
        return report

    def scan_and_ingest_directories(self, search_dirs: List[str], max_models: int = 50) -> Dict[str, Any]:
        """Backward-compatible entry point; it now performs a clean v2 rebuild."""
        return self.rebuild_database(search_dirs, max_pairs=max_models)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the company tolerance evidence database")
    parser.add_argument("--source", action="append", dest="sources", help="Source directory; repeat as needed")
    parser.add_argument("--output", default=FeatureCaseBase.DEFAULT_DB_PATH)
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--max-pairs", type=int, default=None)
    parser.add_argument("--max-dxf-files", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--skip-global-geometry", action="store_true")
    parser.add_argument("--global-max-components", type=int, default=None)
    parser.add_argument("--global-retrieval-top-k", type=int, default=12)
    parser.add_argument("--global-refine-top-drawings", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ingestor = HistoricalDataIngestor()
    report = ingestor.rebuild_database(
        args.sources or list(DEFAULT_SOURCE_DIRS),
        output_path=args.output,
        report_path=args.report,
        manifest_path=args.manifest,
        max_pairs=args.max_pairs,
        max_dxf_files=args.max_dxf_files,
        workers=args.workers,
        enable_global_geometry=not args.skip_global_geometry,
        global_max_components=args.global_max_components,
        global_retrieval_top_k=args.global_retrieval_top_k,
        global_refine_top_drawings=args.global_refine_top_drawings,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
