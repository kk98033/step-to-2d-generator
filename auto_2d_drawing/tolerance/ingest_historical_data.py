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
    FeatureGraphExtractor,
    FeatureNode,
    candidate_feature_types_for_dimension,
)
from auto_2d_drawing.tolerance.feature_inference_2d import FeatureInference2DEngine


DEFAULT_SOURCE_DIRS = (
    r"D:\School\力致\力致_ref",
    r"D:\School\力致\new_data",
)
DEFAULT_REPORT_PATH = os.path.join(_current_dir, "data", "feature_case_base_rebuild_report.json")


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
    inference_engine = FeatureInference2DEngine(modelspace)
    for item in dimensions:
        item.feature_inference_2d = inference_engine.infer(item)
    return dxf_path, [item.to_dict() for item in dimensions]


class HistoricalDataIngestor:
    """Create a clean, provenance-first company tolerance database."""

    def __init__(self, case_base: Optional[FeatureCaseBase] = None):
        self.case_base = case_base or FeatureCaseBase()
        self.frg_extractor = FeatureGraphExtractor()
        self.dxf_extractor = DxfToleranceExtractor()

    @staticmethod
    def _normalise_stem(filename: str) -> str:
        stem = os.path.splitext(os.path.basename(filename))[0].lower().strip()
        return stem.replace("new_", "").replace("old_", "")

    @classmethod
    def _revision_identity(cls, filename: str) -> Tuple[str, Optional[int], str]:
        """Return (part identity, revision, normalized full stem)."""
        stem = cls._normalise_stem(filename)
        match = re.match(r"^(.*?)(?:[-_]?r)(\d+)$", stem, re.IGNORECASE)
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
        return {
            "step_map": step_map,
            "dxf_map": dxf_map,
            "pair_keys": pair_keys,
            "duplicate_steps": sum(max(0, count - 1) for count in step_name_counts.values()),
            "duplicate_dxfs": sum(max(0, count - 1) for count in dxf_name_counts.values()),
            "revision_selection": {
                "step": step_revision_stats,
                "dxf": dxf_revision_stats,
            },
        }

    @staticmethod
    def _tolerance_is_plausible(dim: ExtractedDimension) -> bool:
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
            yield "step_height", float(nominal.get("step_height", 0.0) or 0.0), ("LINEAR",)

    @staticmethod
    def _match_threshold(value: float) -> float:
        return max(0.005, min(0.02, abs(value) * 0.0005))

    @staticmethod
    def _tolerance_signature(config: Dict[str, Any]) -> str:
        return json.dumps(config or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _load_feature_graph(self, step_path: str):
        reader = STEPControl_Reader()
        if reader.ReadFile(step_path) != IFSelect_RetDone:
            return None
        reader.TransferRoots()
        return self.frg_extractor.build_graph(reader.OneShape())

    def _verify_pair(
        self,
        step_path: str,
        dxf_path: str,
        dimensions: Sequence[ExtractedDimension],
    ) -> Tuple[List[Tuple[ToleranceCase, List[Tuple[str, str]]]], Dict[str, int]]:
        graph = self._load_feature_graph(step_path)
        if graph is None or not graph.nodes:
            return [], {"pair_load_failures": 1}

        unique_matches: List[Tuple[ExtractedDimension, FeatureNode, str, float, float]] = []
        ambiguous = 0
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
            if len(candidate_keys) == 1:
                node, field_name, difference, threshold = min(candidates, key=lambda item: item[2])
                unique_matches.append((dim, node, field_name, difference, threshold))
            elif candidate_keys:
                ambiguous += 1
            else:
                no_match += 1

        grouped: Dict[Tuple[str, str], List[Tuple[ExtractedDimension, FeatureNode, float, float]]] = defaultdict(list)
        for dim, node, field_name, difference, threshold in unique_matches:
            grouped[(node.id, field_name)].append((dim, node, difference, threshold))

        verified: List[Tuple[ToleranceCase, List[Tuple[str, str]]]] = []
        conflicts = 0
        for (_node_id, field_name), matches in grouped.items():
            signatures = {self._tolerance_signature(item[0].tolerance_config) for item in matches}
            if len(signatures) != 1:
                conflicts += len(matches)
                continue

            matches.sort(key=lambda item: item[2])
            primary_dim, node, difference, threshold = matches[0]
            if node.feature_type not in CANONICAL_FEATURE_TYPES:
                conflicts += len(matches)
                continue
            evidence_keys = [self._evidence_key(dxf_path, item[0]) for item in matches]
            supporting_handles = sorted({
                handle
                for item in matches
                for handle in [item[0].entity_handle, *item[0].duplicate_entity_handles]
                if handle
            })
            confidence = max(0.90, min(0.97, 0.97 - (difference / max(threshold, 1e-9)) * 0.07))
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
                    f"Exact-name STEP/DXF evidence: {node.feature_type} {field_name} "
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
                    "value_difference_mm": round(difference, 6),
                    "match_threshold_mm": round(threshold, 6),
                    "verification_method": "EXACT_FILENAME_UNIQUE_STEP_DXF_VALUE_TYPE_MATCH",
                    "feature_taxonomy": "FeatureGraphExtractor",
                    "verification_checks": [
                        "exact_step_dxf_filename",
                        "native_dxf_dimension_entity",
                        "explicit_tolerance_present",
                        "dimension_type_matches_feature_type",
                        "nominal_value_matches_step_geometry",
                        "unique_step_feature_candidate",
                        "consistent_duplicate_dimension_tolerances",
                    ],
                    "feature_identity_verified": True,
                    "functional_role_verified": False,
                },
            )
            verified.append((case, evidence_keys))

        return verified, {
            "unique_dimension_matches": len(unique_matches),
            "ambiguous_dimension_matches": ambiguous,
            "unmatched_dimensions": no_match,
            "conflicting_tolerance_matches": conflicts,
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
        max_pairs: Optional[int] = None,
        max_dxf_files: Optional[int] = None,
        workers: int = 4,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        discovery = self.discover_sources(search_dirs)
        dxf_map: Dict[str, str] = discovery["dxf_map"]
        step_map: Dict[str, str] = discovery["step_map"]
        pair_keys: List[str] = discovery["pair_keys"]
        if max_pairs is not None:
            pair_keys = pair_keys[:max_pairs]

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
            extracted_results = executor.map(_extract_dxf_worker, dxf_paths, chunksize=4)
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

        linked_cases: List[ToleranceCase] = []
        consumed_evidence: set[Tuple[str, str]] = set()
        pair_stats: Counter[str] = Counter()
        pair_errors: List[Dict[str, str]] = []
        for index, key in enumerate(pair_keys, start=1):
            step_path = step_map[key]
            dxf_path = dxf_map[key]
            dimensions = [
                item
                for item in dimensions_by_path.get(os.path.normcase(os.path.abspath(dxf_path)), [])
                if self._tolerance_is_plausible(item)
            ]
            try:
                verified, stats = self._verify_pair(step_path, dxf_path, dimensions)
                pair_stats.update(stats)
                for case, evidence_keys in verified:
                    linked_cases.append(case)
                    consumed_evidence.update(evidence_keys)
            except Exception as exc:
                pair_errors.append({"pair": key, "error": str(exc)})
            print(f"Verified STEP/DXF pair {index}/{len(pair_keys)}: {key}", flush=True)

        final_cases = [case for key, case in raw_cases.items() if key not in consumed_evidence]
        final_cases.extend(linked_cases)
        final_cases.sort(key=lambda case: (case.evidence_source.upper(), case.case_id))

        verification_counts = Counter(case.effective_verification_status() for case in final_cases)
        feature_counts = Counter(case.feature_type for case in final_cases)
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
                "processed_pairs": len(pair_keys),
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
        return report

    def scan_and_ingest_directories(self, search_dirs: List[str], max_models: int = 50) -> Dict[str, Any]:
        """Backward-compatible entry point; it now performs a clean v2 rebuild."""
        return self.rebuild_database(search_dirs, max_pairs=max_models)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the company tolerance evidence database")
    parser.add_argument("--source", action="append", dest="sources", help="Source directory; repeat as needed")
    parser.add_argument("--output", default=FeatureCaseBase.DEFAULT_DB_PATH)
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--max-pairs", type=int, default=None)
    parser.add_argument("--max-dxf-files", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ingestor = HistoricalDataIngestor()
    report = ingestor.rebuild_database(
        args.sources or list(DEFAULT_SOURCE_DIRS),
        output_path=args.output,
        report_path=args.report,
        max_pairs=args.max_pairs,
        max_dxf_files=args.max_dxf_files,
        workers=args.workers,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
