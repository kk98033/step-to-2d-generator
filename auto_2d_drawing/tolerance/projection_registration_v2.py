"""Robust vector registration between STEP projections and DXF views.

The legacy drawing engine is intentionally not involved here.  This module
solves a narrowly scoped evidence problem:

* generate a conservative set of orthographic HLR projection candidates;
* estimate a 2D similarity transform (mirror, rotation, uniform scale and
  translation) without assuming that a drawing view uses the STEP axes; and
* score partial/noisy vector overlap with a trimmed bidirectional Chamfer
  distance.

All coordinates are derived from CAD geometry.  No model-specific coordinate
or camera position is embedded in the implementation.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


Point2D = Tuple[float, float]
Vector3D = Tuple[float, float, float]


@dataclass(frozen=True)
class SimilarityTransform2D:
    """A transform mapping STEP projection coordinates into DXF coordinates."""

    scale: float
    rotation_degrees: float
    translation: Point2D
    mirrored: bool = False

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["translation"] = [round(float(value), 8) for value in self.translation]
        payload["scale"] = round(float(self.scale), 10)
        payload["rotation_degrees"] = round(float(self.rotation_degrees), 6)
        return payload

    def apply(self, points: Sequence[Point2D]) -> List[Point2D]:
        array = _as_points(points)
        if not len(array):
            return []
        transformed = self.apply_array(array)
        return [(float(point[0]), float(point[1])) for point in transformed]

    def apply_point(self, point: Point2D) -> Point2D:
        transformed = self.apply_array(np.asarray([point], dtype=float))[0]
        return float(transformed[0]), float(transformed[1])

    def apply_array(self, points: np.ndarray) -> np.ndarray:
        working = np.asarray(points, dtype=float).copy()
        if self.mirrored:
            working[:, 0] *= -1.0
        radians = math.radians(self.rotation_degrees)
        cosine, sine = math.cos(radians), math.sin(radians)
        rotated = np.column_stack((
            working[:, 0] * cosine - working[:, 1] * sine,
            working[:, 0] * sine + working[:, 1] * cosine,
        ))
        return self.scale * rotated + np.asarray(self.translation, dtype=float)

    def inverse_point(self, point: Point2D) -> Point2D:
        if abs(self.scale) <= 1e-12:
            raise ValueError("Cannot invert a zero-scale similarity transform")
        working = (np.asarray(point, dtype=float) - np.asarray(self.translation, dtype=float)) / self.scale
        radians = math.radians(self.rotation_degrees)
        cosine, sine = math.cos(radians), math.sin(radians)
        source = np.asarray((
            working[0] * cosine + working[1] * sine,
            -working[0] * sine + working[1] * cosine,
        ))
        if self.mirrored:
            source[0] *= -1.0
        return float(source[0]), float(source[1])


def _as_points(points: Sequence[Point2D]) -> np.ndarray:
    array = np.asarray(list(points), dtype=float)
    if not array.size:
        return np.empty((0, 2), dtype=float)
    return array.reshape((-1, 2))


class ProjectionRegistrationEngine:
    """Coarse-to-fine, dependency-light point-set registration.

    The implementation intentionally uses NumPy only.  The portable project
    runtime does not ship SciPy/OpenCV, and registration must behave the same
    in the local Conda environment and the backend container.
    """

    METHOD = "DENSITY_BALANCED_MUTUAL_ICP_V3"

    def __init__(
        self,
        angle_step_degrees: int = 15,
        max_points: int = 160,
        trim_fraction: float = 0.82,
        icp_iterations: int = 18,
    ):
        self.angle_step_degrees = max(3, int(angle_step_degrees))
        self.max_points = max(32, int(max_points))
        self.trim_fraction = max(0.55, min(1.0, float(trim_fraction)))
        self.icp_iterations = max(1, int(icp_iterations))

    def register(
        self,
        dxf_points: Sequence[Point2D],
        step_points: Sequence[Point2D],
    ) -> Dict[str, Any]:
        target = self._prepare(dxf_points)
        source = self._prepare(step_points)
        if len(target) < 4 or len(source) < 4:
            return {
                "method": self.METHOD,
                "status": "INSUFFICIENT_POINTS",
                "score": 0.0,
                "contour_score": 0.0,
                "normalized_chamfer": None,
                "inlier_ratio": 0.0,
                "transform": None,
            }
        # A DXF view may oversample one entity much more densely than the STEP
        # projection.  Cap only that excess; do not shrink a complete STEP
        # contour to the size of a genuinely partial DXF observation.
        if len(target) > len(source):
            target = self._farthest_point_sample(target, len(source))

        target_center = np.median(target, axis=0)
        source_center = np.median(source, axis=0)
        target_radius = self._robust_radius(target, target_center)
        source_radius = self._robust_radius(source, source_center)
        if min(target_radius, source_radius) <= 1e-10:
            return {
                "method": self.METHOD,
                "status": "DEGENERATE_GEOMETRY",
                "score": 0.0,
                "contour_score": 0.0,
                "normalized_chamfer": None,
                "inlier_ratio": 0.0,
                "transform": None,
            }

        initial_scale = target_radius / source_radius
        hypotheses: List[Tuple[float, SimilarityTransform2D]] = []
        for mirrored in (False, True):
            for angle in range(0, 360, self.angle_step_degrees):
                transform = self._centered_transform(
                    source_center,
                    target_center,
                    initial_scale,
                    float(angle),
                    mirrored,
                )
                metrics = self._distance_metrics(transform.apply_array(source), target)
                hypotheses.append((metrics["score"], transform))

        hypotheses.sort(key=lambda item: item[0], reverse=True)
        refined: List[Dict[str, Any]] = []
        for _score, transform in hypotheses[:8]:
            fitted = self._refine(source, target, transform)
            transformed = fitted.apply_array(source)
            metrics = self._distance_metrics(transformed, target)
            refined.append({"transform": fitted, **metrics})

        refined.sort(key=lambda item: item["score"], reverse=True)
        best = refined[0]
        distinct_runner_up = next(
            (
                item for item in refined[1:]
                if self._transforms_are_distinct(best["transform"], item["transform"])
            ),
            refined[1] if len(refined) > 1 else None,
        )
        runner_score = float(distinct_runner_up["score"]) if distinct_runner_up else 0.0
        transform = best["transform"]
        return {
            "method": self.METHOD,
            "status": "REGISTERED",
            "score": round(float(best["score"]), 4),
            "contour_score": round(float(best["score"]), 4),
            "normalized_chamfer": round(float(best["normalized_chamfer"]), 6),
            "inlier_ratio": round(float(best["inlier_ratio"]), 4),
            "source_coverage": round(float(best["source_coverage"]), 4),
            "target_coverage": round(float(best["target_coverage"]), 4),
            "ranked_hausdorff": round(float(best["ranked_hausdorff"]), 6),
            "runner_up_score": round(runner_score, 4),
            "score_margin": round(float(best["score"]) - runner_score, 4),
            "transform": transform.to_dict(),
            "rotation_degrees": round(float(transform.rotation_degrees), 4),
            "mirrored": bool(transform.mirrored),
            "scale": round(float(transform.scale), 8),
            "translation": [round(float(value), 6) for value in transform.translation],
            "dxf_point_count": int(len(target)),
            "step_point_count": int(len(source)),
        }

    def _prepare(self, points: Sequence[Point2D]) -> np.ndarray:
        array = _as_points(points)
        if not len(array):
            return array
        finite = array[np.isfinite(array).all(axis=1)]
        if not len(finite):
            return finite
        # Rounded uniqueness removes exact duplicates.  CAD exports often
        # tessellate one curve hundreds of times more densely than another;
        # index-based sampling would then let that curve dominate the fit.
        # Deterministic farthest-point sampling preserves the spatial support
        # of the whole contour instead of its source entity density.
        unique = np.unique(np.round(finite, decimals=7), axis=0)
        if len(unique) <= self.max_points:
            return unique
        return self._farthest_point_sample(unique, self.max_points)

    @staticmethod
    def _farthest_point_sample(points: np.ndarray, count: int) -> np.ndarray:
        center = np.median(points, axis=0)
        first = int(np.argmax(np.sum((points - center) ** 2, axis=1)))
        selected = np.empty(count, dtype=int)
        selected[0] = first
        minimum_squared = np.sum((points - points[first]) ** 2, axis=1)
        for index in range(1, count):
            chosen = int(np.argmax(minimum_squared))
            selected[index] = chosen
            candidate_squared = np.sum((points - points[chosen]) ** 2, axis=1)
            minimum_squared = np.minimum(minimum_squared, candidate_squared)
        return points[selected]

    @staticmethod
    def _robust_radius(points: np.ndarray, center: np.ndarray) -> float:
        distances = np.linalg.norm(points - center, axis=1)
        return float(np.percentile(distances, 80.0))

    @staticmethod
    def _centered_transform(
        source_center: np.ndarray,
        target_center: np.ndarray,
        scale: float,
        angle_degrees: float,
        mirrored: bool,
    ) -> SimilarityTransform2D:
        center = np.asarray(source_center, dtype=float).copy()
        if mirrored:
            center[0] *= -1.0
        radians = math.radians(angle_degrees)
        cosine, sine = math.cos(radians), math.sin(radians)
        rotated_center = np.asarray((
            center[0] * cosine - center[1] * sine,
            center[0] * sine + center[1] * cosine,
        ))
        translation = np.asarray(target_center, dtype=float) - scale * rotated_center
        return SimilarityTransform2D(
            scale=float(scale),
            rotation_degrees=float(angle_degrees),
            translation=(float(translation[0]), float(translation[1])),
            mirrored=mirrored,
        )

    def _refine(
        self,
        source: np.ndarray,
        target: np.ndarray,
        transform: SimilarityTransform2D,
    ) -> SimilarityTransform2D:
        current = transform
        keep_count = max(4, int(math.ceil(len(source) * self.trim_fraction)))
        reflected_source = source.copy()
        if transform.mirrored:
            reflected_source[:, 0] *= -1.0

        previous_score = -1.0
        for _iteration in range(self.icp_iterations):
            transformed = current.apply_array(source)
            distances, indexes = self._nearest(transformed, target)
            _reverse_distances, reverse_indexes = self._nearest(target, transformed)
            mutual = np.asarray([
                index
                for index, target_index in enumerate(indexes)
                if reverse_indexes[target_index] == index
            ], dtype=int)
            minimum_mutual = max(4, int(math.ceil(min(len(source), len(target)) * 0.08)))
            candidates = mutual if len(mutual) >= minimum_mutual else np.arange(len(source))
            selected_count = max(4, min(len(candidates), keep_count))
            selected = candidates[np.argsort(distances[candidates])[:selected_count]]
            residuals = distances[selected]
            target_diagonal = max(float(np.linalg.norm(np.ptp(target, axis=0))), 1e-9)
            median_residual = float(np.median(residuals)) if len(residuals) else target_diagonal
            robust_scale = max(median_residual * 2.5, target_diagonal * 0.0025)
            weights = np.exp(-0.5 * (residuals / robust_scale) ** 2)
            fitted = self._fit_similarity(
                reflected_source[selected],
                target[indexes[selected]],
                mirrored=transform.mirrored,
                weights=weights,
            )
            metrics = self._distance_metrics(fitted.apply_array(source), target)
            if metrics["score"] + 1e-7 < previous_score:
                break
            current = fitted
            if abs(metrics["score"] - previous_score) < 1e-6:
                break
            previous_score = metrics["score"]
        return current

    @staticmethod
    def _fit_similarity(
        reflected_source: np.ndarray,
        target: np.ndarray,
        mirrored: bool,
        weights: Optional[np.ndarray] = None,
    ) -> SimilarityTransform2D:
        if weights is None or len(weights) != len(reflected_source):
            normalized_weights = np.full(len(reflected_source), 1.0 / max(len(reflected_source), 1))
        else:
            safe_weights = np.maximum(np.asarray(weights, dtype=float), 1e-9)
            normalized_weights = safe_weights / float(np.sum(safe_weights))
        source_center = np.sum(reflected_source * normalized_weights[:, None], axis=0)
        target_center = np.sum(target * normalized_weights[:, None], axis=0)
        left = reflected_source - source_center
        right = target - target_center
        a = float(np.sum(normalized_weights * (left[:, 0] * right[:, 0] + left[:, 1] * right[:, 1])))
        b = float(np.sum(normalized_weights * (left[:, 0] * right[:, 1] - left[:, 1] * right[:, 0])))
        angle = math.atan2(b, a)
        cosine, sine = math.cos(angle), math.sin(angle)
        rotated = np.column_stack((
            left[:, 0] * cosine - left[:, 1] * sine,
            left[:, 0] * sine + left[:, 1] * cosine,
        ))
        denominator = float(np.sum(normalized_weights * np.sum(left * left, axis=1)))
        numerator = float(np.sum(normalized_weights * np.sum(rotated * right, axis=1)))
        scale = numerator / denominator if denominator > 1e-12 else 1.0
        scale = max(abs(scale), 1e-10)
        rotated_center = np.asarray((
            source_center[0] * cosine - source_center[1] * sine,
            source_center[0] * sine + source_center[1] * cosine,
        ))
        translation = target_center - scale * rotated_center
        return SimilarityTransform2D(
            scale=scale,
            rotation_degrees=math.degrees(angle) % 360.0,
            translation=(float(translation[0]), float(translation[1])),
            mirrored=mirrored,
        )

    def _distance_metrics(self, transformed_source: np.ndarray, target: np.ndarray) -> Dict[str, float]:
        source_distances, _ = self._nearest(transformed_source, target)
        target_distances, _ = self._nearest(target, transformed_source)
        keep_source = max(1, int(math.ceil(len(source_distances) * self.trim_fraction)))
        keep_target = max(1, int(math.ceil(len(target_distances) * self.trim_fraction)))
        source_trimmed = np.partition(source_distances, keep_source - 1)[:keep_source]
        target_trimmed = np.partition(target_distances, keep_target - 1)[:keep_target]
        diagonal = float(np.linalg.norm(np.ptp(target, axis=0)))
        normalizer = max(diagonal, 1e-9)
        chamfer = 0.5 * (
            float(np.mean(source_trimmed)) + float(np.mean(target_trimmed))
        ) / normalizer
        inlier_threshold = max(normalizer * 0.035, 1e-7)
        source_coverage = float(np.mean(source_distances <= inlier_threshold))
        target_coverage = float(np.mean(target_distances <= inlier_threshold))
        inlier_ratio = math.sqrt(max(source_coverage * target_coverage, 0.0))
        ranked_hausdorff = float(np.percentile(
            np.concatenate((source_distances, target_distances)),
            85.0,
        )) / normalizer
        score = (
            math.exp(-8.0 * chamfer - 1.5 * ranked_hausdorff)
            * (0.40 + 0.60 * inlier_ratio)
        )
        return {
            "score": max(0.0, min(1.0, score)),
            "normalized_chamfer": chamfer,
            "inlier_ratio": inlier_ratio,
            "source_coverage": source_coverage,
            "target_coverage": target_coverage,
            "ranked_hausdorff": ranked_hausdorff,
        }

    @staticmethod
    def _nearest(source: np.ndarray, target: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        difference = source[:, None, :] - target[None, :, :]
        squared = np.einsum("ijk,ijk->ij", difference, difference)
        indexes = np.argmin(squared, axis=1)
        distances = np.sqrt(squared[np.arange(len(source)), indexes])
        return distances, indexes

    @staticmethod
    def _transforms_are_distinct(
        left: SimilarityTransform2D,
        right: SimilarityTransform2D,
    ) -> bool:
        angle_delta = abs((left.rotation_degrees - right.rotation_degrees + 180.0) % 360.0 - 180.0)
        scale_delta = abs(math.log(max(left.scale, 1e-12) / max(right.scale, 1e-12)))
        return left.mirrored != right.mirrored or angle_delta > 8.0 or scale_delta > 0.08


@dataclass(frozen=True)
class CandidateViewSpec:
    name: str
    direction: Vector3D
    up: Vector3D
    source: str


class AdaptiveProjectionCandidateGenerator:
    """Generate HLR views from model axes and extracted feature axes."""

    METHOD = "ADAPTIVE_HLR_CANDIDATES_V2"

    def __init__(self, max_views: int = 18):
        self.max_views = max(6, int(max_views))

    def build_specs(
        self,
        feature_nodes: Optional[Iterable[Any]] = None,
        include_feature_axes: bool = True,
    ) -> List[CandidateViewSpec]:
        requested: List[Tuple[Vector3D, str]] = [
            ((0.0, 0.0, 1.0), "canonical_front"),
            ((0.0, 0.0, -1.0), "canonical_back"),
            ((0.0, 1.0, 0.0), "canonical_top"),
            ((0.0, -1.0, 0.0), "canonical_bottom"),
            ((-1.0, 0.0, 0.0), "canonical_right"),
            ((1.0, 0.0, 0.0), "canonical_left"),
        ]
        for node in (feature_nodes or ()) if include_feature_axes else ():
            source = dict(getattr(node, "source_info", {}) or {})
            raw_axis = source.get("axis_dir")
            axis = self._normalize(raw_axis)
            if axis is None:
                continue
            first, second = self._plane_basis(axis)
            requested.extend([
                (axis, "feature_axis"),
                (tuple(-value for value in axis), "feature_axis_reverse"),
                (first, "feature_radial"),
                (tuple(-value for value in first), "feature_radial_reverse"),
                (second, "feature_radial"),
                (tuple(-value for value in second), "feature_radial_reverse"),
            ])

        specs: List[CandidateViewSpec] = []
        for direction, source in requested:
            normalized = self._normalize(direction)
            if normalized is None:
                continue
            if any(abs(sum(a * b for a, b in zip(normalized, item.direction))) > 0.9995
                   and sum(a * b for a, b in zip(normalized, item.direction)) > 0
                   for item in specs):
                continue
            up, _side = self._plane_basis(normalized)
            canonical_names = {
                (0.0, 0.0, 1.0): "front",
                (0.0, 0.0, -1.0): "back",
                (0.0, 1.0, 0.0): "top",
                (0.0, -1.0, 0.0): "bottom",
                (-1.0, 0.0, 0.0): "right",
                (1.0, 0.0, 0.0): "left",
            }
            rounded = tuple(round(value, 6) for value in normalized)
            name = canonical_names.get(rounded, f"adaptive_{len(specs) + 1:02d}")
            specs.append(CandidateViewSpec(name, normalized, up, source))
            if len(specs) >= self.max_views:
                break
        return specs

    def project_all(
        self,
        shape,
        feature_nodes: Optional[Iterable[Any]] = None,
        include_feature_axes: bool = False,
        exclude_names: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        # OCC imports are delayed so the registration math remains testable in
        # lightweight environments that do not install pythonocc-core.
        from OCC.Core.Bnd import Bnd_Box
        from OCC.Core.BRepBndLib import brepbndlib
        from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
        from OCC.Core.HLRAlgo import HLRAlgo_Projector
        from OCC.Core.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
        from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt

        from auto_2d_drawing.view_projector import ViewProjector

        extractor = ViewProjector()
        result: Dict[str, Any] = {}
        excluded = set(exclude_names or ())
        bbox_3d = Bnd_Box()
        brepbndlib.Add(shape, bbox_3d)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox_3d.Get()
        diagonal = math.sqrt(
            (xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2
        )
        linear_deflection = max(diagonal * 0.002, 0.01)
        mesher = BRepMesh_IncrementalMesh(
            shape,
            linear_deflection,
            False,
            0.35,
            True,
        )
        mesher.Perform()
        for spec in self.build_specs(feature_nodes, include_feature_axes=include_feature_axes):
            if spec.name in excluded:
                continue
            axis = gp_Ax2(
                gp_Pnt(0.0, 0.0, 0.0),
                gp_Dir(*spec.direction),
                gp_Dir(*spec.up),
            )
            hlr = HLRBRep_PolyAlgo()
            hlr.Load(shape)
            hlr.Projector(HLRAlgo_Projector(axis))
            hlr.Update()
            converted = HLRBRep_PolyHLRToShape()
            converted.Update(hlr)
            visible = extractor.extract_edges(converted.VCompound())
            hidden = extractor.extract_edges(converted.HCompound())
            try:
                visible.extend(extractor.extract_edges(converted.OutLineVCompound()))
                hidden.extend(extractor.extract_edges(converted.OutLineHCompound()))
            except Exception:
                pass
            bbox = extractor.get_edges_bbox(visible + hidden)
            result[spec.name] = {
                "visible": visible,
                "hidden": hidden,
                "bbox": bbox,
                "size": (max(bbox[2] - bbox[0], 0.01), max(bbox[3] - bbox[1], 0.01)),
                "projection": {
                    "method": self.METHOD,
                    "hlr_mode": "POLYGONAL_COARSE",
                    "linear_deflection": round(linear_deflection, 6),
                    "direction": list(spec.direction),
                    "up": list(spec.up),
                    "source": spec.source,
                },
            }
        return result

    @staticmethod
    def _normalize(vector: Any) -> Optional[Vector3D]:
        if not isinstance(vector, (list, tuple)) or len(vector) < 3:
            return None
        values = np.asarray(vector[:3], dtype=float)
        length = float(np.linalg.norm(values))
        if not math.isfinite(length) or length <= 1e-10:
            return None
        normalized = values / length
        return float(normalized[0]), float(normalized[1]), float(normalized[2])

    @classmethod
    def _plane_basis(cls, direction: Vector3D) -> Tuple[Vector3D, Vector3D]:
        normal = np.asarray(direction, dtype=float)
        candidates = np.eye(3)
        alignments = np.asarray([abs(float(np.dot(candidate, normal))) for candidate in candidates])
        seed = candidates[int(np.argmin(alignments))]
        up = seed - float(np.dot(seed, normal)) * normal
        up /= np.linalg.norm(up)
        side = np.cross(normal, up)
        side /= np.linalg.norm(side)
        return (
            (float(up[0]), float(up[1]), float(up[2])),
            (float(side[0]), float(side[1]), float(side[2])),
        )
