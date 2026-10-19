"""Run repeatable ordinary/maximum drawing tests with engineer-DXF comparisons."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import ezdxf
from PIL import Image, ImageChops, ImageDraw, ImageFont
from ezdxf import bbox


AUDIT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = AUDIT_ROOT.parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.smart_annotation_engine import SmartAnnotationEngine  # noqa: E402
from auto_2d_drawing.pdf_exporter import export_png  # noqa: E402
from auto_2d_drawing.step_reader import load_step  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2  # noqa: E402
from historical_view_comparison import expanded_crop_bounds  # noqa: E402
from minimal_multiview_drawing import render_minimal_multiview_drawing  # noqa: E402
from run_historical_drawing_comparison import _match_views, _render_native_region  # noqa: E402
from run_selected_feature_render_experiment import validate_selection  # noqa: E402


SUPPORTED_TYPES = {"LINEAR", "DIAMETER", "LEADER", "CENTERLINES"}
EXCLUDED_GENERATED_LAYERS = {"BORDER", "TITLE_LABEL", "TITLE_VALUE"}
BACKGROUND = "#111111"
PANEL = "#171717"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
BLUE = "#2563eb"


def _resolve_path(value: str, config_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    project_path = PROJECT_ROOT / path
    return project_path if project_path.exists() else config_dir / path


def build_maximum_selections(step_path: Path, part_hint: str) -> list[dict[str, str]]:
    shape = load_step(str(step_path))
    views = ViewProjector().project_all_views(shape, view_names=["front", "top", "right"])
    rules = SmartAnnotationEngine().get_candidate_rules(shape, views, part_hint=part_hint)
    selections: list[dict[str, str]] = []
    for rule in rules:
        if str(rule.get("dim_type") or "").upper() not in SUPPORTED_TYPES:
            continue
        preferred = str(rule.get("preferred_view") or "")
        candidates = list(rule.get("target_views") or rule.get("views") or [])
        ordered_views = [preferred, *candidates, "front", "top", "right"]
        for view_name in dict.fromkeys(item for item in ordered_views if item in views):
            if validate_selection(rule, view_name, views[view_name])["status"] == "FAIL":
                continue
            selections.append({
                "feature_id": str(rule.get("rule_id") or rule.get("id")),
                "view": view_name,
                "side": str(rule.get("side") or "BOTTOM"),
                "tolerance": str(rule.get("default_tolerance") or ""),
            })
    return selections


def _selection_family(selection: Mapping[str, Any]) -> str:
    feature_id = str(selection.get("feature_id") or "")
    match = re.match(r"([a-z]+(?:_[a-z]+)?)", feature_id.lower())
    return match.group(1) if match else feature_id


def select_representative_subset(
    selections: Sequence[Mapping[str, Any]],
    limit: int,
    *,
    family_cap: int,
) -> list[dict[str, Any]]:
    if limit <= 0 or len(selections) <= limit:
        return [dict(item) for item in selections]
    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str]] = set()
    family_counts: dict[str, int] = {}

    def append(item: Mapping[str, Any], enforce_family_cap: bool) -> bool:
        key = (str(item.get("feature_id") or ""), str(item.get("view") or ""))
        family = _selection_family(item)
        if key in selected_keys or (enforce_family_cap and family_counts.get(family, 0) >= family_cap):
            return False
        selected.append(dict(item))
        selected_keys.add(key)
        family_counts[family] = family_counts.get(family, 0) + 1
        return True

    for feature_id in ("bbox_overall", "datum_primary_axis"):
        for view_name in ("front", "top", "right"):
            match = next((
                item for item in selections
                if item.get("feature_id") == feature_id and item.get("view") == view_name
            ), None)
            if match is not None:
                append(match, enforce_family_cap=False)
                if len(selected) >= limit:
                    return selected

    for enforce_family_cap in (True, False):
        for view_name in ("front", "top", "right"):
            for item in selections:
                if item.get("view") != view_name:
                    continue
                append(item, enforce_family_cap=enforce_family_cap)
                if len(selected) >= limit:
                    return selected
    return selected


def _render_generated_model_region(dxf_path: Path, output: Path, size: tuple[int, int]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    document = ezdxf.readfile(str(dxf_path))
    entities = [
        entity for entity in document.modelspace()
        if str(entity.dxf.layer).upper() not in EXCLUDED_GENERATED_LAYERS
    ]
    extents = bbox.extents(entities, fast=True)
    if not extents.has_data:
        raise RuntimeError(f"No model entities found in {dxf_path}")
    temporary = output.with_suffix(".model-only.dxf")
    temporary_png = output.with_suffix(".model-only.png")
    clone = ezdxf.new(document.dxfversion)
    importer = ezdxf.addons.Importer(document, clone)
    importer.import_entities(entities, clone.modelspace())
    importer.finalize()
    clone.saveas(temporary)
    try:
        export_png(clone, clone.modelspace(), str(temporary_png), dark_bg=True)
        with Image.open(temporary_png) as source:
            rendered = source.convert("RGB")
        background = Image.new("RGB", rendered.size, rendered.getpixel((0, 0)))
        content_bounds = ImageChops.difference(rendered, background).getbbox()
        if content_bounds is None:
            raise RuntimeError(f"No rendered model pixels found in {dxf_path}")
        margin = max(12, int(max(rendered.size) * 0.025))
        left = max(0, content_bounds[0] - margin)
        top = max(0, content_bounds[1] - margin)
        right = min(rendered.width, content_bounds[2] + margin)
        bottom = min(rendered.height, content_bounds[3] + margin)
        cropped = rendered.crop((left, top, right, bottom))
        cropped.thumbnail(size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", size, rendered.getpixel((0, 0)))
        canvas.paste(cropped, ((size[0] - cropped.width) // 2, (size[1] - cropped.height) // 2))
        canvas.save(output)
    finally:
        for artifact in (
            temporary,
            temporary_png,
            temporary_png.with_suffix(".svg"),
            temporary_png.with_suffix(".pdf"),
        ):
            try:
                artifact.unlink(missing_ok=True)
            except PermissionError:
                pass


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _paste_fit(canvas: Image.Image, source_path: Path, box: tuple[int, int, int, int]) -> None:
    with Image.open(source_path) as source:
        image = source.convert("RGB")
    width, height = box[2] - box[0], box[3] - box[1]
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    left = box[0] + (width - image.width) // 2
    top = box[1] + (height - image.height) // 2
    canvas.paste(image, (left, top))


def _render_comparison(
    generated: Path,
    historical: Path,
    output: Path,
    *,
    case_name: str,
    profile: str,
    view_name: str,
) -> None:
    canvas = Image.new("RGB", (2600, 1320), PANEL)
    draw = ImageDraw.Draw(canvas)
    draw.text((42, 24), f"{case_name} | {profile.upper()} | {view_name.upper()}", font=_font(32, True), fill=WHITE)
    draw.text((42, 72), "Generated model + dimensions", font=_font(20, True), fill=BLUE)
    draw.text((1330, 72), "Original engineer drawing", font=_font(20, True), fill=WHITE)
    draw.rectangle((35, 112, 1285, 1275), fill=BACKGROUND, outline="#404040", width=2)
    draw.rectangle((1315, 112, 2565, 1275), fill=BACKGROUND, outline="#404040", width=2)
    _paste_fit(canvas, generated, (55, 132, 1265, 1255))
    _paste_fit(canvas, historical, (1335, 132, 2545, 1255))
    draw.text((42, 1285), "Generated panel excludes title blocks and tables; reference panel preserves native evidence.", font=_font(16), fill=MUTED)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def _best_matches(step_path: Path, historical_dxf: Path) -> tuple[DxfProjectionViewExtractorV2, dict[str, dict[str, Any]]]:
    shape = load_step(str(step_path))
    views = ViewProjector().project_all_views(shape, view_names=["front", "top", "right"])
    extractor = DxfProjectionViewExtractorV2(str(historical_dxf))
    matchable_views = tuple(
        view_name for view_name in ("front", "top", "right")
        if (views[view_name].get("visible") or views[view_name].get("hidden"))
    )
    if not matchable_views:
        raise RuntimeError(f"No projected geometry available for historical matching: {step_path}")
    selected, rows = _match_views(extractor, views, matchable_views, len(matchable_views))
    matches = {str(item["step_view"]): item for item in selected}
    for view_name in ("front", "top", "right"):
        candidates = [item for item in rows if item["step_view"] == view_name]
        if candidates and view_name not in matches:
            matches[view_name] = max(candidates, key=lambda item: float(item["comparison_score"]))
    return extractor, matches


def _drawing_entries(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    entries = list(report.get("orthographic_drawings") or [])
    half_section = report.get("half_section_drawing")
    if half_section:
        section_type = str((half_section.get("section_spec") or {}).get("view_type") or "")
        output_view = "central_section" if section_type == "CENTRAL_SECTION_CURVES" else "half_section"
        entries.append({**half_section, "output_view": output_view})
    return entries


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "schema_version": 1,
        "method": "UNIFIED_DRAWING_COMPARISON_V1",
        "config": str(config_path.resolve()),
        "cases": [],
    }
    for case in config.get("cases") or []:
        name = str(case["name"])
        step_path = _resolve_path(str(case["step_path"]), config_path.parent)
        historical_dxf = _resolve_path(str(case["historical_dxf"]), config_path.parent)
        case_dir = output_dir / name
        extractor, matches = _best_matches(step_path, historical_dxf)
        reference_dir = case_dir / "reference"
        reference_dir.mkdir(parents=True, exist_ok=True)
        reference_images: dict[str, Path] = {}
        for view_name, match in matches.items():
            bounds = expanded_crop_bounds(
                match["candidate_bbox"], extractor.sheet_bbox,
                float(case.get("reference_margin_fraction") or 0.28),
            )
            path = reference_dir / f"{view_name}.png"
            _render_native_region(historical_dxf, bounds, path, (1200, 1100))
            reference_images[view_name] = path

        maximum = build_maximum_selections(step_path, str(case.get("part_hint") or ""))
        ordinary = case.get("ordinary_selections") or []
        ordinary_auto_limit = int(case.get("ordinary_auto_limit") or 0)
        if not ordinary and ordinary_auto_limit:
            ordinary = select_representative_subset(maximum, ordinary_auto_limit, family_cap=2)
        maximum_limit = int(case.get("maximum_selection_limit") or 0)
        if maximum_limit:
            maximum = select_representative_subset(
                maximum,
                maximum_limit,
                family_cap=max(8, maximum_limit // 8),
            )
        profiles = {
            "ordinary": {
                "selections": ordinary,
                "relational": case.get("ordinary_relational_selections") or [],
                "pattern_angles": case.get("ordinary_pattern_angle_selections") or [],
                "annular": case.get("ordinary_annular_selections") or [],
            },
            "maximum": {
                "selections": maximum,
                "relational": case.get("maximum_relational_selections") or [],
                "pattern_angles": case.get("maximum_pattern_angle_selections") or [],
                "annular": case.get("maximum_annular_selections") or [],
            },
        }
        case_report: dict[str, Any] = {
            "name": name,
            "step_path": str(step_path),
            "historical_dxf": str(historical_dxf),
            "profiles": {},
        }
        for profile, profile_config in profiles.items():
            profile_dir = case_dir / profile
            report = render_minimal_multiview_drawing(
                step_path,
                profile_config["selections"],
                profile_dir / "drawings",
                part_hint=str(case.get("part_hint") or ""),
                include_half_section=bool(case.get("include_half_section")),
                relational_selections=profile_config["relational"],
                pattern_angle_selections=profile_config["pattern_angles"],
                annular_selections=profile_config["annular"],
                half_section_selection=case.get("half_section_selection"),
            )
            comparisons = []
            for entry in _drawing_entries(report):
                view_name = str(entry.get("output_view") or entry["view"])
                reference_view = (
                    str(case.get("half_section_reference_view") or "right")
                    if view_name in {"half_section", "central_section"}
                    else view_name
                )
                if reference_view not in reference_images:
                    reference_view = next(iter(reference_images))
                generated = profile_dir / "model_only" / f"{view_name}.png"
                generated.parent.mkdir(parents=True, exist_ok=True)
                _render_generated_model_region(Path(entry["files"]["dxf"]), generated, (1200, 1100))
                comparison = profile_dir / "comparison" / f"{view_name}.png"
                _render_comparison(
                    generated, reference_images[reference_view], comparison,
                    case_name=name, profile=profile, view_name=view_name,
                )
                comparisons.append(str(comparison.resolve()))
            case_report["profiles"][profile] = {
                "success": report["success"],
                "selection_count": report["configured_selection_count"],
                "comparison_images": comparisons,
                "drawing_report": str((profile_dir / "drawings" / "minimal_multiview_report.json").resolve()),
            }
        summary["cases"].append(case_report)
    report_path = output_dir / "unified_drawing_comparison_report.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    summary = run(args.config.resolve(), args.output.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(
        profile["success"]
        for case in summary["cases"]
        for profile in case["profiles"].values()
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
