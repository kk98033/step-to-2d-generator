"""Leakage-safe benchmark utilities for the tolerance recommendation pipeline.

The benchmark deliberately separates four questions:

1. Can the DXF tolerance be extracted again from the source entity?
2. Can the 2D inference engine bind it to the STEP-verified feature class?
3. Can retrieval find a compatible historical case without seeing the same part?
4. Does the final recommendation reproduce the held-out tolerance?

Silver labels are created only from retrieval-eligible cases.  They are useful for
regression testing, but they are not a replacement for an engineer-signed gold set.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase
from auto_2d_drawing.tolerance.feature_graph import FeatureNode
from auto_2d_drawing.tolerance.tolerance_decision_service import ToleranceDecisionService


BENCHMARK_SCHEMA_VERSION = 1
DEFAULT_TOP_K = (1, 3, 5)
REVISION_SUFFIX = re.compile(r"-(?:R|A)(\d+)$", re.IGNORECASE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def base_part_number(source_name: str) -> str:
    """Return a revision-independent part key used to prevent data leakage."""
    stem = Path(str(source_name or "UNKNOWN")).stem.upper().strip()
    return REVISION_SUFFIX.sub("", stem) or "UNKNOWN"


def _stable_fraction(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def assign_group_splits(
    group_ids: Sequence[str],
    train_ratio: float = 0.70,
    validation_ratio: float = 0.15,
) -> Dict[str, str]:
    """Assign whole part groups to deterministic train/validation/test splits.

    Small datasets are forced to contain validation and test groups when at least
    three groups exist.  No drawing revision can appear in more than one split.
    """
    unique = sorted(set(group_ids), key=lambda item: (_stable_fraction(item), item))
    count = len(unique)
    if not count:
        return {}
    if count == 1:
        return {unique[0]: "test"}
    if count == 2:
        return {unique[0]: "train", unique[1]: "test"}

    train_count = max(1, int(round(count * train_ratio)))
    validation_count = max(1, int(round(count * validation_ratio)))
    if train_count + validation_count >= count:
        train_count = max(1, count - 2)
        validation_count = 1

    result: Dict[str, str] = {}
    for index, group_id in enumerate(unique):
        if index < train_count:
            split = "train"
        elif index < train_count + validation_count:
            split = "validation"
        else:
            split = "test"
        result[group_id] = split
    return result


def tolerance_deviations(config: Dict[str, Any]) -> Tuple[float, float]:
    config = config or {}
    mode = str(config.get("mode") or "NONE").upper()
    if mode == "CUSTOM_SYMMETRIC":
        dev = abs(_safe_float(config.get("dev"), _safe_float(config.get("upper_dev"))))
        return dev, -dev
    return _safe_float(config.get("upper_dev")), _safe_float(config.get("lower_dev"))


def tolerance_signature(config: Dict[str, Any], precision: int = 6) -> Tuple[Any, ...]:
    config = config or {}
    upper, lower = tolerance_deviations(config)
    return (
        str(config.get("mode") or "NONE").upper(),
        str(config.get("fit_class") or ""),
        round(upper, precision),
        round(lower, precision),
        bool(config.get("is_hole", False)),
    )


def tolerance_equal(first: Dict[str, Any], second: Dict[str, Any], atol: float = 1e-6) -> bool:
    a_mode, a_fit, a_upper, a_lower, a_hole = tolerance_signature(first, precision=9)
    b_mode, b_fit, b_upper, b_lower, b_hole = tolerance_signature(second, precision=9)
    if a_mode != b_mode or a_fit != b_fit:
        return False
    if a_mode == "FIT" and a_hole != b_hole:
        return False
    return abs(a_upper - b_upper) <= atol and abs(a_lower - b_lower) <= atol


@dataclass
class BenchmarkRecord:
    benchmark_id: str
    label_tier: str
    group_id: str
    split: str
    query: Dict[str, Any]
    expected: Dict[str, Any]
    source: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkDataset:
    name: str
    records: List[BenchmarkRecord]
    created_at: str = field(default_factory=_utc_now)
    schema_version: int = BENCHMARK_SCHEMA_VERSION
    label_policy: str = (
        "SILVER labels are derived from exact STEP/DXF evidence or engineer verification; "
        "reported accuracy is a proxy until a frozen engineer-signed GOLD set exists."
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "created_at": self.created_at,
            "label_policy": self.label_policy,
            "records": [record.to_dict() for record in self.records],
        }

    def save(self, path: os.PathLike[str] | str) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: os.PathLike[str] | str) -> "BenchmarkDataset":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            name=payload.get("name", "tolerance-benchmark"),
            created_at=payload.get("created_at", ""),
            schema_version=int(payload.get("schema_version", 0)),
            label_policy=payload.get("label_policy", ""),
            records=[BenchmarkRecord(**item) for item in payload.get("records", [])],
        )


def _dimension_category(case: ToleranceCase) -> str:
    metadata = case.source_metadata or {}
    category = str(metadata.get("dimension_category") or "").upper()
    if category:
        return category
    field_name = str(metadata.get("matched_nominal_field") or "")
    if field_name in {"diameter", "groove_diameter"}:
        return "DIAMETER"
    if field_name == "radius":
        return "RADIUS"
    if field_name == "angle":
        return "ANGULAR"
    return "LINEAR"


def _nominal_value(case: ToleranceCase) -> float:
    metadata = case.source_metadata or {}
    field_name = str(metadata.get("matched_nominal_field") or "")
    nominal = case.nominal_dimensions or {}
    if field_name and field_name in nominal:
        return _safe_float(nominal.get(field_name))
    category = _dimension_category(case)
    if category == "DIAMETER":
        return _safe_float(nominal.get("diameter", nominal.get("groove_diameter")))
    if category == "RADIUS":
        return _safe_float(nominal.get("radius"))
    for key in ("length", "groove_width", "chamfer_height", "step_height", "angle"):
        if key in nominal:
            return _safe_float(nominal[key])
    return 0.0


def _case_group(case: ToleranceCase) -> str:
    metadata = case.source_metadata or {}
    source = metadata.get("drawing_file") or case.evidence_source or case.case_id
    return f"part:{base_part_number(str(source))}"


def build_silver_dataset(
    cases: Iterable[ToleranceCase],
    name: str = "forcecon-tolerance-silver",
) -> BenchmarkDataset:
    """Build a deterministic silver dataset from trusted historical evidence."""
    eligible = [case for case in cases if case.is_retrieval_eligible()]
    group_splits = assign_group_splits([_case_group(case) for case in eligible])
    records: List[BenchmarkRecord] = []

    for case in sorted(eligible, key=lambda item: item.case_id):
        metadata = dict(case.source_metadata or {})
        group_id = _case_group(case)
        category = _dimension_category(case)
        label_tier = "GOLD" if case.effective_verification_status() == "ENGINEER_VERIFIED" else "SILVER"
        records.append(BenchmarkRecord(
            benchmark_id=f"benchmark:{case.case_id}",
            label_tier=label_tier,
            group_id=group_id,
            split=group_splits.get(group_id, "test"),
            query={
                "part_type": case.part_type,
                "product_family": metadata.get("product_family")
                or FeatureCaseBase.infer_product_family(str(metadata.get("drawing_file") or case.evidence_source)),
                "feature_type": case.feature_type,
                "inferred_role": case.inferred_role,
                "nominal_dimensions": case.nominal_dimensions,
                "nominal_value": _nominal_value(case),
                "neighbor_types": case.neighbor_types,
                "boundary_position": case.boundary_position,
                "dimension_category": category,
            },
            expected={
                "source_case_id": case.case_id,
                "feature_type": case.feature_type,
                "inferred_role": case.inferred_role,
                "dimension_category": category,
                "nominal_value": _nominal_value(case),
                "tolerance_config": case.tolerance_config,
            },
            source={
                "verification_status": case.effective_verification_status(),
                "evidence_source": case.evidence_source,
                "drawing_file": metadata.get("drawing_file") or case.evidence_source,
                "dxf_path": metadata.get("dxf_path"),
                "step_path": metadata.get("step_path"),
                "entity_handle": metadata.get("entity_handle"),
                "matched_feature_id": metadata.get("matched_feature_id"),
                "matched_nominal_field": metadata.get("matched_nominal_field"),
                "verification_method": metadata.get("verification_method"),
                "verification_checks": metadata.get("verification_checks", []),
                "feature_identity_verified": bool(metadata.get("feature_identity_verified")),
                "functional_role_verified": bool(metadata.get("functional_role_verified")),
            },
        ))
    return BenchmarkDataset(name=name, records=records)


def audit_split_leakage(records: Sequence[BenchmarkRecord]) -> Dict[str, Any]:
    group_splits: Dict[str, set[str]] = {}
    for record in records:
        group_splits.setdefault(record.group_id, set()).add(record.split)
    leaking = {
        group_id: sorted(splits)
        for group_id, splits in group_splits.items()
        if len(splits) > 1
    }
    split_counts: Dict[str, int] = {}
    group_counts: Dict[str, set[str]] = {}
    for record in records:
        split_counts[record.split] = split_counts.get(record.split, 0) + 1
        group_counts.setdefault(record.split, set()).add(record.group_id)
    return {
        "passed": not leaking,
        "leaking_groups": leaking,
        "record_counts": split_counts,
        "group_counts": {key: len(value) for key, value in group_counts.items()},
    }


def _record_node(record: BenchmarkRecord) -> FeatureNode:
    query = record.query
    nominal = dict(query.get("nominal_dimensions") or {})
    length = _safe_float(nominal.get("length", nominal.get("groove_width", nominal.get("chamfer_height"))))
    return FeatureNode(
        id=record.benchmark_id,
        feature_type=str(query.get("feature_type") or "unresolved_feature"),
        nominal=nominal,
        axial_span=[0.0, length],
        center_axial=length / 2.0,
        neighbor_types=list(query.get("neighbor_types") or []),
        boundary_position=str(query.get("boundary_position") or "INTERIOR"),
        inferred_role=query.get("inferred_role"),
    )


def _case_matches_record(case: ToleranceCase, record: BenchmarkRecord) -> bool:
    expected = record.expected
    if FeatureCaseBase.canonical_feature_type(case.feature_type) != FeatureCaseBase.canonical_feature_type(
        str(expected.get("feature_type") or "")
    ):
        return False
    if _dimension_category(case) != str(expected.get("dimension_category") or "").upper():
        return False
    return tolerance_equal(case.tolerance_config, expected.get("tolerance_config") or {})


def _dcg(binary_relevance: Sequence[int]) -> float:
    return sum(value / math.log2(index + 2) for index, value in enumerate(binary_relevance))


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _interval_iou(expected: Dict[str, Any], predicted: Dict[str, Any]) -> float:
    e_upper, e_lower = tolerance_deviations(expected)
    p_upper, p_lower = tolerance_deviations(predicted)
    intersection = max(0.0, min(e_upper, p_upper) - max(e_lower, p_lower))
    union = max(e_upper, p_upper) - min(e_lower, p_lower)
    if union <= 1e-12:
        return 1.0 if abs(e_upper - p_upper) <= 1e-9 and abs(e_lower - p_lower) <= 1e-9 else 0.0
    return intersection / union


def _calibration_metrics(samples: Sequence[Tuple[float, int]], bins: int = 10) -> Dict[str, Optional[float]]:
    if not samples:
        return {"brier_score": None, "expected_calibration_error": None}
    brier = sum((confidence - correct) ** 2 for confidence, correct in samples) / len(samples)
    ece = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        bucket = [item for item in samples if lower <= item[0] < upper or (index == bins - 1 and item[0] == 1.0)]
        if not bucket:
            continue
        avg_conf = sum(item[0] for item in bucket) / len(bucket)
        accuracy = sum(item[1] for item in bucket) / len(bucket)
        ece += (len(bucket) / len(samples)) * abs(avg_conf - accuracy)
    return {"brier_score": round(brier, 6), "expected_calibration_error": round(ece, 6)}


class ToleranceBenchmarkRunner:
    """Evaluate source extraction, feature linkage, retrieval and recommendation."""

    def __init__(
        self,
        cases: Sequence[ToleranceCase],
        dataset: BenchmarkDataset,
        top_k: Sequence[int] = DEFAULT_TOP_K,
    ):
        self.cases = list(cases)
        self.dataset = dataset
        self.top_k = tuple(sorted(set(int(value) for value in top_k if int(value) > 0))) or DEFAULT_TOP_K
        self.eligible_cases = [case for case in self.cases if case.is_retrieval_eligible()]

    def run(self, replay_sources: bool = True) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "generated_at": _utc_now(),
            "dataset": {
                "name": self.dataset.name,
                "created_at": self.dataset.created_at,
                "label_policy": self.dataset.label_policy,
                "record_count": len(self.dataset.records),
                "label_tier_counts": self._count(record.label_tier for record in self.dataset.records),
            },
            "case_base": self._case_base_audit(),
            "leakage_audit": audit_split_leakage(self.dataset.records),
            "protocol": {
                "source_replay": replay_sources,
                "retrieval_and_recommendation": "LEAVE_ONE_PART_GROUP_OUT",
                "group_key": "revision-normalized base part number",
                "top_k": list(self.top_k),
            },
        }
        if replay_sources:
            extraction, linkage = self._evaluate_source_replay()
        else:
            extraction = {"status": "SKIPPED", "reason": "source replay disabled"}
            linkage = {"status": "SKIPPED", "reason": "source replay disabled"}
        report["extraction"] = extraction
        report["feature_linkage_2d"] = linkage
        retrieval, recommendation, rows = self._evaluate_retrieval_and_recommendation()
        report["retrieval"] = retrieval
        report["recommendation"] = recommendation
        report["records"] = rows
        report["limitations"] = [
            "Silver labels are proxy ground truth generated from strict STEP/DXF matching.",
            "Functional roles marked functional_role_verified=false are not gold semantic labels.",
            "A frozen engineer-signed gold set is required before publishing production accuracy.",
        ]
        report["quality_gates"] = self._quality_gates(report)
        return report

    @staticmethod
    def _quality_gates(report: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate conservative regression gates suitable for CI.

        Linkage and recommendation accuracy are reported but not release gates
        until a frozen GOLD set exists. Safety, source replay and leakage can be
        enforced immediately without pretending silver labels are production
        truth.
        """
        checks: List[Dict[str, Any]] = []

        def add(name: str, passed: bool, actual: Any, requirement: str) -> None:
            checks.append({
                "name": name,
                "passed": bool(passed),
                "actual": actual,
                "requirement": requirement,
            })

        leakage_passed = bool((report.get("leakage_audit") or {}).get("passed"))
        add("no_group_leakage", leakage_passed, leakage_passed, "must be true")

        extraction = report.get("extraction") or {}
        if extraction.get("status") == "COMPLETED":
            found_rate = extraction.get("entity_found_rate")
            tolerance_accuracy = extraction.get("tolerance_exact_accuracy")
            add("source_entity_found_rate", found_rate is not None and found_rate >= 0.99, found_rate, ">= 0.99")
            add(
                "source_tolerance_exact_accuracy",
                tolerance_accuracy is not None and tolerance_accuracy >= 0.99,
                tolerance_accuracy,
                ">= 0.99",
            )

        recommendation = report.get("recommendation") or {}
        unsafe_rate = recommendation.get("unsafe_looser_rate")
        if unsafe_rate is not None:
            add("unsafe_looser_rate", unsafe_rate <= 0.01, unsafe_rate, "<= 0.01")

        return {
            "passed": all(item["passed"] for item in checks),
            "checks": checks,
            "note": "Accuracy gates require a frozen engineer-signed GOLD dataset.",
        }

    @staticmethod
    def _count(values: Iterable[str]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for value in values:
            counts[str(value)] = counts.get(str(value), 0) + 1
        return counts

    def _case_base_audit(self) -> Dict[str, Any]:
        status_counts = self._count(case.effective_verification_status() for case in self.cases)
        feature_counts = self._count(case.feature_type for case in self.cases)
        missing_source_handle = 0
        missing_source_path = 0
        for case in self.eligible_cases:
            metadata = case.source_metadata or {}
            if not metadata.get("entity_handle"):
                missing_source_handle += 1
            if not metadata.get("dxf_path"):
                missing_source_path += 1
        return {
            "total_cases": len(self.cases),
            "retrieval_eligible_cases": len(self.eligible_cases),
            "verification_status_counts": status_counts,
            "feature_counts": feature_counts,
            "eligible_missing_entity_handle": missing_source_handle,
            "eligible_missing_dxf_path": missing_source_path,
        }

    def _evaluate_source_replay(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        try:
            import ezdxf
            from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor
            from auto_2d_drawing.tolerance.feature_inference_2d import FeatureInference2DEngine
        except Exception as exc:
            skipped = {"status": "UNAVAILABLE", "reason": str(exc)}
            return skipped, dict(skipped)

        cache: Dict[str, Tuple[Any, Dict[str, Any]]] = {}
        extraction_rows: List[Dict[str, Any]] = []
        linkage_rows: List[Dict[str, Any]] = []
        extractor = DxfToleranceExtractor()

        for record in self.dataset.records:
            path = str(record.source.get("dxf_path") or "")
            handle = str(record.source.get("entity_handle") or "").upper()
            if not path or not os.path.exists(path):
                extraction_rows.append({"id": record.benchmark_id, "found": False, "reason": "SOURCE_NOT_FOUND"})
                linkage_rows.append({"id": record.benchmark_id, "evaluated": False, "reason": "SOURCE_NOT_FOUND"})
                continue
            try:
                if path not in cache:
                    doc = ezdxf.readfile(path)
                    msp = doc.modelspace()
                    extracted = extractor.extract_from_modelspace(
                        msp, os.path.basename(path), include_rejected=True, native_dimensions_only=True
                    )
                    cache[path] = (msp, {str(item.entity_handle).upper(): item for item in extracted})
                msp, dimensions = cache[path]
                item = dimensions.get(handle)
            except Exception as exc:
                extraction_rows.append({"id": record.benchmark_id, "found": False, "reason": f"READ_ERROR:{exc}"})
                linkage_rows.append({"id": record.benchmark_id, "evaluated": False, "reason": "READ_ERROR"})
                continue
            if item is None:
                extraction_rows.append({"id": record.benchmark_id, "found": False, "reason": "ENTITY_NOT_FOUND"})
                linkage_rows.append({"id": record.benchmark_id, "evaluated": False, "reason": "ENTITY_NOT_FOUND"})
                continue

            expected_tolerance = record.expected.get("tolerance_config") or {}
            category_correct = item.dimension_category == record.expected.get("dimension_category")
            nominal_error = abs(_safe_float(item.nominal_value) - _safe_float(record.expected.get("nominal_value")))
            tolerance_correct = tolerance_equal(item.tolerance_config, expected_tolerance)
            extraction_rows.append({
                "id": record.benchmark_id,
                "found": True,
                "category_correct": category_correct,
                "nominal_error_mm": nominal_error,
                "tolerance_correct": tolerance_correct,
            })

            try:
                inference = FeatureInference2DEngine(msp).infer(item)
                predicted_feature = inference.get("feature_type")
                expected_feature = record.expected.get("feature_type")
                linkage_rows.append({
                    "id": record.benchmark_id,
                    "evaluated": True,
                    "correct": predicted_feature == expected_feature,
                    "predicted_feature_type": predicted_feature,
                    "expected_feature_type": expected_feature,
                    "status": inference.get("status"),
                    "confidence": inference.get("confidence", 0.0),
                    "retrieval_eligible": bool(inference.get("retrieval_eligible")),
                })
            except Exception as exc:
                linkage_rows.append({"id": record.benchmark_id, "evaluated": False, "reason": f"INFERENCE_ERROR:{exc}"})

        found = [row for row in extraction_rows if row.get("found")]
        evaluated_linkage = [row for row in linkage_rows if row.get("evaluated")]
        extraction = {
            "status": "COMPLETED",
            "sample_count": len(extraction_rows),
            "entity_found_rate": round(len(found) / len(extraction_rows), 6) if extraction_rows else None,
            "dimension_category_accuracy": round(sum(bool(row.get("category_correct")) for row in found) / len(found), 6) if found else None,
            "nominal_mae_mm": round(_mean([row["nominal_error_mm"] for row in found]) or 0.0, 9) if found else None,
            "tolerance_exact_accuracy": round(sum(bool(row.get("tolerance_correct")) for row in found) / len(found), 6) if found else None,
            "failures": [row for row in extraction_rows if not row.get("found")],
        }
        linkage = {
            "status": "COMPLETED",
            "sample_count": len(linkage_rows),
            "evaluated_count": len(evaluated_linkage),
            "coverage": round(len(evaluated_linkage) / len(linkage_rows), 6) if linkage_rows else None,
            "feature_type_accuracy": round(sum(bool(row.get("correct")) for row in evaluated_linkage) / len(evaluated_linkage), 6) if evaluated_linkage else None,
            "auto_decision_rate": round(sum(row.get("status") == "AUTO_INFERRED_2D" for row in evaluated_linkage) / len(evaluated_linkage), 6) if evaluated_linkage else None,
            "retrieval_eligible_rate": round(sum(bool(row.get("retrieval_eligible")) for row in evaluated_linkage) / len(evaluated_linkage), 6) if evaluated_linkage else None,
            "confusion": self._count(
                f"{row.get('expected_feature_type')}->{row.get('predicted_feature_type') or 'UNRESOLVED'}"
                for row in evaluated_linkage
            ),
            "rows": linkage_rows,
        }
        return extraction, linkage

    def _evaluate_retrieval_and_recommendation(self) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
        max_k = max(self.top_k)
        recall_hits = {value: 0 for value in self.top_k}
        precision_sums = {value: 0.0 for value in self.top_k}
        evaluable = 0
        reciprocal_ranks: List[float] = []
        ndcgs: List[float] = []
        recommendation_rows: List[Dict[str, Any]] = []
        detail_rows: List[Dict[str, Any]] = []

        for record in self.dataset.records:
            train_cases = [case for case in self.eligible_cases if _case_group(case) != record.group_id]
            relevant_ids = {case.case_id for case in train_cases if _case_matches_record(case, record)}
            base = FeatureCaseBase.__new__(FeatureCaseBase)
            base.db_path = ""
            base.cases = train_cases
            node = _record_node(record)
            matches = base.search_similar_cases_detailed(
                node,
                part_type=str(record.query.get("part_type") or "GENERAL"),
                top_k=max_k,
                product_family=str(record.query.get("product_family") or ""),
                dimension_category=str(record.query.get("dimension_category") or ""),
            )
            retrieved_ids = [item["case"].case_id for item in matches]
            relevance = [1 if case_id in relevant_ids else 0 for case_id in retrieved_ids]
            first_rank = next((index + 1 for index, value in enumerate(relevance) if value), None)
            if relevant_ids:
                evaluable += 1
                reciprocal_ranks.append(1.0 / first_rank if first_rank else 0.0)
                ideal = [1] * min(len(relevant_ids), max_k)
                ndcgs.append(_dcg(relevance[:max_k]) / _dcg(ideal) if ideal else 0.0)
                for k in self.top_k:
                    hits = sum(relevance[:k])
                    recall_hits[k] += int(hits > 0)
                    precision_sums[k] += hits / k

            category = self._service_category(record)
            is_diameter = str(record.query.get("dimension_category") or "").upper() == "DIAMETER"
            service = ToleranceDecisionService(case_base=base)
            recommendation = service._evaluate_recommendation(
                record.benchmark_id,
                category,
                is_diameter,
                _safe_float(record.query.get("nominal_value")),
                node,
                str(record.query.get("part_type") or "GENERAL"),
                product_family=str(record.query.get("product_family") or ""),
                dimension_category=str(record.query.get("dimension_category") or ""),
            ).to_dict()
            predicted_config = dict(recommendation.get("tolerance_config") or {})
            expected_config = dict(record.expected.get("tolerance_config") or {})
            exact = tolerance_equal(predicted_config, expected_config)
            p_upper, p_lower = tolerance_deviations(predicted_config)
            e_upper, e_lower = tolerance_deviations(expected_config)
            predicted_width = p_upper - p_lower
            expected_width = e_upper - e_lower
            rec_row = {
                "id": record.benchmark_id,
                "correct": exact,
                "mode_correct": str(predicted_config.get("mode") or "NONE").upper() == str(expected_config.get("mode") or "NONE").upper(),
                "fit_class_correct": str(predicted_config.get("fit_class") or "") == str(expected_config.get("fit_class") or ""),
                "upper_error_mm": abs(p_upper - e_upper),
                "lower_error_mm": abs(p_lower - e_lower),
                "interval_iou": _interval_iou(expected_config, predicted_config),
                "unsafe_looser": predicted_width > expected_width + 1e-9,
                "over_tight": predicted_width + 1e-9 < expected_width,
                "abstained": recommendation.get("decision_status") == "REVIEW_REQUIRED",
                "confidence": min(1.0, max(0.0, _safe_float(recommendation.get("confidence")))),
                "tier_level": recommendation.get("tier_level"),
                "decision_status": recommendation.get("decision_status"),
                "expected": expected_config,
                "predicted": predicted_config,
            }
            recommendation_rows.append(rec_row)
            detail_rows.append({
                "benchmark_id": record.benchmark_id,
                "group_id": record.group_id,
                "split": record.split,
                "train_case_count": len(train_cases),
                "relevant_case_count": len(relevant_ids),
                "retrieved_case_ids": retrieved_ids,
                "first_relevant_rank": first_rank,
                "recommendation": rec_row,
            })

        retrieval: Dict[str, Any] = {
            "protocol": "LEAVE_ONE_PART_GROUP_OUT",
            "query_count": len(self.dataset.records),
            "evaluable_query_count": evaluable,
            "evaluable_coverage": round(evaluable / len(self.dataset.records), 6) if self.dataset.records else None,
            "mrr": round(_mean(reciprocal_ranks) or 0.0, 6) if reciprocal_ranks else None,
            "ndcg_at_max_k": round(_mean(ndcgs) or 0.0, 6) if ndcgs else None,
        }
        for k in self.top_k:
            retrieval[f"recall_at_{k}"] = round(recall_hits[k] / evaluable, 6) if evaluable else None
            retrieval[f"precision_at_{k}"] = round(precision_sums[k] / evaluable, 6) if evaluable else None

        total = len(recommendation_rows)
        non_abstained = [row for row in recommendation_rows if not row["abstained"]]
        display_calibration = _calibration_metrics(
            [(row["confidence"], int(row["correct"])) for row in recommendation_rows]
        )
        decision_calibration = _calibration_metrics(
            [(row["confidence"], int(row["correct"])) for row in non_abstained]
        )
        recommendation_report: Dict[str, Any] = {
            "sample_count": total,
            "exact_tolerance_accuracy": round(sum(row["correct"] for row in recommendation_rows) / total, 6) if total else None,
            "mode_accuracy": round(sum(row["mode_correct"] for row in recommendation_rows) / total, 6) if total else None,
            "fit_class_accuracy": round(sum(row["fit_class_correct"] for row in recommendation_rows) / total, 6) if total else None,
            "upper_deviation_mae_mm": round(_mean([row["upper_error_mm"] for row in recommendation_rows]) or 0.0, 9) if total else None,
            "lower_deviation_mae_mm": round(_mean([row["lower_error_mm"] for row in recommendation_rows]) or 0.0, 9) if total else None,
            "mean_interval_iou": round(_mean([row["interval_iou"] for row in recommendation_rows]) or 0.0, 6) if total else None,
            "unsafe_looser_rate": round(sum(row["unsafe_looser"] for row in non_abstained) / len(non_abstained), 6) if non_abstained else None,
            "over_tight_rate": round(sum(row["over_tight"] for row in non_abstained) / len(non_abstained), 6) if non_abstained else None,
            "abstention_rate": round(sum(row["abstained"] for row in recommendation_rows) / total, 6) if total else None,
            "non_abstained_coverage": round(len(non_abstained) / total, 6) if total else None,
            "selective_accuracy": round(sum(row["correct"] for row in non_abstained) / len(non_abstained), 6) if non_abstained else None,
            "tier_counts": self._count(str(row["tier_level"]) for row in recommendation_rows),
            "decision_status_counts": self._count(str(row["decision_status"]) for row in recommendation_rows),
            "display_brier_score": display_calibration["brier_score"],
            "display_expected_calibration_error": display_calibration["expected_calibration_error"],
            "decision_brier_score": decision_calibration["brier_score"],
            "decision_expected_calibration_error": decision_calibration["expected_calibration_error"],
        }
        return retrieval, recommendation_report, detail_rows

    @staticmethod
    def _service_category(record: BenchmarkRecord) -> str:
        feature_type = str(record.query.get("feature_type") or "")
        category = str(record.query.get("dimension_category") or "").upper()
        if feature_type == "retaining_ring_groove":
            return "groove"
        if feature_type == "locating_shoulder":
            return "step"
        if feature_type == "pilot_chamfer":
            return "chamfer"
        if category == "DIAMETER":
            return "hole" if feature_type == "hole" else "shaft"
        return "linear"


def save_report(report: Dict[str, Any], path: os.PathLike[str] | str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
