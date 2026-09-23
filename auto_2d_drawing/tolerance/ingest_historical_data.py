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

    def discover_sources(self, search_dirs: Sequence[str]) -> Dict[str, Any]:
        step_map: Dict[str, str] = {}
        dxf_map: Dict[str, str] = {}
        duplicate_steps = 0
        duplicate_dxfs = 0

        for source_dir in search_dirs:
            if not os.path.isdir(source_dir):
                continue
            for root, _dirs, files in os.walk(source_dir):
                for filename in files:
                    extension = os.path.splitext(filename)[1].lower()
                    if extension not in {".stp", ".step", ".dxf"}:
                        continue
                    key = self._normalise_stem(filename)
                    full_path = os.path.abspath(os.path.join(root, filename))
                    if extension in {".stp", ".step"}:
                        if key in step_map:
                            duplicate_steps += 1
                        else:
                            step_map[key] = full_path
                    else:
                        if key in dxf_map:
                            duplicate_dxfs += 1
                        else:
                            dxf_map[key] = full_path

        pair_keys = sorted(set(step_map) & set(dxf_map))
        return {
            "step_map": step_map,
            "dxf_map": dxf_map,
            "pair_keys": pair_keys,
            "duplicate_steps": duplicate_steps,
            "duplicate_dxfs": duplicate_dxfs,
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
            supporting_handles = sorted({item[0].entity_handle for item in matches if item[0].entity_handle})
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
        parse_errors = 0
        dxf_paths = sorted(dxf_map.values())
        if max_dxf_files is not None:
            dxf_paths = dxf_paths[:max_dxf_files]
        with ProcessPoolExecutor(max_workers=max(1, workers)) as executor:
            extracted_results = executor.map(_extract_dxf_worker, dxf_paths, chunksize=4)
            for index, (dxf_path, extracted_dicts) in enumerate(extracted_results, start=1):
                try:
                    extracted = [ExtractedDimension(**item) for item in extracted_dicts]
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
            },
            "extraction": {
                "all_candidate_statuses": dict(extraction_statuses),
                "parse_errors": parse_errors,
                "native_tolerance_entities": len(raw_cases),
                "consumed_by_feature_links": len(consumed_evidence),
                "feature_inference_2d": {
                    "method": FeatureInference2DEngine.METHOD,
                    "status_counts": dict(inference_statuses),
                    "auto_inferred_feature_counts": dict(inferred_feature_types),
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
