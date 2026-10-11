"""Merge generic and specialized evidence for every explicit Blade tolerance.

This remains an experimental audit artifact.  It does not write the production
case database or update the web application.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BACKGROUND = "#171717"
PANEL = "#262626"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
GREEN = "#22c55e"
AMBER = "#facc15"
RED = "#ef4444"
BLUE = "#60a5fa"


def _apply_retrieval_policy(row: dict) -> None:
    """Keep strict verification while allowing reviewable candidates in retrieval."""
    status = str(row.get("status") or "UNRESOLVED").upper()
    if status == "VERIFIED":
        policy = {
            "eligible": True,
            "tier": "TRUSTED",
            "confidence_label": "HIGH",
            "engineer_review_required": False,
        }
    elif status == "CANDIDATE_ONLY":
        policy = {
            "eligible": True,
            "tier": "REVIEW_REQUIRED",
            "confidence_label": "MEDIUM" if row.get("semantic_path") else "LOW",
            "engineer_review_required": True,
        }
    else:
        policy = {
            "eligible": False,
            "tier": "EXCLUDED_UNRESOLVED",
            "confidence_label": "INSUFFICIENT",
            "engineer_review_required": True,
        }
    row["case_retrieval"] = policy
    row["recommendation_eligible"] = policy["eligible"]
    row["recommendation_label"] = policy["tier"]
    row["engineer_review_required"] = policy["engineer_review_required"]


def _font(size: int):
    for path in (Path("C:/Windows/Fonts/msjh.ttc"), Path("C:/Windows/Fonts/arial.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tolerance_label(row: dict) -> str:
    cfg = row.get("tolerance_config") or {}
    nominal = float(row.get("nominal_value") or row.get("nominal_mm") or 0.0)
    upper = float(cfg.get("upper_dev") or 0.0)
    lower = float(cfg.get("lower_dev") or 0.0)
    prefix = "DIA " if str(row.get("dimension_category", "")).upper() == "DIAMETER" else ""
    return f"{prefix}{nominal:g}  +{upper:g}/{lower:g} mm"


def merge(
    baseline_path: Path,
    radial_path: Path,
    projection_path: Path,
    current_path: Path,
    assembly_path=None,
    rotational_envelope_path=None,
) -> dict:
    baseline = _read(baseline_path)
    radial = _read(radial_path)
    projection = _read(projection_path)
    current = _read(current_path)
    assembly = _read(assembly_path) if assembly_path and assembly_path.is_file() else {}
    rotational_envelope = (
        _read(rotational_envelope_path)
        if rotational_envelope_path and rotational_envelope_path.is_file()
        else {}
    )
    radial_by_handle = {
        str(item["entity_handle"]).upper(): item
        for item in radial.get("dimensions") or []
    }
    projection_by_handle = {
        str(item["entity_handle"]).upper(): item
        for item in projection.get("dimension_bindings") or []
    }
    current_by_handle = {
        str(item.get("dimension_handle", "")).upper(): item
        for item in current.get("tolerances") or []
        if item.get("dimension_handle")
    }
    assembly_by_handle = {
        str(item.get("entity_handle", "")).upper(): item
        for item in assembly.get("dimensions") or []
    }

    rows = []
    for source in baseline.get("dimensions") or []:
        row = dict(source)
        handle = str(row.get("entity_handle", "")).upper()
        row["baseline_status"] = row.get("status")
        row["verification_methods"] = ["GENERAL_FEATURE_TOPOLOGY_VERIFIER"]
        radial_row = radial_by_handle.get(handle)
        projection_row = projection_by_handle.get(handle)
        if radial_row and projection_row:
            semantic = radial_row.get("radial_semantic") or {}
            unique_semantic = str(semantic.get("status", "")).startswith("UNIQUE_")
            projection_passed = bool(
                projection_row.get("topology_passed")
                and float(projection_row.get("geometry_score") or 0.0) >= 0.95
                and float(projection_row.get("localization_score") or 0.0) >= 0.95
            )
            if unique_semantic and projection_passed:
                row["status"] = "VERIFIED"
                row["feature_id"] = projection_row.get("feature_id")
                row["nominal_field"] = semantic.get("semantic_path")
                row["semantic_path"] = semantic.get("semantic_path")
                row["reason"] = (
                    "Drawing-local projection, BRep topology, and radial material-side "
                    "semantics agree."
                )
                row["verification_methods"] = [
                    "MULTI_PROJECTION_INSTANCE_INDEX_V1",
                    "BREP_RADIAL_MATERIAL_SIDE_GRAPH_V1",
                ]
                row["projection_evidence"] = {
                    key: projection_row.get(key)
                    for key in (
                        "instance_id", "drawing_view_instance_id", "geometry_score",
                        "localization_score", "topology_passed",
                    )
                }
                row["radial_semantic"] = semantic

        current_row = current_by_handle.get(handle)
        if current_row and current_row.get("status") == "VERIFIED":
            row["status"] = "VERIFIED"
            row["feature_id"] = current_row.get("semantic_feature")
            row["nominal_field"] = "projected_envelope_diameter"
            row["semantic_path"] = "repeated_radial_pattern.outer_projected_envelope_diameter"
            row["reason"] = "Central section, drawing-local phase, and STEP metric gates passed."
            row["verification_methods"] = [
                "CENTRAL_SECTION_CURVES",
                "DRAWING_LOCAL_GEOMETRY_CONSTRAINED_PHASE_V1",
            ]
            row["specialized_evidence"] = current_row

        row["pre_assembly_status"] = row.get("status")
        assembly_row = assembly_by_handle.get(handle)
        if assembly_row and row.get("status") != "VERIFIED":
            assembly_status = assembly_row.get("status")
            if assembly_status == "VERIFIED" or (
                assembly_status == "CANDIDATE_ONLY" and row.get("status") == "UNRESOLVED"
            ):
                row["status"] = assembly_status
                row["feature_id"] = assembly_row.get("feature_id")
                row["nominal_field"] = assembly_row.get("semantic_path")
                row["semantic_path"] = assembly_row.get("semantic_path")
                row["feature_class"] = assembly_row.get("feature_class")
                row["reason"] = assembly_row.get("reason")
                row["verification_methods"] = assembly_row.get("verification_methods") or []
                row["assembly_evidence"] = assembly_row.get("assembly_evidence")
                row["effective_dimension_category"] = assembly_row.get("effective_dimension_category")
                row["assembly_metric_error_mm"] = assembly_row.get("metric_error_mm")

        if (
            rotational_envelope
            and handle == str(rotational_envelope.get("dimension_handle", "")).upper()
            and row.get("status") != "VERIFIED"
            and rotational_envelope.get("status") == "CANDIDATE_ONLY"
        ):
            row["status"] = "CANDIDATE_ONLY"
            row["feature_id"] = "rotational_section_outer_envelope"
            row["nominal_field"] = "sectional_outer_envelope_diameter"
            row["semantic_path"] = "repeated_radial_pattern.sectional_outer_envelope_diameter"
            row["feature_class"] = "derived_rotational_envelope_feature"
            row["reason"] = (
                "DXF extension lines trace to two section boundaries; a near-tied "
                "geometry phase enters the native tolerance, but the derived contour "
                "still requires engineer confirmation."
            )
            row["verification_methods"] = [
                "DXF_EXTENSION_LINE_TO_TARGET_EDGE_TRACE_V1",
                "ROTATED_CENTRAL_SECTION_PHASE_SCAN_V1",
                "LOCAL_GEOMETRY_TIE_WITH_DIMENSION_TIEBREAKER_V1",
            ]
            row["rotational_envelope_evidence"] = rotational_envelope

        _apply_retrieval_policy(row)
        rows.append(row)

    explicit_count = len(rows)
    summary = {
        "explicit_tolerance_count": explicit_count,
        "tolerance_value_extracted": explicit_count,
        "verified_3d_feature_links": sum(item.get("status") == "VERIFIED" for item in rows),
        "candidate_only": sum(item.get("status") == "CANDIDATE_ONLY" for item in rows),
        "unresolved_3d_feature": sum(item.get("status") == "UNRESOLVED" for item in rows),
        "searchable_cases": sum(item.get("recommendation_eligible") for item in rows),
        "trusted_searchable_cases": sum(
            (item.get("case_retrieval") or {}).get("tier") == "TRUSTED" for item in rows
        ),
        "review_required_searchable_cases": sum(
            (item.get("case_retrieval") or {}).get("tier") == "REVIEW_REQUIRED" for item in rows
        ),
    }
    summary["tolerance_extraction_rate"] = round(
        summary["tolerance_value_extracted"] / max(explicit_count, 1), 6
    )
    summary["verified_feature_link_rate"] = round(
        summary["verified_3d_feature_links"] / max(explicit_count, 1), 6
    )
    summary["recommendation_coverage"] = round(
        summary["searchable_cases"] / max(explicit_count, 1), 6
    )
    verified_before_assembly = sum(
        item.get("pre_assembly_status") == "VERIFIED" for item in rows
    )
    summary["assembly_improvement"] = {
        "verified_before": verified_before_assembly,
        "verified_after": summary["verified_3d_feature_links"],
        "new_verified": summary["verified_3d_feature_links"] - verified_before_assembly,
        "link_rate_before": round(verified_before_assembly / max(explicit_count, 1), 6),
        "link_rate_after": summary["verified_feature_link_rate"],
    }

    radius = current_by_handle.get("FC4D")
    supplemental = []
    if radius:
        supplemental.append({
            "entity_handle": "FC4D",
            "annotation": "7-R2.15",
            "nominal_value": radius.get("nominal_radius_mm"),
            "explicit_upper_lower_tolerance": False,
            "status": radius.get("status"),
            "semantic_path": "repeated_radial_pattern.blade_profile_bend_radius",
            "reason": radius.get("reason"),
            "best_candidate": radius.get("best_candidate"),
            "classification": "NOMINAL_ONLY_OR_TITLE_BLOCK_GENERAL_TOLERANCE",
        })

    return {
        "schema_version": 1,
        "method": "ALL_EXPLICIT_TOLERANCES_MULTI_METHOD_AUDIT_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_or_web_updated": False,
        "component": baseline.get("component_name"),
        "drawing": baseline.get("drawing"),
        "summary": summary,
        "explicit_tolerances": rows,
        "supplemental_nominal_annotations": supplemental,
        "evidence_sources": {
            "generic_baseline": str(baseline_path),
            "radial_material_graph": str(radial_path),
            "multi_projection_instances": str(projection_path),
            "current_final": str(current_path),
            "assembly_feature_resolver": str(assembly_path) if assembly_path else None,
            "rotational_envelope_phase_scan": str(rotational_envelope_path) if rotational_envelope_path else None,
        },
    }


def render(report: dict, output: Path) -> None:
    rows = report["explicit_tolerances"]
    row_height = 76
    width = 2200
    height = 260 + row_height * len(rows) + 150
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    summary = report["summary"]
    draw.text((42, 28), "AL0W BLADE | ALL EXPLICIT TOLERANCES", font=_font(40), fill=WHITE)
    draw.text(
        (42, 84),
        (
            f"extracted {summary['tolerance_value_extracted']}/{summary['explicit_tolerance_count']} | "
            f"3D verified {summary['verified_3d_feature_links']} | "
            f"candidate {summary['candidate_only']} | unresolved {summary['unresolved_3d_feature']}"
            f" | searchable {summary['searchable_cases']}"
        ),
        font=_font(25),
        fill=BLUE,
    )
    draw.text(
        (42, 125),
        "All values were parsed. VERIFIED means the tolerance is also linked to defensible 3D geometry.",
        font=_font(21), fill=MUTED,
    )
    columns = [(42, "HANDLE"), (210, "TOLERANCE"), (630, "STATUS"), (880, "3D SEMANTIC / REASON")]
    for x, label in columns:
        draw.text((x, 190), label, font=_font(21), fill=WHITE)
    y = 230
    for index, row in enumerate(rows):
        if index % 2 == 0:
            draw.rectangle((25, y - 4, width - 25, y + row_height - 6), fill=PANEL)
        status = row.get("status", "UNRESOLVED")
        color = GREEN if status == "VERIFIED" else AMBER if status == "CANDIDATE_ONLY" else RED
        draw.text((42, y + 12), str(row.get("entity_handle")), font=_font(23), fill=WHITE)
        draw.text((210, y + 12), _tolerance_label(row), font=_font(23), fill=WHITE)
        draw.ellipse((630, y + 17, 648, y + 35), fill=color)
        draw.text((660, y + 10), status, font=_font(22), fill=color)
        semantic = row.get("semantic_path") or (
            f"{row.get('feature_id')}.{row.get('nominal_field')}" if row.get("feature_id") else row.get("reason")
        )
        draw.text((880, y + 8), str(semantic)[:100], font=_font(21), fill=WHITE if status == "VERIFIED" else MUTED)
        if status == "VERIFIED":
            methods = " + ".join(row.get("verification_methods") or [])
            draw.text((880, y + 38), methods[:120], font=_font(16), fill=MUTED)
        elif status == "CANDIDATE_ONLY":
            draw.text(
                (880, y + 38),
                "SEARCHABLE CANDIDATE | ENGINEER REVIEW REQUIRED",
                font=_font(16), fill=AMBER,
            )
        y += row_height
    supplemental = report.get("supplemental_nominal_annotations") or []
    if supplemental:
        draw.text((42, y + 22), "SUPPLEMENTAL NOMINAL ANNOTATION", font=_font(22), fill=WHITE)
        item = supplemental[0]
        draw.text(
            (42, y + 60),
            f"{item['annotation']} | {item['status']} | no explicit +/- limits; kept outside explicit-tolerance accuracy",
            font=_font(21), fill=AMBER,
        )
    image.save(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--radial", required=True, type=Path)
    parser.add_argument("--projection", required=True, type=Path)
    parser.add_argument("--current-final", required=True, type=Path)
    parser.add_argument("--assembly", type=Path)
    parser.add_argument("--rotational-envelope", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    args = parser.parse_args()
    report = merge(
        args.baseline.resolve(), args.radial.resolve(), args.projection.resolve(),
        args.current_final.resolve(), args.assembly.resolve() if args.assembly else None,
        args.rotational_envelope.resolve() if args.rotational_envelope else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    render(report, args.image.resolve())
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
