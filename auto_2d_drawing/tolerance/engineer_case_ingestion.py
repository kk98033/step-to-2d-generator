"""Validated ingestion of tolerances confirmed on known 3D features."""

import hashlib
import math
from typing import Any, Dict, Optional

from auto_2d_drawing.canonical_features import CANONICAL_FEATURE_SCHEMA
from auto_2d_drawing.tolerance.canonical_feature_graph import CanonicalFeatureGraphExtractor
from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase


class EngineerCaseValidationError(ValueError):
    pass


class EngineerConfirmedCaseService:
    """Store a UI-confirmed tolerance after revalidating its feature on STEP."""

    ALLOWED_MODES = {"FIT", "CUSTOM_SYMMETRIC", "CUSTOM_LIMITS", "GROOVE"}

    def __init__(self, case_base: Optional[FeatureCaseBase] = None):
        self.case_base = case_base or FeatureCaseBase()
        self.graph_extractor = CanonicalFeatureGraphExtractor()

    @staticmethod
    def _validate_tolerance_config(config: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(config or {})
        mode = str(normalized.get("mode") or "").upper()
        if mode not in EngineerConfirmedCaseService.ALLOWED_MODES:
            raise EngineerCaseValidationError(f"Unsupported tolerance mode: {mode or 'EMPTY'}")
        normalized["mode"] = mode
        if mode == "FIT" and not str(normalized.get("fit_class") or "").strip():
            raise EngineerCaseValidationError("FIT tolerance requires fit_class")
        numeric_fields = {
            "CUSTOM_SYMMETRIC": ("dev",),
            "CUSTOM_LIMITS": ("upper_dev", "lower_dev"),
            "GROOVE": ("upper_dev", "lower_dev"),
        }.get(mode, ())
        for field_name in numeric_fields:
            value = normalized.get(field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise EngineerCaseValidationError(f"{mode} tolerance requires numeric {field_name}")
        if mode == "CUSTOM_SYMMETRIC" and float(normalized["dev"]) < 0.0:
            raise EngineerCaseValidationError("CUSTOM_SYMMETRIC dev must be non-negative")
        return normalized

    @staticmethod
    def _resolve_nominal_field(
        nominal: Dict[str, Any],
        requested_field: Optional[str],
        requested_value: Optional[float],
    ) -> tuple[str, float]:
        numeric = {
            key: float(value)
            for key, value in nominal.items()
            if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))
        }
        if requested_field:
            if requested_field not in numeric:
                raise EngineerCaseValidationError(
                    f"Nominal field {requested_field!r} is not present on the selected feature"
                )
            field_name = requested_field
        elif requested_value is not None:
            matches = [
                key for key, value in numeric.items()
                if abs(value - float(requested_value)) <= max(0.005, abs(value) * 0.0005)
            ]
            if len(matches) != 1:
                raise EngineerCaseValidationError("nominal_field is required when nominal value is ambiguous")
            field_name = matches[0]
        elif len(numeric) == 1:
            field_name = next(iter(numeric))
        else:
            raise EngineerCaseValidationError("nominal_field is required for this feature")

        canonical_value = numeric[field_name]
        if requested_value is not None:
            threshold = max(0.005, abs(canonical_value) * 0.0005)
            if abs(canonical_value - float(requested_value)) > threshold:
                raise EngineerCaseValidationError(
                    f"Nominal value does not match STEP feature {field_name}={canonical_value:g}"
                )
        return field_name, canonical_value

    def confirm(
        self,
        *,
        shape,
        model_id: str,
        part_id: str,
        feature_id: str,
        dimension_category: str,
        tolerance_config: Dict[str, Any],
        engineer_id: str,
        nominal_field: Optional[str] = None,
        nominal_value: Optional[float] = None,
        part_type: Optional[str] = None,
        product_family: Optional[str] = None,
        rule_id: Optional[str] = None,
        drawing_file: Optional[str] = None,
        description: Optional[str] = None,
    ) -> ToleranceCase:
        if not feature_id:
            raise EngineerCaseValidationError("feature_id is required")
        category = FeatureCaseBase.canonical_dimension_category(dimension_category)
        if not category:
            raise EngineerCaseValidationError("dimension_category is required")

        graph = self.graph_extractor.build_graph(shape, part_type=part_type)
        node = graph.get_node(feature_id)
        if node is None:
            raise EngineerCaseValidationError(
                f"Feature {feature_id!r} is not present in the canonical STEP extraction"
            )
        field_name, canonical_value = self._resolve_nominal_field(
            node.nominal, nominal_field, nominal_value
        )
        normalized_tolerance = self._validate_tolerance_config(tolerance_config)
        canonical_record = dict((node.source_info or {}).get("canonical_record") or {})
        identity_text = "|".join([
            model_id, part_id, feature_id, field_name, category, drawing_file or "",
        ])
        digest = hashlib.sha1(identity_text.encode("utf-8")).hexdigest()[:12]
        case = ToleranceCase(
            case_id=f"ENG_{part_id}_{feature_id}_{digest}",
            part_type=graph.part_type,
            feature_type=node.feature_type,
            inferred_role=node.inferred_role or str(canonical_record.get("role") or "GENERAL_FEATURE").upper(),
            nominal_dimensions=node.nominal,
            neighbor_types=node.neighbor_types,
            boundary_position=node.boundary_position,
            tolerance_config=normalized_tolerance,
            confidence=1.0,
            evidence_source="ENGINEER_CONFIRMED",
            description=description or (
                f"Engineer-confirmed {category} tolerance on {part_id}/{feature_id}.{field_name}."
            ),
            verification_status="ENGINEER_VERIFIED",
            source_metadata={
                "schema_version": 3,
                "feature_taxonomy": CANONICAL_FEATURE_SCHEMA,
                "model_id": model_id,
                "part_id": part_id,
                "product_family": product_family or FeatureCaseBase.infer_product_family(part_id),
                "drawing_file": drawing_file,
                "engineer_id": engineer_id,
                "rule_id": rule_id,
                "dimension_category": category,
                "canonical_feature_id": feature_id,
                "matched_feature_id": feature_id,
                "matched_nominal_field": field_name,
                "matched_nominal_value": canonical_value,
                "feature_snapshot": canonical_record,
                "verification_method": "ENGINEER_UI_FEATURE_CONFIRMATION",
                "verification_checks": [
                    "feature_reextracted_from_step",
                    "canonical_feature_id_exists",
                    "nominal_value_matches_step_geometry",
                    "engineer_explicit_confirmation",
                ],
                "feature_identity_verified": True,
                "functional_role_verified": True,
            },
        )
        self.case_base.add_case(case)
        return case
