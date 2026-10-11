"""Rebuild CURRENT_FINAL tolerance evidence and atomically deploy the web case base.

The production JSON is never edited in place.  A complete staging database is
validated first, the existing production artifacts are copied to a timestamped
backup directory, and only then is the active case base replaced atomically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
LAB_ROOT = PROJECT_ROOT / "experimental_lab" / "tolerance_extraction_audit"
DEFAULT_CONFIG = LAB_ROOT / "CURRENT_FINAL" / "database_full_extract_config.json"
DEFAULT_PRODUCTION_DB = PROJECT_ROOT / "auto_2d_drawing" / "tolerance" / "data" / "feature_case_base.json"
DEFAULT_PRODUCTION_REPORT = PROJECT_ROOT / "auto_2d_drawing" / "tolerance" / "data" / "feature_case_base_rebuild_report.json"
DEFAULT_PRODUCTION_MANIFEST = PROJECT_ROOT / "auto_2d_drawing" / "tolerance" / "data" / "historical_pair_manifest.json"
AUTO_STATUSES = {"AUTO_VERIFIED", "AUTO_EXTRACTED", "REVIEW_CANDIDATE"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(temporary.read_text(encoding="utf-8"))
    os.replace(temporary, path)


def _configure_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tolerance-case-rebuild")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def _run_stage(
    name: str,
    command: list[str],
    cwd: Path,
    timeout_seconds: float,
    logger: logging.Logger,
) -> None:
    logger.info("STAGE_START | %s | %s", name, subprocess.list2cmdline(command))
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output_queue.put(line.rstrip())
        output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    stream_finished = False
    while process.poll() is None or not stream_finished:
        if time.monotonic() - started > timeout_seconds:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            raise TimeoutError(f"Stage {name} exceeded {timeout_seconds:.0f} seconds")
        try:
            line = output_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        if line is None:
            stream_finished = True
        elif line:
            logger.info("%s | %s", name, line)
    return_code = process.wait()
    elapsed = time.monotonic() - started
    if return_code != 0:
        raise RuntimeError(f"Stage {name} failed with exit code {return_code}")
    logger.info("STAGE_DONE | %s | %.2fs", name, elapsed)


def _raw_dimension_index(extraction: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result = {}
    for drawing in extraction.get("drawings") or []:
        if not drawing.get("is_canonical_drawing_occurrence"):
            continue
        dxf_key = str(Path(drawing["dxf_path"]).resolve()).casefold()
        for dimension in drawing.get("dimensions") or []:
            result[(dxf_key, str(dimension.get("entity_handle") or "").upper())] = dimension
    return result


def _nominal_dimensions(row: dict[str, Any]) -> dict[str, float]:
    value = abs(float(row.get("nominal_value", 0.0) or 0.0))
    category = str(row.get("dimension_category") or "").upper()
    field = str(row.get("nominal_field") or "").strip()
    canonical_field = field
    if category == "DIAMETER" or "diameter" in field.lower():
        canonical_field = canonical_field or "diameter"
        result = {"diameter": value}
    elif category == "RADIUS" or field.lower() == "radius":
        canonical_field = canonical_field or "radius"
        result = {"radius": value}
    elif category == "ANGULAR" or "angle" in field.lower():
        canonical_field = canonical_field or "angle"
        result = {"angle": value}
    else:
        canonical_field = canonical_field or "length"
        result = {"length": value}
    if canonical_field and canonical_field not in result:
        result[canonical_field] = value
    return result


def _part_type(feature_type: str, family: str) -> str:
    feature = feature_type.lower()
    if feature in {"shaft_segment", "retaining_ring_groove", "locating_shoulder", "pilot_chamfer", "transition_fillet", "compound_axial_span"}:
        return "SHAFT"
    if feature in {"annular_hub", "annular_rim", "annular_interface_gap", "derived_rotational_envelope_feature", "repeated_radial_pattern"}:
        return "FAN"
    if "assembly" in feature or "component" in feature:
        return "ASSEMBLY"
    if "軸流扇" in family:
        return "FAN"
    return "GENERAL"


def _resolved_feature_type(row: dict[str, Any]) -> tuple[str, str]:
    """Normalize specialized feature paths without model-specific part names."""
    explicit = str(row.get("feature_type") or "").strip()
    if explicit:
        return explicit, "EXPLICIT"
    semantic = " ".join((
        str(row.get("feature_id") or ""),
        str(row.get("feature_role") or ""),
        str(row.get("nominal_field") or ""),
    )).lower()
    rules = (
        (("rotational_envelope", "outer_envelope", "projected_envelope", "sectional_outer_envelope"), "derived_rotational_envelope_feature"),
        (("assembly.axial_span", "cross_component", "assembly_span"), "assembly_relation_feature"),
        (("composite_axial", "compound_axial"), "compound_axial_span"),
        (("bbox_overall", "overall_dimension", "overall.length"), "overall_dimension"),
        (("annular_rim", "annular_hub"), "annular_rim"),
        (("annular_interface_gap",), "annular_interface_gap"),
        (("groove",), "retaining_ring_groove"),
        (("chamfer",), "pilot_chamfer"),
        (("fillet",), "transition_fillet"),
        (("hole", "inner_cylindrical_surface"), "hole"),
        (("step", "shoulder"), "locating_shoulder"),
        (("shaft", "journal", "outer_cylindrical_surface"), "shaft_segment"),
    )
    for markers, feature_type in rules:
        if any(marker in semantic for marker in markers):
            return feature_type, "SEMANTIC_PATH_NORMALIZATION"
    return "unresolved_feature", "UNRESOLVED"


def _compact_geometry(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    association = dict(payload.get("association") or {})
    registration = dict(payload.get("view_registration") or {})
    return {
        "method": payload.get("method"),
        "status": payload.get("status"),
        "passed": bool(payload.get("passed")),
        "score": payload.get("score"),
        "checks": dict(payload.get("checks") or {}),
        "association": {
            "method": association.get("method"),
            "association_status": association.get("association_status"),
            "association_confidence": association.get("association_confidence"),
            "attached_geometry_handles": association.get("attached_geometry_handles", []),
            "common_attachment_handles": association.get("common_attachment_handles", []),
            "view_ids": association.get("view_ids", []),
            "primary_view_id": association.get("primary_view_id"),
            "recovered_dimension_geometry": association.get("recovered_dimension_geometry"),
            "dimension_target_edges": association.get("dimension_target_edges", []),
        },
        "view_registration": {
            key: registration.get(key)
            for key in (
                "step_view", "dxf_view_id", "transform", "score", "contour_score",
                "normalized_chamfer", "inlier_ratio", "source_coverage",
                "target_coverage", "ranked_hausdorff", "extent_score",
            )
            if key in registration
        },
        "topology_mapping": payload.get("topology_mapping"),
    }


def _stable_case_id(row: dict[str, Any]) -> str:
    identity = "|".join((
        str(Path(row["dxf_path"]).resolve()).casefold(),
        str(row.get("entity_handle") or "").upper(),
        str(row.get("feature_id") or ""),
        str(row.get("nominal_field") or ""),
    ))
    return f"CURRENT_FINAL_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24].upper()}"


def build_cases(
    extraction: dict[str, Any],
    feature_report: dict[str, Any],
    existing_cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_by_key = _raw_dimension_index(extraction)
    preserved = [
        row for row in existing_cases
        if str(row.get("verification_status") or "UNVERIFIED").upper() not in AUTO_STATUSES
    ]
    generated = []
    skipped = []
    for row in feature_report.get("tolerances") or []:
        value = abs(float(row.get("nominal_value", 0.0) or 0.0))
        tolerance = dict(row.get("tolerance_config") or {})
        if value <= 0.0 or str(tolerance.get("mode") or "NONE").upper() == "NONE":
            skipped.append({"drawing": row.get("drawing"), "handle": row.get("entity_handle"), "reason": "INVALID_NOMINAL_OR_TOLERANCE"})
            continue
        key = (
            str(Path(row["dxf_path"]).resolve()).casefold(),
            str(row.get("entity_handle") or "").upper(),
        )
        raw = raw_by_key.get(key, {})
        status = str(row.get("status") or "UNRESOLVED").upper()
        if status == "VERIFIED":
            verification_status = "AUTO_VERIFIED"
        elif status == "CANDIDATE_ONLY":
            verification_status = "REVIEW_CANDIDATE"
        else:
            verification_status = "AUTO_EXTRACTED"
        feature_type, feature_type_resolution = _resolved_feature_type(row)
        feature_role = str(row.get("feature_role") or (
            f"UNRESOLVED_{str(row.get('dimension_category') or 'DIMENSION').upper()}"
            if status == "UNRESOLVED" else "GENERAL_FEATURE"
        ))
        pair = dict(row.get("pair") or {})
        geometry = _compact_geometry(row.get("geometry_verification"))
        policy = dict(row.get("case_retrieval") or {})
        feature_verified = status == "VERIFIED" and bool(geometry and geometry.get("passed"))
        confidence = float(row.get("confidence", 0.0) or 0.0)
        if verification_status == "REVIEW_CANDIDATE":
            confidence = min(confidence or 0.65, 0.79)
        elif verification_status == "AUTO_EXTRACTED":
            confidence = min(float(raw.get("extraction_confidence", 0.5) or 0.5), 0.60)
        generated.append({
            "case_id": _stable_case_id(row),
            "part_type": _part_type(feature_type, str(row.get("family") or "")),
            "feature_type": feature_type,
            "inferred_role": feature_role,
            "nominal_dimensions": _nominal_dimensions(row),
            "neighbor_types": list(
                ((geometry or {}).get("topology_mapping") or {}).get("neighbor_types") or []
            ),
            "boundary_position": str(
                ((geometry or {}).get("topology_mapping") or {}).get("boundary_position") or "INTERIOR"
            ),
            "tolerance_config": tolerance,
            "confidence": round(max(0.0, min(confidence, 1.0)), 6),
            "evidence_source": str(row.get("drawing") or Path(row["dxf_path"]).name),
            "description": (
                f"CURRENT_FINAL {status}: {row.get('reason') or 'historical DXF tolerance evidence'}"
            ),
            "verification_status": verification_status,
            "source_metadata": {
                "schema_version": 3,
                "pipeline": "CURRENT_FINAL_DATABASE_REBUILD",
                "product_family": row.get("family"),
                "drawing_file": row.get("drawing"),
                "dxf_path": row.get("dxf_path"),
                "entity_handle": row.get("entity_handle"),
                "raw_text": raw.get("raw_text"),
                "layer": raw.get("layer"),
                "points": raw.get("points"),
                "source_entity_type": raw.get("source_entity_type"),
                "dimension_category": row.get("dimension_category"),
                "canonical_feature_id": row.get("feature_id"),
                "matched_feature_id": row.get("feature_id"),
                "matched_nominal_field": row.get("nominal_field"),
                "feature_role": row.get("feature_role"),
                "feature_type_resolution": feature_type_resolution,
                "feature_identity_verified": feature_verified,
                "candidate_feature_link": status == "CANDIDATE_ONLY",
                "candidate_feature_types": sorted({
                    str(item.get("feature_type"))
                    for item in row.get("alternatives") or []
                    if item.get("feature_type")
                }),
                "pair_method": pair.get("pair_method"),
                "step_path": pair.get("step_path"),
                "component_name": pair.get("component_name"),
                "component_label_entry": pair.get("component_label_entry"),
                "geometry_verification": geometry,
                "recommendation_tier": policy.get("tier"),
                "engineer_review_required": verification_status != "AUTO_VERIFIED",
                "specialized_current_final_source": row.get("specialized_current_final_source"),
                "unresolved_reason": row.get("reason") if status == "UNRESOLVED" else None,
            },
        })
    cases_by_id = {row["case_id"]: row for row in [*preserved, *generated]}
    cases = sorted(cases_by_id.values(), key=lambda row: (row.get("evidence_source", ""), row["case_id"]))
    status_counts = Counter(str(row.get("verification_status") or "UNVERIFIED") for row in cases)
    feature_counts = Counter(str(row.get("feature_type") or "UNKNOWN") for row in cases)
    summary = {
        "source_tolerance_count": len(feature_report.get("tolerances") or []),
        "generated_case_count": len(generated),
        "preserved_non_auto_case_count": len(preserved),
        "skipped_count": len(skipped),
        "total_deployed_case_count": len(cases),
        "verification_status_counts": dict(sorted(status_counts.items())),
        "feature_type_counts": dict(sorted(feature_counts.items())),
        "skipped": skipped[:100],
    }
    return cases, summary


def validate_case_base(path: Path, expected_count: int) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != expected_count:
        raise ValueError(f"Staging case count mismatch: {len(payload) if isinstance(payload, list) else 'not-list'} != {expected_count}")
    required = {
        "case_id", "part_type", "feature_type", "inferred_role",
        "nominal_dimensions", "tolerance_config", "confidence",
        "evidence_source", "verification_status", "source_metadata",
    }
    duplicate_ids = len(payload) - len({row.get("case_id") for row in payload})
    invalid = [index for index, row in enumerate(payload) if required - set(row)]
    if duplicate_ids or invalid:
        raise ValueError(f"Invalid staging database: duplicates={duplicate_ids}, invalid_rows={invalid[:10]}")
    # Import through the same runtime loader used by FastAPI.
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from auto_2d_drawing.tolerance.case_base import FeatureCaseBase
    loaded = FeatureCaseBase(str(path))
    if len(loaded.cases) != expected_count:
        raise ValueError(f"Web loader accepted {len(loaded.cases)}/{expected_count} cases")
    return {
        "json_valid": True,
        "schema_valid": True,
        "web_runtime_load_count": len(loaded.cases),
        "duplicate_case_ids": duplicate_ids,
    }


def _backup(paths: list[Path], backup_dir: Path, logger: logging.Logger) -> list[dict[str, Any]]:
    backup_dir.mkdir(parents=True, exist_ok=False)
    backed_up = []
    for path in paths:
        if not path.is_file():
            continue
        destination = backup_dir / path.name
        shutil.copy2(path, destination)
        backed_up.append({
            "source": str(path),
            "backup": str(destination),
            "size": destination.stat().st_size,
        })
        logger.info("BACKUP | %s -> %s", path, destination)
    return backed_up


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild and deploy the CURRENT_FINAL tolerance case base")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--production-db", type=Path, default=DEFAULT_PRODUCTION_DB)
    parser.add_argument("--production-report", type=Path, default=DEFAULT_PRODUCTION_REPORT)
    parser.add_argument("--production-manifest", type=Path, default=DEFAULT_PRODUCTION_MANIFEST)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--model-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--stage-timeout-seconds", type=float, default=43200.0)
    parser.add_argument("--global-max-components", type=int)
    parser.add_argument("--specialized-report", type=Path)
    parser.add_argument("--specialized-config", type=Path)
    parser.add_argument("--reuse-extraction-report", type=Path)
    parser.add_argument("--reuse-feature-report", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()

    stamp = _timestamp()
    run_dir = (args.run_dir or (LAB_ROOT / "experiments" / datetime.now().strftime("%Y-%m-%d") / f"production_rebuild_{stamp}")).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    logger = _configure_logging(run_dir / "pipeline.log")
    summary_path = run_dir / "deployment_summary.json"
    summary: dict[str, Any] = {
        "schema_version": 1,
        "started_at_utc": _utc_now(),
        "status": "RUNNING",
        "dry_run": bool(args.dry_run),
        "run_dir": str(run_dir),
        "production_db": str(args.production_db.resolve()),
        "model_timeout_seconds": args.model_timeout_seconds,
        "stage_timeout_seconds": args.stage_timeout_seconds,
    }
    _atomic_json(summary_path, summary)
    try:
        extraction_dir = run_dir / "01_extraction"
        feature_dir = run_dir / "02_feature_link"
        extraction_report = args.reuse_extraction_report.resolve() if args.reuse_extraction_report else extraction_dir / "database_tolerance_full_report.json"
        feature_report = args.reuse_feature_report.resolve() if args.reuse_feature_report else feature_dir / "database_feature_link_report.json"
        if not args.reuse_extraction_report:
            _run_stage(
                "DXF_EXTRACTION",
                [
                    sys.executable,
                    str(LAB_ROOT / "common" / "scripts" / "run_database_tolerance_full_extract.py"),
                    "--config", str(args.config.resolve()),
                    "--output-dir", str(extraction_dir),
                    "--workers", str(args.workers),
                    "--drawing-timeout-seconds", str(args.model_timeout_seconds),
                ],
                PROJECT_ROOT,
                args.stage_timeout_seconds,
                logger,
            )
        if not args.reuse_feature_report:
            command = [
                sys.executable,
                str(LAB_ROOT / "common" / "scripts" / "run_database_feature_link_batch.py"),
                "--config", str(args.config.resolve()),
                "--extraction-report", str(extraction_report),
                "--output-dir", str(feature_dir),
                "--workers", str(args.workers),
                "--pair-timeout-seconds", str(args.model_timeout_seconds),
            ]
            if args.global_max_components is not None:
                command.extend(["--global-max-components", str(args.global_max_components)])
            try:
                _run_stage("FEATURE_LINK", command, PROJECT_ROOT, args.stage_timeout_seconds, logger)
            except RuntimeError:
                if not args.allow_partial or not feature_report.is_file():
                    raise
                logger.warning("FEATURE_LINK returned errors; continuing because --allow-partial was supplied")
            checkpoint = feature_dir / "pair_results_checkpoint.json"
            if checkpoint.is_file():
                finalize = [
                    sys.executable,
                    str(LAB_ROOT / "common" / "scripts" / "finalize_database_feature_link_report.py"),
                    "--config", str(args.config.resolve()),
                    "--extraction-report", str(extraction_report),
                    "--report", str(feature_report),
                    "--checkpoint", str(checkpoint),
                ]
                if args.specialized_report and args.specialized_config:
                    finalize.extend([
                        "--specialized-report", str(args.specialized_report.resolve()),
                        "--specialized-config", str(args.specialized_config.resolve()),
                    ])
                _run_stage("FINALIZE_TRUST_POLICY", finalize, PROJECT_ROOT, args.stage_timeout_seconds, logger)

        extraction = json.loads(extraction_report.read_text(encoding="utf-8"))
        feature = json.loads(feature_report.read_text(encoding="utf-8"))
        existing_cases = []
        production_db = args.production_db.resolve()
        if production_db.is_file():
            existing_cases = json.loads(production_db.read_text(encoding="utf-8"))
        cases, case_summary = build_cases(extraction, feature, existing_cases)
        staging_db = run_dir / "03_staging" / "feature_case_base.json"
        _atomic_json(staging_db, cases)
        validation = validate_case_base(staging_db, len(cases))
        logger.info(
            "STAGING_VALID | total=%d verified=%d candidates=%d unresolved=%d preserved=%d",
            len(cases),
            case_summary["verification_status_counts"].get("AUTO_VERIFIED", 0),
            case_summary["verification_status_counts"].get("REVIEW_CANDIDATE", 0),
            case_summary["verification_status_counts"].get("AUTO_EXTRACTED", 0),
            case_summary["preserved_non_auto_case_count"],
        )
        summary.update({
            "extraction_report": str(extraction_report),
            "feature_report": str(feature_report),
            "staging_db": str(staging_db),
            "case_database": case_summary,
            "validation": validation,
        })
        if args.dry_run:
            summary["status"] = "DRY_RUN_VALIDATED"
            summary["finished_at_utc"] = _utc_now()
            _atomic_json(summary_path, summary)
            logger.info("DRY_RUN_DONE | production database was not changed")
            return 0

        backup_dir = production_db.parent / "backups" / stamp
        backed_up = _backup(
            [production_db, args.production_report.resolve(), args.production_manifest.resolve()],
            backup_dir,
            logger,
        )
        production_payload = run_dir / "03_staging" / "feature_case_base.production.tmp.json"
        shutil.copy2(staging_db, production_payload)
        os.replace(production_payload, production_db)
        production_report_payload = {
            **summary,
            "status": "DEPLOYED",
            "deployed_at_utc": _utc_now(),
            "backup_dir": str(backup_dir),
            "backed_up": backed_up,
            "restart_required": True,
        }
        _atomic_json(args.production_report.resolve(), production_report_payload)
        pair_manifest = feature_dir / "feature_link_pair_manifest.json"
        if pair_manifest.is_file():
            manifest_payload = json.loads(pair_manifest.read_text(encoding="utf-8"))
            _atomic_json(args.production_manifest.resolve(), manifest_payload)
        summary.update({
            "status": "DEPLOYED",
            "finished_at_utc": _utc_now(),
            "backup_dir": str(backup_dir),
            "backed_up": backed_up,
            "restart_required": True,
        })
        _atomic_json(summary_path, summary)
        logger.info("DEPLOY_DONE | %s | restart web backend to load the new in-memory case base", production_db)
        return 0
    except BaseException as exc:
        logger.exception("PIPELINE_FAILED | %s", exc)
        summary.update({
            "status": "FAILED",
            "finished_at_utc": _utc_now(),
            "error": f"{type(exc).__name__}: {exc}",
        })
        _atomic_json(summary_path, summary)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
