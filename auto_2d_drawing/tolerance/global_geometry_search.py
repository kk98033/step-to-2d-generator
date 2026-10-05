"""Global 3D-component to 2D-drawing geometry retrieval.

This module deliberately does not trust filenames.  It explodes STEP
assemblies into leaf definitions, projects every unique geometry, splits DXF
sheets into drawing-view clusters, and performs a two-stage search:

1. a cheap rotation/scale invariant descriptor retrieves Top-K candidates;
2. the existing robust similarity registration verifies the vector contours.

Only reciprocal, unambiguous and high-quality matches are emitted as
``verified_pairs``.  Lower-confidence results remain visible as candidates and
never become recommendation evidence by themselves.
"""

from __future__ import annotations

import hashlib
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import ezdxf
import numpy as np

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
from auto_2d_drawing.tolerance.historical_manifest import parse_part_identity
from auto_2d_drawing.tolerance.projection_registration_v2 import (
    AdaptiveProjectionCandidateGenerator,
    ProjectionRegistrationEngine,
)


Point2D = Tuple[float, float]


@dataclass
class GeometryViewRecord:
    record_id: str
    source_path: str
    view_id: str
    points: List[Point2D]
    descriptor: List[float]
    metadata: Dict[str, Any]


class GeometryDescriptor:
    """Small invariant descriptor used only for candidate retrieval."""

    METHOD = "RADIAL_PAIR_DISTANCE_DESCRIPTOR_V1"
    RADIAL_BINS = 8
    DISTANCE_BINS = 8

    @classmethod
    def build(cls, points: Sequence[Point2D]) -> List[float]:
        array = np.asarray(list(points), dtype=float).reshape((-1, 2))
        if len(array) < 4:
            return [0.0] * (4 + cls.RADIAL_BINS + cls.DISTANCE_BINS)
        array = array[np.isfinite(array).all(axis=1)]
        array = np.unique(np.round(array, decimals=6), axis=0)
        if len(array) < 4:
            return [0.0] * (4 + cls.RADIAL_BINS + cls.DISTANCE_BINS)

        # Centroid is equivariant under rotation/reflection; coordinate-wise
        # medians are not and would make the descriptor orientation-sensitive.
        center = np.mean(array, axis=0)
        centered = array - center
        radii = np.linalg.norm(centered, axis=1)
        scale = max(float(np.percentile(radii, 90.0)), 1e-9)
        normalized = centered / scale
        radii = np.linalg.norm(normalized, axis=1)

        # Avoid LAPACK for a 2x2 covariance matrix.  Some Windows Conda builds
        # spend disproportionate time initializing the BLAS backend here,
        # while the analytical eigenvalues are exact and deterministic.
        xx = float(np.mean(normalized[:, 0] * normalized[:, 0]))
        yy = float(np.mean(normalized[:, 1] * normalized[:, 1]))
        xy = float(np.mean(normalized[:, 0] * normalized[:, 1]))
        trace = xx + yy
        root = math.sqrt(max((xx - yy) ** 2 + 4.0 * xy * xy, 0.0))
        largest = max((trace + root) / 2.0, 1e-9)
        smallest = max((trace - root) / 2.0, 0.0)
        anisotropy = smallest / largest
        # Principal-axis aspect is rotation invariant; an axis-aligned bbox
        # ratio would incorrectly penalize the same view after 2D rotation.
        aspect = math.sqrt(max(anisotropy, 0.0))
        hull_fill_proxy = float(min(1.0, len(array) / max(16.0, 4.0 * math.sqrt(len(array)))))
        radius_spread = float(np.std(radii) / max(np.mean(radii), 1e-9))

        radial_hist, _ = np.histogram(radii, bins=cls.RADIAL_BINS, range=(0.0, 1.5))
        radial = radial_hist.astype(float)
        radial /= max(float(np.sum(radial)), 1.0)

        sampled = cls._farthest_sample(normalized, min(32, len(normalized)))
        delta = sampled[:, None, :] - sampled[None, :, :]
        distances = np.linalg.norm(delta, axis=2)
        distances = distances[np.triu_indices(len(sampled), 1)]
        distance_hist, _ = np.histogram(distances, bins=cls.DISTANCE_BINS, range=(0.0, 2.5))
        pairwise = distance_hist.astype(float)
        pairwise /= max(float(np.sum(pairwise)), 1.0)
        return [
            round(aspect, 8),
            round(anisotropy, 8),
            round(min(radius_spread, 3.0) / 3.0, 8),
            round(hull_fill_proxy, 8),
            *[round(float(value), 8) for value in radial],
            *[round(float(value), 8) for value in pairwise],
        ]

    @staticmethod
    def _farthest_sample(points: np.ndarray, count: int) -> np.ndarray:
        if len(points) <= count:
            return points
        center = np.mean(points, axis=0)
        selected = [int(np.argmax(np.sum((points - center) ** 2, axis=1)))]
        minimum = np.sum((points - points[selected[0]]) ** 2, axis=1)
        while len(selected) < count:
            chosen = int(np.argmax(minimum))
            selected.append(chosen)
            minimum = np.minimum(minimum, np.sum((points - points[chosen]) ** 2, axis=1))
        return points[np.asarray(selected, dtype=int)]


class GlobalGeometryPairSearcher:
    METHOD = "GLOBAL_COMPONENT_DXF_GEOMETRY_SEARCH_V1"

    def __init__(
        self,
        retrieval_top_k: int = 12,
        refine_top_drawings: int = 8,
        minimum_points: int = 12,
        minimum_registration_score: float = 0.78,
        minimum_inlier_ratio: float = 0.74,
        maximum_chamfer: float = 0.04,
        maximum_ranked_hausdorff: float = 0.13,
        minimum_reciprocal_margin: float = 0.035,
        maximum_record_points: int = 256,
    ):
        self.retrieval_top_k = max(2, int(retrieval_top_k))
        self.refine_top_drawings = max(1, int(refine_top_drawings))
        self.minimum_points = max(4, int(minimum_points))
        self.minimum_registration_score = float(minimum_registration_score)
        self.minimum_inlier_ratio = float(minimum_inlier_ratio)
        self.maximum_chamfer = float(maximum_chamfer)
        self.maximum_ranked_hausdorff = float(maximum_ranked_hausdorff)
        self.minimum_reciprocal_margin = float(minimum_reciprocal_margin)
        self.maximum_record_points = max(64, int(maximum_record_points))
        # Global retrieval uses a faster verifier; every promoted tolerance is
        # subsequently checked again by ProjectionGeometryVerifier at full V3
        # resolution before it becomes AUTO_VERIFIED.
        self.registration = ProjectionRegistrationEngine(
            angle_step_degrees=30,
            max_points=96,
            trim_fraction=0.82,
            icp_iterations=10,
        )

    def build_dxf_index(self, dxf_paths: Iterable[str]) -> Tuple[List[GeometryViewRecord], List[Dict[str, str]]]:
        records: List[GeometryViewRecord] = []
        errors: List[Dict[str, str]] = []
        for dxf_path in sorted({os.path.abspath(path) for path in dxf_paths}, key=str.lower):
            try:
                modelspace = ezdxf.readfile(dxf_path).modelspace()
                analyzer = DxfStructure2DAnalyzer(modelspace)
                for cluster in analyzer.view_clusters:
                    points = self._dxf_cluster_points(analyzer, cluster)
                    if len(points) < self.minimum_points or min(cluster.width, cluster.height) <= 1e-8:
                        continue
                    points = self._reduce_points(points)
                    records.append(GeometryViewRecord(
                        record_id=self._record_id(dxf_path, cluster.view_id),
                        source_path=dxf_path,
                        view_id=cluster.view_id,
                        points=points,
                        descriptor=GeometryDescriptor.build(points),
                        metadata={
                            "bbox": [round(float(value), 6) for value in cluster.bbox],
                            "primitive_count": len(cluster.primitive_indexes),
                        },
                    ))
            except Exception as exc:
                errors.append({"path": dxf_path, "error": str(exc)})
        return records, errors

    def search(
        self,
        step_paths: Iterable[str],
        dxf_paths: Iterable[str],
        max_components: Optional[int] = None,
        excluded_pairs: Optional[Iterable[Tuple[str, str, str]]] = None,
        pre_extracted_components: Optional[Iterable[Any]] = None,
    ) -> Dict[str, Any]:
        step_paths = list(step_paths)
        dxf_paths = list(dxf_paths)
        dxf_records, dxf_errors = self.build_dxf_index(dxf_paths)
        components, component_errors = self._unique_components(
            step_paths,
            max_components=max_components,
            pre_extracted_components=pre_extracted_components,
        )
        if not dxf_records or not components:
            return self._empty_manifest(components, dxf_records, component_errors, dxf_errors)

        descriptor_matrix = np.asarray([record.descriptor for record in dxf_records], dtype=float)
        excluded = {
            (os.path.normcase(os.path.abspath(step)), str(label), os.path.normcase(os.path.abspath(dxf)))
            for step, label, dxf in (excluded_pairs or ())
        }
        refined: List[Dict[str, Any]] = []
        projection_errors: List[Dict[str, str]] = []
        projected_view_count = 0
        for component_index, component in enumerate(components, start=1):
            try:
                projections = AdaptiveProjectionCandidateGenerator(max_views=6).project_all(component["shape"])
            except Exception as exc:
                projection_errors.append({
                    "step_path": component["step_path"],
                    "component_label_entry": component["label_entry"],
                    "error": str(exc),
                })
                continue
            candidate_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for step_view_id, projection in projections.items():
                step_points = self._step_view_points(projection)
                if len(step_points) < self.minimum_points:
                    continue
                step_points = self._reduce_points(step_points)
                projected_view_count += 1
                descriptor = np.asarray(GeometryDescriptor.build(step_points), dtype=float)
                distances = np.linalg.norm(descriptor_matrix - descriptor[None, :], axis=1)
                count = min(self.retrieval_top_k, len(dxf_records))
                indexes = np.argpartition(distances, count - 1)[:count]
                for record_index in indexes:
                    record = dxf_records[int(record_index)]
                    pair_key = (
                        os.path.normcase(component["step_path"]),
                        component["label_entry"],
                        os.path.normcase(record.source_path),
                    )
                    if pair_key in excluded:
                        continue
                    candidate_rows[record.source_path].append({
                        "descriptor_distance": float(distances[int(record_index)]),
                        "step_view_id": step_view_id,
                        "step_points": step_points,
                        "dxf_record": record,
                    })

            ranked_drawings = sorted(
                candidate_rows.items(),
                key=lambda item: min(row["descriptor_distance"] for row in item[1]),
            )[:self.refine_top_drawings]
            for dxf_path, rows in ranked_drawings:
                exact_rows = []
                for row in sorted(rows, key=lambda item: item["descriptor_distance"])[:4]:
                    registration = self.registration.register(
                        row["dxf_record"].points,
                        row["step_points"],
                    )
                    exact_rows.append({
                        "step_view_id": row["step_view_id"],
                        "dxf_view_id": row["dxf_record"].view_id,
                        "dxf_record_id": row["dxf_record"].record_id,
                        "descriptor_distance": round(row["descriptor_distance"], 6),
                        "descriptor_similarity": round(math.exp(-2.5 * row["descriptor_distance"]), 6),
                        "registration": registration,
                    })
                exact_rows.sort(
                    key=lambda item: float(item["registration"].get("score", 0.0) or 0.0),
                    reverse=True,
                )
                if not exact_rows:
                    continue
                best = exact_rows[0]
                refined.append({
                    "component": {key: value for key, value in component.items() if key != "shape"},
                    "dxf_path": dxf_path,
                    "best_match": best,
                    "supporting_matches": exact_rows[1:3],
                    "component_progress": [component_index, len(components)],
                })

        manifest = self._classify(refined)
        manifest["errors"] = {
            "component_extraction": component_errors,
            "dxf_index": dxf_errors,
            "projection": projection_errors,
        }
        manifest["statistics"].update({
            "scanned_step_files": len({os.path.abspath(path) for path in step_paths}),
            "unique_component_geometries": len(components),
            "indexed_dxf_views": len(dxf_records),
            "projected_component_views": projected_view_count,
            "refined_component_drawing_candidates": len(refined),
        })
        return manifest

    def _unique_components(
        self,
        step_paths: Iterable[str],
        max_components: Optional[int],
        pre_extracted_components: Optional[Iterable[Any]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
        grouped: Dict[str, List[Any]] = defaultdict(list)
        errors: List[Dict[str, str]] = []
        if pre_extracted_components is not None:
            for component in pre_extracted_components:
                grouped[component.fingerprint].append(component)
        else:
            for step_path in sorted({os.path.abspath(path) for path in step_paths}, key=str.lower):
                try:
                    for component in extract_leaf_components(step_path):
                        grouped[component.fingerprint].append(component)
                except Exception as exc:
                    errors.append({"path": step_path, "error": str(exc)})
        result: List[Dict[str, Any]] = []
        for fingerprint, equivalents in sorted(grouped.items()):
            representative = sorted(equivalents, key=lambda item: (item.step_path.lower(), item.label_entry))[0]
            identity = representative.identity
            result.append({
                "fingerprint": fingerprint,
                "step_path": representative.step_path,
                "label_entry": representative.label_entry,
                "name": representative.name,
                "part_number": identity.part_number if identity else None,
                "component_revision": identity.revision if identity else None,
                "equivalent_step_sources": sorted({item.step_path for item in equivalents}, key=str.lower),
                "equivalent_occurrence_count": len(equivalents),
                "shape": representative.shape,
            })
        if max_components is not None:
            result = result[:max(0, int(max_components))]
        return result, errors

    def _classify(self, refined: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        by_dxf_view: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in refined:
            by_dxf_view[item["best_match"]["dxf_record_id"]].append(item)
        verified_pairs: List[Dict[str, Any]] = []
        candidates: List[Dict[str, Any]] = []
        statuses: Counter[str] = Counter()
        for item in refined:
            best = item["best_match"]
            registration = best["registration"]
            rivals = sorted(
                by_dxf_view[best["dxf_record_id"]],
                key=lambda row: float(row["best_match"]["registration"].get("score", 0.0) or 0.0),
                reverse=True,
            )
            score = float(registration.get("score", 0.0) or 0.0)
            rival_scores = [
                float(row["best_match"]["registration"].get("score", 0.0) or 0.0)
                for row in rivals
                if row["component"]["fingerprint"] != item["component"]["fingerprint"]
            ]
            margin = score - max(rival_scores, default=0.0)
            reciprocal_best = bool(rivals and rivals[0]["component"]["fingerprint"] == item["component"]["fingerprint"])
            checks = {
                "global_descriptor_retrieval": best["descriptor_similarity"] >= 0.45,
                "robust_projection_registration": score >= self.minimum_registration_score,
                "registration_inlier_ratio": float(registration.get("inlier_ratio", 0.0) or 0.0) >= self.minimum_inlier_ratio,
                "registration_chamfer": (
                    registration.get("normalized_chamfer") is not None
                    and float(registration["normalized_chamfer"]) <= self.maximum_chamfer
                ),
                "registration_ranked_hausdorff": (
                    registration.get("ranked_hausdorff") is not None
                    and float(registration["ranked_hausdorff"]) <= self.maximum_ranked_hausdorff
                ),
                "reciprocal_best_component": reciprocal_best,
                "unambiguous_component_margin": margin >= self.minimum_reciprocal_margin,
            }
            passed = all(checks.values())
            status = "GEOMETRY_PAIR_VERIFIED" if passed else "GEOMETRY_PAIR_CANDIDATE"
            statuses[status] += 1
            component = item["component"]
            dxf_identity = parse_part_identity(item["dxf_path"])
            payload = {
                "status": status,
                "pair_id": (
                    f"GLOBAL_GEOMETRY:{component['fingerprint']}:"
                    f"{os.path.basename(item['dxf_path'])}:{best['dxf_view_id']}"
                ),
                "step_path": component["step_path"],
                "dxf_path": item["dxf_path"],
                "pair_method": self.METHOD,
                "eligible_for_auto_verification": passed,
                "part_number": component.get("part_number") or (dxf_identity.part_number if dxf_identity else None),
                "revision": dxf_identity.revision if dxf_identity else None,
                "component_name": component["name"],
                "component_revision": component.get("component_revision"),
                "component_label_entry": component["label_entry"],
                "component_fingerprint": component["fingerprint"],
                "equivalent_step_sources": component["equivalent_step_sources"],
                "matched_dxf_view_ids": [best["dxf_view_id"]],
                "matched_step_view": best["step_view_id"],
                "global_geometry_score": round(score, 6),
                "reciprocal_margin": round(margin, 6),
                "geometry_search_evidence": {
                    "descriptor_method": GeometryDescriptor.METHOD,
                    "descriptor_similarity": best["descriptor_similarity"],
                    "registration": registration,
                    "supporting_matches": item["supporting_matches"],
                    "checks": checks,
                },
                "verification_checks": [key for key, value in checks.items() if value],
            }
            (verified_pairs if passed else candidates).append(payload)
        return {
            "schema_version": 1,
            "method": self.METHOD,
            "policy": {
                "filename_independent": True,
                "all_leaf_components": True,
                "two_stage_retrieval": True,
                "reciprocal_match_required": True,
                "ambiguous_matches_auto_verified": False,
                "downstream_dimension_localization_required": True,
            },
            "verified_pairs": verified_pairs,
            "candidates": candidates,
            "statistics": {
                "verified_pair_count": len(verified_pairs),
                "candidate_pair_count": len(candidates),
                "statuses": dict(statuses),
            },
        }

    def _empty_manifest(self, components, records, component_errors, dxf_errors) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "method": self.METHOD,
            "policy": {"filename_independent": True, "two_stage_retrieval": True},
            "verified_pairs": [],
            "candidates": [],
            "errors": {
                "component_extraction": component_errors,
                "dxf_index": dxf_errors,
                "projection": [],
            },
            "statistics": {
                "unique_component_geometries": len(components),
                "indexed_dxf_views": len(records),
                "verified_pair_count": 0,
                "candidate_pair_count": 0,
            },
        }

    @staticmethod
    def _record_id(path: str, view_id: str) -> str:
        digest = hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:12]
        return f"{digest}:{view_id}"

    def _reduce_points(self, points: Sequence[Point2D]) -> List[Point2D]:
        array = np.asarray(list(points), dtype=float).reshape((-1, 2))
        array = array[np.isfinite(array).all(axis=1)]
        array = np.unique(np.round(array, decimals=6), axis=0)
        coarse_limit = max(self.maximum_record_points * 16, 1024)
        if len(array) > coarse_limit:
            # Collapse extreme tessellation density into a spatial grid before
            # farthest-point sampling.  This preserves sheet-wide coverage
            # and changes the complexity from O(raw_points * K) to a bounded
            # O(4096 * K) for the default settings.
            minimum = np.min(array, axis=0)
            extent = np.maximum(np.ptp(array, axis=0), 1e-9)
            grid_size = max(16, int(math.ceil(math.sqrt(coarse_limit))))
            cells = np.floor((array - minimum) / extent * (grid_size - 1)).astype(int)
            keys = cells[:, 0] * grid_size + cells[:, 1]
            _unique_keys, indexes = np.unique(keys, return_index=True)
            array = array[np.sort(indexes)]
        if len(array) > self.maximum_record_points:
            array = GeometryDescriptor._farthest_sample(array, self.maximum_record_points)
        return [(float(point[0]), float(point[1])) for point in array]

    @classmethod
    def _dxf_cluster_points(cls, analyzer, cluster) -> List[Point2D]:
        result: List[Point2D] = []
        for index in cluster.primitive_indexes:
            primitive = analyzer.primitives[index]
            if primitive.center is not None and primitive.radius > 0.0:
                cx, cy = primitive.center
                result.extend(
                    (
                        float(cx) + primitive.radius * math.cos(2.0 * math.pi * step / 32.0),
                        float(cy) + primitive.radius * math.sin(2.0 * math.pi * step / 32.0),
                    )
                    for step in range(32)
                )
            else:
                for start, end in zip(primitive.points, primitive.points[1:]):
                    result.extend(cls._sample_segment(start, end, 20))
        return result

    @classmethod
    def _step_view_points(cls, view: Dict[str, Any]) -> List[Point2D]:
        result: List[Point2D] = []
        for edge in list(view.get("visible") or []) + list(view.get("hidden") or []):
            if edge.get("type") == "circle" and edge.get("center") and edge.get("radius"):
                cx, cy = edge["center"]
                radius = float(edge["radius"])
                result.extend(
                    (
                        float(cx) + radius * math.cos(2.0 * math.pi * step / 32.0),
                        float(cy) + radius * math.sin(2.0 * math.pi * step / 32.0),
                    )
                    for step in range(32)
                )
            elif edge.get("p1") and edge.get("p2"):
                result.extend(cls._sample_segment(edge["p1"], edge["p2"], 20))
            elif edge.get("points"):
                for start, end in zip(edge["points"], edge["points"][1:]):
                    result.extend(cls._sample_segment(start, end, 12))
        return result

    @staticmethod
    def _sample_segment(start, end, max_count: int) -> List[Point2D]:
        length = math.dist(start, end)
        count = max(1, min(max_count, int(math.ceil(length)) + 1))
        return [
            (
                float(start[0]) + (float(end[0]) - float(start[0])) * index / count,
                float(start[1]) + (float(end[1]) - float(start[1])) * index / count,
            )
            for index in range(count + 1)
        ]
