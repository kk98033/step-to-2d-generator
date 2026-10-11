"""Batch the CURRENT_FINAL 2D tolerance -> 3D feature lookup over database families.

The output is an experimental staging audit.  Candidate links remain searchable
with engineer review, while unresolved 2D tolerances are retained without a
fabricated 3D identity.  No production case database is modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for root in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from auto_2d_drawing.tolerance.assembly_components import (  # noqa: E402
    build_component_pair_manifest,
    extract_leaf_components,
)
from auto_2d_drawing.tolerance.global_geometry_search import GlobalGeometryPairSearcher  # noqa: E402
from auto_2d_drawing.tolerance.historical_manifest import build_pair_manifest  # noqa: E402
from auto_2d_drawing.tolerance.ingest_historical_data import (  # noqa: E402
    ExtractedDimension,
    HistoricalDataIngestor,
)
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2  # noqa: E402
from run_view_instance_projection_experiment import experimental_verifier  # noqa: E402
try:  # package import in tests, direct script import in production runs
    from .timed_process_runner import iter_timed_process_results  # type: ignore
except ImportError:
    from timed_process_runner import iter_timed_process_results  # noqa: E402


BACKGROUND = "#171717"
PANEL = "#262626"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
BLUE = "#60a5fa"
GREEN = "#22c55e"
AMBER = "#facc15"
RED = "#ef4444"
DIMENSION_FIELDS = {field.name for field in fields(ExtractedDimension)}
STATUS_RANK = {"UNRESOLVED": 0, "CANDIDATE_ONLY": 1, "VERIFIED": 2}


def _json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return str(value)


def _font(size: int):
    for path in (Path("C:/Windows/Fonts/msjh.ttc"), Path("C:/Windows/Fonts/arial.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _dimension(payload: dict[str, Any]) -> ExtractedDimension:
    return ExtractedDimension(**{key: value for key, value in payload.items() if key in DIMENSION_FIELDS})


def _key(path: str | Path) -> str:
    return str(Path(path).resolve()).casefold()


def _collect_inputs(config: dict[str, Any], extraction_report: dict[str, Any]):
    source_root = Path(config["source_root"])
    step_paths = sorted({
        path.resolve()
        for family in config.get("families") or []
        for path in (source_root / family).rglob("*")
        if path.is_file() and path.suffix.casefold() in {".stp", ".step"}
    }, key=lambda path: str(path).casefold())
    drawings = {
        _key(row["dxf_path"]): row
        for row in extraction_report.get("drawings") or []
        if row.get("is_canonical_drawing_occurrence") and row.get("dxf_path")
    }
    return step_paths, drawings


def _secondary_regions(dxf_path: Path) -> list[dict[str, Any]]:
    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    return [
        {
            "view_id": candidate.view_id,
            "bbox": list(candidate.bbox),
            "points": [list(point) for point in candidate.points],
            "primitive_count": candidate.primitive_count,
            "source": candidate.source,
        }
        for candidate in extractor.view_candidates
    ]


def _pair_key(pair: dict[str, Any]):
    return (
        _key(pair["step_path"]),
        str(pair.get("component_label_entry") or ""),
        _key(pair["dxf_path"]),
    )


def build_manifests(step_paths, dxf_paths, enable_global_geometry: bool, global_max_components: int | None):
    components = []
    component_errors = []
    per_step_component_counts = {}
    for step_path in step_paths:
        try:
            extracted = extract_leaf_components(str(step_path))
            components.extend(extracted)
            per_step_component_counts[str(step_path)] = len(extracted)
        except Exception as exc:
            component_errors.append({"step_path": str(step_path), "error": f"{type(exc).__name__}: {exc}"})

    direct = build_pair_manifest(map(str, step_paths), map(str, dxf_paths))
    component = build_component_pair_manifest(
        map(str, step_paths), map(str, dxf_paths), pre_extracted_components=components
    )
    pairs = []
    known = set()
    for pair in [*direct.get("verified_pairs", []), *component.get("verified_pairs", [])]:
        key = _pair_key(pair)
        if key not in known:
            pairs.append(pair)
            known.add(key)

    global_manifest = {
        "schema_version": 1,
        "method": GlobalGeometryPairSearcher.METHOD,
        "verified_pairs": [],
        "candidates": [],
        "statistics": {"status": "DISABLED"},
    }
    if enable_global_geometry:
        searcher = GlobalGeometryPairSearcher(retrieval_top_k=12, refine_top_drawings=8)
        global_manifest = searcher.search(
            list(map(str, step_paths)),
            list(map(str, dxf_paths)),
            max_components=global_max_components,
            excluded_pairs=[(key[0], key[1], key[2]) for key in known],
            pre_extracted_components=components,
        )
        for pair in global_manifest.get("verified_pairs", []):
            key = _pair_key(pair)
            if key not in known:
                pairs.append(pair)
                known.add(key)

    return {
        "step_files": [str(path) for path in step_paths],
        "leaf_component_count": len(components),
        "named_leaf_component_count": sum(component.identity is not None for component in components),
        "per_step_component_counts": per_step_component_counts,
        "component_errors": component_errors,
        "direct_manifest": direct,
        "component_manifest": component,
        "global_geometry_manifest": global_manifest,
        "verification_pairs": pairs,
    }


def _candidate_from_diagnostic(diagnostic: dict[str, Any], pair: dict[str, Any]) -> dict[str, Any] | None:
    candidates = list(diagnostic.get("candidates") or [])
    if not candidates:
        return None

    def score(item):
        verification = item.get("geometry_verification") or {}
        localization = verification.get("feature_localization") or {}
        return (
            int(bool(verification.get("passed"))),
            int(bool((verification.get("checks") or {}).get("dimension_endpoints_map_to_brep_topology"))),
            float(localization.get("score", 0.0) or 0.0),
            float(verification.get("score", 0.0) or 0.0),
            -float(item.get("value_difference_mm", 1e9) or 1e9),
        )

    selected = max(candidates, key=score)
    verification = selected.get("geometry_verification") or {}
    return {
        "status": "CANDIDATE_ONLY",
        "feature_id": selected.get("feature_id"),
        "feature_type": selected.get("feature_type"),
        "feature_role": None,
        "nominal_field": selected.get("nominal_field"),
        "confidence": round(float(verification.get("score", 0.0) or 0.0), 6),
        "reason": str(diagnostic.get("status") or "3D nominal candidate requires engineer review"),
        "geometry_verification": verification,
        "pair": {
            "pair_id": pair.get("pair_id"),
            "pair_method": pair.get("pair_method"),
            "step_path": pair.get("step_path"),
            "component_name": pair.get("component_name"),
            "component_label_entry": pair.get("component_label_entry"),
        },
    }


def verify_pair(ingestor, pair: dict[str, Any], drawing: dict[str, Any]) -> dict[str, Any]:
    dxf_path = Path(pair["dxf_path"]).resolve()
    dimensions = [_dimension(item) for item in drawing.get("dimensions") or []]
    primary_evidence = dict(pair)
    primary_evidence["allow_secondary_view_search"] = False
    primary_evidence.setdefault("verification_checks", [])
    primary_evidence["verification_checks"] = list(primary_evidence["verification_checks"]) + [
        "current_final_primary_view_first",
        "current_final_annular_and_composite_feature_graph",
    ]
    primary_cases_with_keys, primary_stats = ingestor._verify_pair(
        pair["step_path"], str(dxf_path), dimensions,
        pair_evidence=primary_evidence, collect_diagnostics=True,
    )
    primary_cases = [case for case, _evidence_keys in primary_cases_with_keys]
    primary_verified_handles = {
        str(case.source_metadata.get("entity_handle") or "").upper()
        for case in primary_cases
    }
    primary_diagnostics = {
        str(item.get("entity_handle") or "").upper(): item
        for item in primary_stats.get("_dimension_diagnostics") or []
    }
    recovery_dimensions = [
        dimension for dimension in dimensions
        if str(dimension.entity_handle or "").upper() not in primary_verified_handles
        and bool((primary_diagnostics.get(str(dimension.entity_handle or "").upper()) or {}).get("candidates"))
    ]
    recovery_cases = []
    recovery_stats: dict[str, Any] = {}
    if recovery_dimensions:
        recovery_evidence = dict(pair)
        recovery_evidence["allow_secondary_view_search"] = True
        recovery_evidence["secondary_dxf_regions"] = _secondary_regions(dxf_path)
        recovery_evidence.setdefault("verification_checks", [])
        recovery_evidence["verification_checks"] = list(recovery_evidence["verification_checks"]) + [
            "current_final_targeted_dimension_local_multi_view_recovery",
            "current_final_annular_and_composite_feature_graph",
        ]
        with experimental_verifier():
            recovered_with_keys, recovery_stats = ingestor._verify_pair(
                pair["step_path"], str(dxf_path), recovery_dimensions,
                pair_evidence=recovery_evidence, collect_diagnostics=True,
            )
        recovery_cases = [case for case, _evidence_keys in recovered_with_keys]
    cases = [*primary_cases, *recovery_cases]
    verified_by_handle = defaultdict(list)
    for case in cases:
        handle = str(case.source_metadata.get("entity_handle") or "").upper()
        verified_by_handle[handle].append(case)
    diagnostics = dict(primary_diagnostics)
    diagnostics.update({
        str(item.get("entity_handle") or "").upper(): item
        for item in recovery_stats.get("_dimension_diagnostics") or []
    })
    assignments = []
    for dimension in dimensions:
        handle = str(dimension.entity_handle or "").upper()
        verified = verified_by_handle.get(handle) or []
        if verified:
            for case in verified:
                geometry_verification = case.source_metadata.get("geometry_verification") or {}
                global_pair_without_topology = bool(
                    pair.get("pair_method") == GlobalGeometryPairSearcher.METHOD
                    and not (geometry_verification.get("checks") or {}).get(
                        "dimension_endpoints_map_to_brep_topology", False
                    )
                )
                assignments.append({
                    "entity_handle": dimension.entity_handle,
                    "status": "CANDIDATE_ONLY" if global_pair_without_topology else "VERIFIED",
                    "feature_id": case.source_metadata.get("matched_feature_id"),
                    "feature_type": case.feature_type,
                    "feature_role": case.inferred_role,
                    "nominal_field": case.source_metadata.get("matched_nominal_field"),
                    "confidence": float(case.confidence),
                    "reason": (
                        "GLOBAL_GEOMETRY_PAIR_WITHOUT_BREP_ENDPOINT_PROOF"
                        if global_pair_without_topology else
                        "Projection, dimension location and BRep topology verification passed."
                    ),
                    "geometry_verification": geometry_verification,
                    "pair": {
                        "pair_id": pair.get("pair_id"),
                        "pair_method": pair.get("pair_method"),
                        "step_path": pair.get("step_path"),
                        "component_name": pair.get("component_name"),
                        "component_label_entry": pair.get("component_label_entry"),
                    },
                })
        else:
            candidate = _candidate_from_diagnostic(diagnostics.get(handle) or {}, pair)
            if candidate:
                assignments.append({"entity_handle": dimension.entity_handle, **candidate})
    return {
        "pair_id": pair.get("pair_id"),
        "pair_method": pair.get("pair_method"),
        "step_path": pair.get("step_path"),
        "dxf_path": str(dxf_path),
        "component_name": pair.get("component_name"),
        "component_label_entry": pair.get("component_label_entry"),
        "dimension_count": len(dimensions),
        "verified_assignment_count": sum(row["status"] == "VERIFIED" for row in assignments),
        "candidate_assignment_count": sum(row["status"] == "CANDIDATE_ONLY" for row in assignments),
        "assignments": assignments,
        "statistics": {
            "primary": {key: value for key, value in primary_stats.items() if not key.startswith("_")},
            "multi_view_recovery": {key: value for key, value in recovery_stats.items() if not key.startswith("_")},
            "multi_view_recovery_dimension_count": len(recovery_dimensions),
        },
        "projection_overlays": {
            **(primary_stats.get("_projection_overlays") or {}),
            **(recovery_stats.get("_projection_overlays") or {}),
        },
    }


def _verify_pair_worker(pair: dict[str, Any], drawing: dict[str, Any]) -> dict[str, Any]:
    """Process-isolated OCC verification for one STEP/DXF pair."""
    return verify_pair(HistoricalDataIngestor(), pair, drawing)


def _aggregate(drawings: dict[str, dict[str, Any]], pair_results: list[dict[str, Any]]):
    assignments = defaultdict(list)
    paired_dxf_keys = {_key(row["dxf_path"]) for row in pair_results}
    for pair_result in pair_results:
        dxf_key = _key(pair_result["dxf_path"])
        for row in pair_result.get("assignments") or []:
            assignments[(dxf_key, str(row.get("entity_handle") or "").upper())].append(row)

    rows = []
    for dxf_key, drawing in drawings.items():
        for dimension in drawing.get("dimensions") or []:
            handle = str(dimension.get("entity_handle") or "").upper()
            options = assignments.get((dxf_key, handle)) or []
            verified = [row for row in options if row["status"] == "VERIFIED"]
            verified_keys = {
                (
                    str((row.get("pair") or {}).get("step_path") or "").casefold(),
                    str((row.get("pair") or {}).get("component_label_entry") or ""),
                    str(row.get("feature_id") or ""),
                    str(row.get("nominal_field") or ""),
                )
                for row in verified
            }
            if len(verified_keys) == 1:
                selected = max(verified, key=lambda row: float(row.get("confidence", 0.0) or 0.0))
            elif len(verified_keys) > 1:
                selected = max(verified, key=lambda row: float(row.get("confidence", 0.0) or 0.0)).copy()
                selected["status"] = "CANDIDATE_ONLY"
                selected["reason"] = "AMBIGUOUS_ACROSS_MULTIPLE_3D_FEATURE_SOURCES"
                selected["alternative_count"] = len(verified_keys)
            elif options:
                selected = max(
                    options,
                    key=lambda row: (STATUS_RANK[row["status"]], float(row.get("confidence", 0.0) or 0.0)),
                )
            else:
                selected = {
                    "status": "UNRESOLVED",
                    "feature_id": None,
                    "feature_type": None,
                    "feature_role": None,
                    "nominal_field": None,
                    "confidence": 0.0,
                    "reason": (
                        "NO_3D_NOMINAL_CANDIDATE_AFTER_VERIFICATION"
                        if dxf_key in paired_dxf_keys else "NO_TRUSTED_STEP_DRAWING_PAIR"
                    ),
                    "pair": None,
                }
            policy = {
                "eligible": selected["status"] in {"VERIFIED", "CANDIDATE_ONLY"},
                "tier": "TRUSTED" if selected["status"] == "VERIFIED" else (
                    "REVIEW_REQUIRED" if selected["status"] == "CANDIDATE_ONLY" else "EXCLUDED_UNRESOLVED"
                ),
                "engineer_review_required": selected["status"] != "VERIFIED",
            }
            rows.append({
                "family": drawing["family"],
                "drawing": drawing["drawing"],
                "dxf_path": drawing["dxf_path"],
                "entity_handle": dimension.get("entity_handle"),
                "nominal_value": dimension.get("nominal_value"),
                "dimension_category": dimension.get("dimension_category"),
                "tolerance_config": dimension.get("tolerance_config"),
                **selected,
                "case_retrieval": policy,
                "alternatives": options,
            })
    return rows


def _render(report: dict[str, Any], output: Path):
    families = report["families"]
    width, row_height = 2800, 130
    height = 200 + row_height * len(families)
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    summary = report["summary"]
    draw.text((30, 24), "CURRENT_FINAL DATABASE FEATURE LINK AUDIT", font=_font(34), fill=WHITE)
    draw.text(
        (30, 72),
        f"tolerances {summary['tolerance_count']} | verified {summary['verified']} | "
        f"candidate {summary['candidate_only']} | unresolved {summary['unresolved']} | "
        f"searchable {summary['searchable']}",
        font=_font(24), fill=BLUE,
    )
    draw.text((30, 116), "Candidate links remain searchable with ENGINEER REVIEW REQUIRED.", font=_font(19), fill=MUTED)
    y = 185
    for index, row in enumerate(families):
        if index % 2 == 0:
            draw.rectangle((18, y, width - 18, y + row_height - 5), fill=PANEL)
        draw.text((32, y + 14), row["family"], font=_font(21), fill=WHITE)
        draw.text((930, y + 14), f"total {row['tolerance_count']}", font=_font(19), fill=WHITE)
        draw.text((1150, y + 14), f"verified {row['verified']}", font=_font(19), fill=GREEN)
        draw.text((1450, y + 14), f"candidate {row['candidate_only']}", font=_font(19), fill=AMBER)
        draw.text((1790, y + 14), f"unresolved {row['unresolved']}", font=_font(19), fill=RED)
        draw.text((32, y + 65), f"searchable {row['searchable']} | trusted pairs {row['paired_drawing_count']}", font=_font(17), fill=MUTED)
        y += row_height
    image.save(output)


def run(
    config_path: Path,
    extraction_report_path: Path,
    output_dir: Path,
    enable_global_geometry: bool,
    global_max_components: int | None,
    workers: int,
    pair_timeout_seconds: float = 900.0,
):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    extraction_report = json.loads(extraction_report_path.read_text(encoding="utf-8"))
    step_paths, drawings = _collect_inputs(config, extraction_report)
    manifests = build_manifests(
        step_paths, [Path(row["dxf_path"]) for row in drawings.values()],
        enable_global_geometry, global_max_components,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "feature_link_pair_manifest.json").write_text(
        json.dumps(manifests, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8"
    )

    pair_results = []
    pair_errors = []
    pairs = manifests["verification_pairs"]
    jobs = []
    for pair in pairs:
        drawing = drawings.get(_key(pair["dxf_path"]))
        if drawing is None or not drawing.get("dimensions"):
            continue
        jobs.append((pair, drawing))
    checkpoint_path = output_dir / "pair_results_checkpoint.json"
    if checkpoint_path.is_file():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        pair_results = list(checkpoint.get("pair_results") or [])
        pair_errors = list(checkpoint.get("pair_errors") or [])
    completed_keys = {
        (_key(row["step_path"]), str(row.get("component_label_entry") or ""), _key(row["dxf_path"]))
        for row in pair_results
    }
    completed_keys.update({
        (_key(row["step_path"]), str(row.get("component_label_entry") or ""), _key(row["dxf_path"]))
        for row in pair_errors
        if row.get("step_path") and row.get("dxf_path")
    })
    total_job_count = len(jobs)
    jobs = [(pair, drawing) for pair, drawing in jobs if _pair_key(pair) not in completed_keys]
    pair_by_task = {
        f"pair-{index:06d}": (pair, drawing)
        for index, (pair, drawing) in enumerate(jobs, start=1)
    }
    tasks = [
        (task_id, (pair, drawing))
        for task_id, (pair, drawing) in pair_by_task.items()
    ]
    completed = len(completed_keys)
    for timed in iter_timed_process_results(
        _verify_pair_worker,
        tasks,
        timeout_seconds=pair_timeout_seconds,
        workers=workers,
    ):
        pair, _drawing = pair_by_task[timed.task_id]
        completed += 1
        if timed.status == "COMPLETED":
            result = timed.value
            result["elapsed_seconds"] = timed.elapsed_seconds
            pair_results.append(result)
        else:
            pair_errors.append({
                "pair_id": pair.get("pair_id"),
                "step_path": pair.get("step_path"),
                "dxf_path": pair.get("dxf_path"),
                "status": "PAIR_TIMEOUT" if timed.status == "TIMEOUT" else "PAIR_FAILED",
                "elapsed_seconds": timed.elapsed_seconds,
                "error": timed.error,
            })
        checkpoint_path.write_text(json.dumps({
            "completed": completed,
            "total": total_job_count,
            "pair_results": pair_results,
            "pair_errors": pair_errors,
        }, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
        print(
            f"Feature-linked pair {completed}/{total_job_count}: {pair.get('pair_id')} "
            f"[{timed.status}] {timed.elapsed_seconds:.2f}s",
            flush=True,
        )

    tolerances = _aggregate(drawings, pair_results)
    family_names = list(config.get("families") or [])
    families = []
    paired_drawings = {_key(row["dxf_path"]) for row in pair_results}
    for family in family_names:
        rows = [row for row in tolerances if row["family"] == family]
        families.append({
            "family": family,
            "tolerance_count": len(rows),
            "verified": sum(row["status"] == "VERIFIED" for row in rows),
            "candidate_only": sum(row["status"] == "CANDIDATE_ONLY" for row in rows),
            "unresolved": sum(row["status"] == "UNRESOLVED" for row in rows),
            "searchable": sum(row["case_retrieval"]["eligible"] for row in rows),
            "paired_drawing_count": len({_key(row["dxf_path"]) for row in rows} & paired_drawings),
        })
    summary = {
        "step_file_count": len(step_paths),
        "leaf_component_count": manifests["leaf_component_count"],
        "canonical_drawing_count": len(drawings),
        "verification_pair_count": len(pairs),
        "processed_pair_count": len(pair_results),
        "pair_error_count": len(pair_errors),
        "tolerance_count": len(tolerances),
        "verified": sum(row["status"] == "VERIFIED" for row in tolerances),
        "candidate_only": sum(row["status"] == "CANDIDATE_ONLY" for row in tolerances),
        "unresolved": sum(row["status"] == "UNRESOLVED" for row in tolerances),
        "searchable": sum(row["case_retrieval"]["eligible"] for row in tolerances),
    }
    report = {
        "schema_version": 1,
        "method": "CURRENT_FINAL_MULTI_VIEW_COMPOSITE_DATABASE_FEATURE_LINK_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_or_web_updated": False,
        "summary": summary,
        "families": families,
        "pair_errors": pair_errors,
        "pair_results": pair_results,
        "tolerances": tolerances,
    }
    report_path = output_dir / "database_feature_link_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8"
    )
    _render(report, output_dir / "database_feature_link_overview.png")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--extraction-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--skip-global-geometry", action="store_true")
    parser.add_argument("--global-max-components", type=int, default=None)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--pair-timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()
    report = run(
        args.config.resolve(), args.extraction_report.resolve(), args.output_dir.resolve(),
        not args.skip_global_geometry, args.global_max_components, args.workers,
        args.pair_timeout_seconds,
    )
    return 1 if report["summary"]["pair_error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
