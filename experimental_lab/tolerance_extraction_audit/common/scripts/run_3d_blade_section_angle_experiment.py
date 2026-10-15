"""Run experiment 16: 3D B-Rep blade-section angle semantics."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.tolerance.assembly_components import load_component_shape  # noqa: E402
from adaptive_curved_radial_features import detect_adaptive_curved_radial_partitions  # noqa: E402
from blade_profile_semantics import extract_blade_profile_semantics  # noqa: E402
from blade_section_semantics import section_shape_view  # noqa: E402
from run_classification_tree_audit import (  # noqa: E402
    BOX, CYAN, GREEN, GRID, MUTED, ORANGE, PANEL, RED, WHITE, YELLOW,
    _edge_points, _font, _mapper,
)


METHOD = "BLADE_3D_SECTION_ANGLE_SEMANTIC_V1"
BACKGROUND = "#0b0b0b"
MAGENTA = "#f472b6"
FIELDS = (
    "inner_relative_to_radial", "inner_relative_to_tangent",
    "outer_relative_to_radial", "outer_relative_to_tangent",
)


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in profile.items() if key != "measurements"}


def _scan_sections(shape, fractions: Sequence[float]) -> list[dict[str, Any]]:
    results = []
    for fraction in fractions:
        try:
            view = section_shape_view(shape, fraction)
            detection = detect_adaptive_curved_radial_partitions(view)
            profile = extract_blade_profile_semantics(view, detection) if detection.get("resolved") else {
                "resolved": False,
                "reason": detection.get("reason") or "SECTION_PATTERN_UNRESOLVED",
            }
            results.append({
                "fraction": fraction,
                "resolved": bool(profile.get("resolved")),
                "view": view,
                "detection": detection,
                "profile": profile,
            })
        except Exception as exc:
            results.append({"fraction": fraction, "resolved": False, "error": str(exc)})
    return results


def _select_station_roles(scans: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    resolved = [item for item in scans if item.get("resolved")]
    if len(resolved) < 3:
        return {}
    by_count: dict[int, list[dict[str, Any]]] = {}
    for item in resolved:
        count = int(item["profile"].get("pattern_count") or 0)
        by_count.setdefault(count, []).append(item)
    # A slice grazing a shroud/hub can expose only every other boundary. Keep
    # the count supported by the largest contiguous station population rather
    # than using a historical blade-count label.
    stable = max(by_count.values(), key=lambda items: (len(items), int(items[0]["profile"].get("pattern_count") or 0)))
    if len(stable) < 3:
        return {}
    indexes = (0, len(stable) // 2, len(stable) - 1)
    return {role: stable[index] for role, index in zip(("lower", "mid", "upper"), indexes)}


def _absolute_angle(value: float) -> float:
    value = abs(float(value)) % 180.0
    return min(value, 180.0 - value)


def _transform(value: float, name: str) -> float:
    base = _absolute_angle(value)
    return {
        "ABS": base,
        "COMPLEMENT_90": abs(90.0 - base),
        "PLUS_90": 90.0 + base,
        "SUPPLEMENT_180": 180.0 - base,
    }[name]


def _candidate_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for role, station in case["station_roles"].items():
        family_summaries = station["profile"].get("family_summaries") or {}
        for family in ("left", "right"):
            summaries = family_summaries.get(family) or {}
            for field in FIELDS:
                summary = summaries.get(field) or {}
                if "median" not in summary:
                    continue
                for transform_name in ("ABS", "COMPLEMENT_90", "PLUS_90", "SUPPLEMENT_180"):
                    rows.append({
                        "signature": f"{role}.{family}.{field}.{transform_name}",
                        "station_role": role,
                        "station_fraction": station["fraction"],
                        "boundary_family": family,
                        "field": field,
                        "transform": transform_name,
                        "raw_value": float(summary["median"]),
                        "predicted_angle": _transform(float(summary["median"]), transform_name),
                    })
    return rows


def _leave_one_out(cases: Sequence[dict[str, Any]], target: str) -> dict[str, Any]:
    folds = []
    for holdout in cases:
        training = [case for case in cases if case is not holdout]
        maps = {case["name"]: {row["signature"]: row for row in case["angle_candidates"]} for case in cases}
        common = set(maps[holdout["name"]])
        for case in training:
            common &= set(maps[case["name"]])
        ranked = []
        for signature in common:
            errors = [
                abs(maps[case["name"]][signature]["predicted_angle"] - float(case["historical"][target]))
                for case in training
            ]
            ranked.append((sum(errors) / len(errors), max(errors), signature))
        if not ranked:
            continue
        train_mae, train_max, signature = min(ranked)
        row = maps[holdout["name"]][signature]
        actual = float(holdout["historical"][target])
        predicted = float(row["predicted_angle"])
        folds.append({
            "holdout": holdout["name"],
            "selected_signature": signature,
            "train_mae": round(train_mae, 6),
            "train_max_error": round(train_max, 6),
            "prediction": round(predicted, 6),
            "actual": actual,
            "absolute_error": round(abs(predicted - actual), 6),
            "station_fraction": row["station_fraction"],
        })
    errors = [item["absolute_error"] for item in folds]
    return {
        "target": target,
        "fold_count": len(folds),
        "holdout_mae": round(sum(errors) / len(errors), 6) if errors else None,
        "holdout_max_error": round(max(errors), 6) if errors else None,
        "folds": folds,
    }


def _canonical_semantic_metrics(cases: Sequence[dict[str, Any]], target: str) -> dict[str, Any]:
    """Evaluate one physically fixed convention without label-driven selection."""
    field = "inner_relative_to_tangent" if target == "inlet_angle_degrees" else "outer_relative_to_radial"
    transform_name = "ABS" if target == "inlet_angle_degrees" else "PLUS_90"
    signature = f"mid.left.{field}.{transform_name}"
    samples = []
    for case in cases:
        row = next(item for item in case["angle_candidates"] if item["signature"] == signature)
        actual = float(case["historical"][target])
        predicted = float(row["predicted_angle"])
        samples.append({
            "case": case["name"],
            "prediction": round(predicted, 6),
            "actual": actual,
            "absolute_error": round(abs(predicted - actual), 6),
            "station_fraction": row["station_fraction"],
        })
    errors = [item["absolute_error"] for item in samples]
    return {
        "target": target,
        "signature": signature,
        "semantic": (
            "acute angle between inner blade tangent and local circumferential tangent"
            if target == "inlet_angle_degrees"
            else "oriented outer blade angle: 90 degrees plus acute radial offset"
        ),
        "mae": round(sum(errors) / len(errors), 6),
        "maximum_error": round(max(errors), 6),
        "samples": samples,
    }


def _expanded_bbox(bbox: Sequence[float], fraction: float = 0.10) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = [float(value) for value in bbox]
    pad = max(x1 - x0, y1 - y0) * fraction
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad


def _draw_section(canvas: Image.Image, station: dict[str, Any], box: Sequence[int], role: str) -> None:
    draw = ImageDraw.Draw(canvas)
    view, profile = station["view"], station["profile"]
    draw.rectangle(tuple(box), fill=BACKGROUND, outline=GRID, width=2)
    mapper = _mapper(_expanded_bbox(view["bbox"]), (box[0] + 25, box[1] + 75, box[2] - 25, box[3] - 25))
    for edge in view.get("visible") or []:
        points = _edge_points(edge)
        if len(points) >= 2:
            draw.line([mapper(point) for point in points], fill="#a3a3a3", width=2)
    representative = next(iter(profile.get("measurements") or []), None)
    if representative:
        for reference in representative.get("chain_edge_refs") or [representative["edge_ref"]]:
            collection_name, index_text = reference.split(":", 1)
            edges = view.get(collection_name) or []
            index = int(index_text)
            if 0 <= index < len(edges):
                points = _edge_points(edges[index])
                if len(points) >= 2:
                    draw.line([mapper(point) for point in points], fill=CYAN, width=7)
        center = profile["center"]
        for point, sample, colour in (
            (representative["inner_point"], representative["inner_sample_point"], GREEN),
            (representative["outer_point"], representative["outer_sample_point"], ORANGE),
        ):
            draw.line((mapper(center), mapper(point)), fill=YELLOW, width=3)
            draw.line((mapper(point), mapper(sample)), fill=colour, width=8)
    inner = ((profile.get("family_summaries") or {}).get("left") or {}).get("inner_relative_to_radial") or {}
    outer = ((profile.get("family_summaries") or {}).get("left") or {}).get("outer_relative_to_tangent") or {}
    draw.text((box[0] + 18, box[1] + 15), f"{role.upper()} section @ {station['fraction']:.2f}", font=_font(19, True), fill=CYAN)
    draw.text((box[0] + 18, box[1] + 43), f"inner radial={abs(float(inner.get('median') or 0.0)):.2f} deg | outer tangent={abs(float(outer.get('median') or 0.0)):.2f} deg", font=_font(15), fill=WHITE)


def _fit(canvas: Image.Image, path: Path, box: Sequence[int]) -> None:
    with Image.open(path) as source:
        image = source.convert("RGB")
    width, height = int(box[2] - box[0]), int(box[3] - box[1])
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas.paste(image, (int(box[0] + (width - image.width) / 2), int(box[1] + (height - image.height) / 2)))


def _render_case(case: dict[str, Any], drawing_path: Path, output: Path) -> None:
    canvas = Image.new("RGB", (4500, 2250), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((45, 25), f"Experiment 16 | {case['name']} | Dynamic 3D blade sections", font=_font(38, True), fill=WHITE)
    draw.text((45, 82), f"axis={case['axis']['axis_index']} confidence={case['axis']['confidence']:.3f} | historical inlet={case['historical']['inlet_angle_degrees']:.1f} deg outlet={case['historical']['outlet_angle_degrees']:.1f} deg", font=_font(19), fill=GREEN)
    station_boxes = ((40, 145, 1460, 1080), (1510, 145, 2930, 1080), (2980, 145, 4400, 1080))
    for role, box in zip(("lower", "mid", "upper"), station_boxes):
        _draw_section(canvas, case["station_roles"][role], box, role)
    draw.rectangle((40, 1130, 4400, 2180), fill=BACKGROUND, outline=GRID, width=2)
    draw.text((65, 1150), "ORIGINAL ENGINEER DRAWING", font=_font(20, True), fill=CYAN)
    _fit(canvas, drawing_path, (65, 1200, 4375, 2155))
    canvas.save(output)


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source_config = json.loads(_resolve(config["experiment_15_config"]).read_text(encoding="utf-8"))
    source_report = json.loads(_resolve(config["experiment_15_report"]).read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    accepted = [case for case in source_report["cases"] if case["eligible_for_calibration"]]
    config_by_name = {case["name"]: case for case in source_config["cases"]}
    reports = []
    fractions = [float(value) for value in config.get("scan_fractions") or [index / 10 for index in range(1, 10)]]
    for index, source_case in enumerate(accepted, start=2):
        started = time.perf_counter()
        case_config = config_by_name[source_case["name"]]
        shape = load_component_shape(case_config["step_path"], source_case["selected_component"]["label_entry"])
        if shape is None:
            raise RuntimeError(f"Cannot reload selected component for {source_case['name']}")
        scans = _scan_sections(shape, fractions)
        roles = _select_station_roles(scans)
        if len(roles) != 3:
            raise RuntimeError(f"Fewer than three resolved 3D sections for {source_case['name']}")
        first = next(iter(roles.values()))
        case = {
            "name": source_case["name"],
            "historical": source_case["historical"],
            "component_label": source_case["selected_component"]["label_entry"],
            "component_name": source_case["selected_component"]["name"],
            "axis": first["view"]["axis"],
            "scan_results": [{
                "fraction": item["fraction"],
                "resolved": item.get("resolved", False),
                "error": item.get("error"),
                "pattern_count": int((item.get("profile") or {}).get("pattern_count") or 0),
            } for item in scans],
            "station_roles": roles,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        case["angle_candidates"] = _candidate_rows(case)
        case_dir = output_dir / f"{index:02d}_{source_case['name']}_review"
        case_dir.mkdir(parents=True, exist_ok=True)
        drawing_path = _resolve(config["experiment_15_directory"]) / f"{index + 1:02d}_{source_case['name']}_review" / "02_original_engineer_drawing.png"
        if not drawing_path.exists():
            # Experiment 15 numbering starts with the rejected FLBN control.
            matches = list(_resolve(config["experiment_15_directory"]).glob(f"*_{source_case['name']}_review/02_original_engineer_drawing.png"))
            if not matches:
                raise FileNotFoundError(f"Original drawing image not found for {source_case['name']}")
            drawing_path = matches[0]
        _render_case(case, drawing_path, case_dir / "01_3d_section_angle_review.png")
        public_case = {
            **case,
            "station_roles": {
                role: {
                    "fraction": station["fraction"],
                    "section_edge_count": station["view"]["section_edge_count"],
                    "profile": _public_profile(station["profile"]),
                }
                for role, station in roles.items()
            },
        }
        (case_dir / "section_angle_report.json").write_text(json.dumps(public_case, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(public_case)
    inlet = _leave_one_out(reports, "inlet_angle_degrees")
    outlet = _leave_one_out(reports, "outlet_angle_degrees")
    canonical_inlet = _canonical_semantic_metrics(reports, "inlet_angle_degrees")
    canonical_outlet = _canonical_semantic_metrics(reports, "outlet_angle_degrees")
    summary = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "model_names_used_by_section_detector": False,
        "metrics": {
            "case_count": len(reports),
            "three_station_resolved_count": len(reports),
            "inlet_holdout_mae": inlet["holdout_mae"],
            "inlet_holdout_max_error": inlet["holdout_max_error"],
            "outlet_holdout_mae": outlet["holdout_mae"],
            "outlet_holdout_max_error": outlet["holdout_max_error"],
            "canonical_inlet_mae": canonical_inlet["mae"],
            "canonical_inlet_max_error": canonical_inlet["maximum_error"],
            "canonical_outlet_mae": canonical_outlet["mae"],
            "canonical_outlet_max_error": canonical_outlet["maximum_error"],
        },
        "inlet_leave_one_out": inlet,
        "outlet_leave_one_out": outlet,
        "canonical_inlet_semantic": canonical_inlet,
        "canonical_outlet_semantic": canonical_outlet,
        "cases": reports,
    }
    (output_dir / "01_experiment_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.config, args.output_dir)
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
