"""Replay extracted drawing rules on the matched STEP HLR projection.

The replay is an audit visualization, not a production drawing.  DXF
definition points are reconstructed from view-relative coordinates and then
mapped back into STEP projection space with the locked registration transform.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from auto_2d_drawing.tolerance.assembly_components import load_component_shape  # noqa: E402
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor  # noqa: E402
from auto_2d_drawing.tolerance.projection_registration_v2 import SimilarityTransform2D  # noqa: E402
from auto_2d_drawing.view_projector import ViewProjector  # noqa: E402
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2  # noqa: E402
from generate_tolerance_edge_review import _render_native_dxf  # noqa: E402


BACKGROUND = "#171717"
PANEL = "#262626"
WHITE = "#f5f5f5"
MUTED = "#a3a3a3"
MODEL_VISIBLE = "#d4d4d4"
MODEL_HIDDEN = "#525252"
RELIABLE = "#22d3ee"
REVIEW = "#f59e0b"
CONTACT = "#facc15"
METHOD = "LOCKED_STEP_PROJECTION_DRAWING_RULE_ROUND_TRIP_V1"


def _font(size: int, bold: bool = False):
    names = ("msjhbd.ttc", "arialbd.ttf") if bold else ("msjh.ttc", "arial.ttf")
    for name in names:
        path = Path("C:/Windows/Fonts") / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def denormalise_point(point: Sequence[float], bbox: Sequence[float]) -> tuple[float, float]:
    width = float(bbox[2]) - float(bbox[0])
    height = float(bbox[3]) - float(bbox[1])
    return (
        float(bbox[0]) + float(point[0]) * width,
        float(bbox[1]) + float(point[1]) * height,
    )


def _rule_anchor_keys(rule: dict[str, Any]) -> tuple[str, ...]:
    dim_type = str(rule.get("dimension", {}).get("dim_type") or "LINEAR").upper()
    if dim_type in {"DIAMETER", "RADIAL"}:
        return "defpoint", "defpoint4"
    if dim_type == "ANGLE":
        return "defpoint2", "defpoint3", "defpoint4"
    return "defpoint2", "defpoint3"


def rule_step_anchors(
    rule: dict[str, Any],
    transform: SimilarityTransform2D,
) -> list[tuple[float, float]]:
    points = rule.get("placement", {}).get("definition_points_normalized") or {}
    bbox = rule["view_bbox"]
    anchors = []
    for key in _rule_anchor_keys(rule):
        normalized = points.get(key)
        if not isinstance(normalized, Sequence) or len(normalized) < 2:
            continue
        dxf_point = denormalise_point(normalized, bbox)
        # Bad converter placeholders can be hundreds of view spans away.
        if max(abs(float(normalized[0])), abs(float(normalized[1]))) > 8.0:
            continue
        anchors.append(transform.inverse_point(dxf_point))
    return anchors


def _format_tolerance(config: dict[str, Any]) -> str:
    mode = str(config.get("mode") or "NONE").upper()
    if mode == "FIT" and config.get("fit_class"):
        return str(config["fit_class"])
    if mode == "CUSTOM_SYMMETRIC":
        value = config.get("dev", config.get("value", config.get("tolerance")))
        return f" ±{float(value):g}" if value is not None else ""
    upper = config.get("upper_dev")
    lower = config.get("lower_dev")
    if upper is not None or lower is not None:
        return f" +{float(upper or 0):g}/{float(lower or 0):g}"
    return ""


def format_rule_text(rule: dict[str, Any]) -> str:
    dimension = rule["dimension"]
    prefix = str(dimension.get("prefix") or "")
    nominal = float(dimension.get("nominal_value") or 0.0)
    tolerance = _format_tolerance(dimension.get("tolerance_config") or {})
    return f"{prefix}{nominal:g}{tolerance}"


def _edge_points(edge: dict[str, Any]) -> list[tuple[float, float]]:
    if edge.get("type") == "line":
        return [tuple(edge["p1"]), tuple(edge["p2"])]
    return [tuple(point) for point in edge.get("points") or []]


def _expand_bbox(bbox: Sequence[float], x_factor: float, y_factor: float) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = (float(value) for value in bbox)
    width, height = max(xmax - xmin, 1e-9), max(ymax - ymin, 1e-9)
    return (
        xmin - width * x_factor,
        ymin - height * y_factor,
        xmax + width * x_factor,
        ymax + height * y_factor,
    )


def _mapper(world: Sequence[float], box: Sequence[int]):
    xmin, ymin, xmax, ymax = (float(value) for value in world)
    left, top, right, bottom = (float(value) for value in box)
    scale = min((right - left) / max(xmax - xmin, 1e-9), (bottom - top) / max(ymax - ymin, 1e-9))
    used_w, used_h = (xmax - xmin) * scale, (ymax - ymin) * scale
    ox = left + ((right - left) - used_w) / 2.0
    oy = top + ((bottom - top) - used_h) / 2.0

    def apply(point: Sequence[float]) -> tuple[float, float]:
        return ox + (float(point[0]) - xmin) * scale, oy + (ymax - float(point[1])) * scale

    return apply


def _draw_model(draw: ImageDraw.ImageDraw, view_data: dict[str, Any], mapper) -> None:
    for edge in view_data.get("hidden") or []:
        points = [mapper(point) for point in _edge_points(edge)]
        if len(points) >= 2:
            for left, right in zip(points, points[1:]):
                draw.line((left, right), fill=MODEL_HIDDEN, width=2)
    for edge in view_data.get("visible") or []:
        points = [mapper(point) for point in _edge_points(edge)]
        if len(points) >= 2:
            draw.line(points, fill=MODEL_VISIBLE, width=4)


def _layout_line(
    anchors: Sequence[tuple[float, float]],
    side: str,
    rank: int,
    model_bbox: Sequence[float],
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if len(anchors) < 2:
        return None
    xmin, ymin, xmax, ymax = (float(value) for value in model_bbox)
    width, height = max(xmax - xmin, 1e-9), max(ymax - ymin, 1e-9)
    gap = max(min(width, height) * 0.42, max(width, height) * 0.025)
    if side == "TOP":
        y = ymax + gap * (rank + 0.5)
        return (anchors[0][0], y), (anchors[1][0], y)
    if side == "BOTTOM":
        y = ymin - gap * (rank + 0.5)
        return (anchors[0][0], y), (anchors[1][0], y)
    if side == "LEFT":
        x = xmin - gap * (rank + 0.5)
        return (x, anchors[0][1]), (x, anchors[1][1])
    if side == "RIGHT":
        x = xmax + gap * (rank + 0.5)
        return (x, anchors[0][1]), (x, anchors[1][1])
    return anchors[0], anchors[1]


def _draw_rule(
    draw: ImageDraw.ImageDraw,
    rule: dict[str, Any],
    anchors: Sequence[tuple[float, float]],
    model_bbox: Sequence[float],
    mapper,
) -> bool:
    if not anchors:
        return False
    color = RELIABLE if rule.get("evidence_status") == "EXTRACTED_RELIABLE_2D" else REVIEW
    side = str(rule.get("placement", {}).get("side") or "IN_VIEW")
    rank = int(rule.get("placement", {}).get("rank") or 1)
    dim_type = str(rule.get("dimension", {}).get("dim_type") or "LINEAR")
    mapped_anchors = [mapper(point) for point in anchors]
    for point in mapped_anchors:
        draw.ellipse((point[0] - 8, point[1] - 8, point[0] + 8, point[1] + 8), fill=CONTACT, outline=BACKGROUND, width=2)
    layout = _layout_line(anchors, side, rank, model_bbox)
    if layout is None:
        return False
    left, right = layout
    left_px, right_px = mapper(left), mapper(right)
    if dim_type in {"RADIAL", "DIAMETER"} or len(anchors) == 1:
        draw.line((mapped_anchors[0], left_px), fill=BACKGROUND, width=9)
        draw.line((mapped_anchors[0], left_px), fill=color, width=4)
        text_point = left_px
    else:
        for anchor, endpoint in zip(mapped_anchors[:2], (left_px, right_px)):
            draw.line((anchor, endpoint), fill=color, width=3)
        draw.line((left_px, right_px), fill=BACKGROUND, width=9)
        draw.line((left_px, right_px), fill=color, width=4)
        text_point = ((left_px[0] + right_px[0]) / 2.0, (left_px[1] + right_px[1]) / 2.0)
    label = f"{rule['dimension_handle']}  {format_rule_text(rule)}"
    bounds = draw.textbbox((0, 0), label, font=_font(20, bold=True))
    text_w, text_h = bounds[2] - bounds[0], bounds[3] - bounds[1]
    tx, ty = text_point[0] - text_w / 2.0, text_point[1] - text_h - 7
    draw.rectangle((tx - 4, ty - 2, tx + text_w + 4, ty + text_h + 3), fill=BACKGROUND)
    draw.text((tx, ty), label, font=_font(20, bold=True), fill=color)
    return True


def _load_pose(path: Path, component_name: str) -> tuple[dict[str, Any], SimilarityTransform2D]:
    report = json.loads(path.read_text(encoding="utf-8"))
    component = next(
        (item for item in report.get("components") or [] if item.get("component_name") == component_name),
        None,
    )
    if component is None:
        raise KeyError(f"Component {component_name!r} not found in pose report")
    registration = component["pose_search"]["locked_registration"]
    raw = registration["transform"]
    transform = SimilarityTransform2D(
        scale=float(raw["scale"]),
        rotation_degrees=float(raw["rotation_degrees"]),
        translation=(float(raw["translation"][0]), float(raw["translation"][1])),
        mirrored=bool(raw.get("mirrored", False)),
    )
    return registration, transform


def _native_crop_bounds(rules: Sequence[dict[str, Any]]) -> tuple[float, float, float, float]:
    bbox = [float(value) for value in rules[0]["view_bbox"]]
    points = [(bbox[0], bbox[1]), (bbox[2], bbox[3])]
    for rule in rules:
        normalized = rule.get("placement", {}).get("text_anchor_normalized")
        if isinstance(normalized, Sequence) and len(normalized) >= 2 and max(abs(float(normalized[0])), abs(float(normalized[1]))) <= 5.0:
            points.append(denormalise_point(normalized, bbox))
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    return (
        min(xs) - max(width * 0.15, 2.0),
        min(ys) - max(height * 0.15, 2.0),
        max(xs) + max(width * 0.15, 2.0),
        max(ys) + max(height * 0.15, 2.0),
    )


def render_replay(
    rules_report_path: Path,
    pose_report_path: Path,
    step_path: Path,
    dxf_path: Path,
    component_name: str,
    component_label: str,
    output_dir: Path,
) -> dict[str, Any]:
    rules_report = json.loads(rules_report_path.read_text(encoding="utf-8"))
    rules = list(rules_report.get("drawing_rule_cases") or [])
    if not rules:
        raise RuntimeError("Drawing-rule report contains no cases")
    registration, transform = _load_pose(pose_report_path, component_name)
    shape = load_component_shape(str(step_path), component_label)
    if shape is None or shape.IsNull():
        raise RuntimeError(f"Could not load component label {component_label} from {step_path}")
    step_view = str(registration["step_view"])
    view_data = ViewProjector().project_all_views(shape, view_names=[step_view])[step_view]
    model_bbox = view_data["bbox"]

    output_dir.mkdir(parents=True, exist_ok=True)
    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    native_dimensions = DxfToleranceExtractor().extract_from_file(str(dxf_path), include_rejected=True, native_dimensions_only=True)
    if not native_dimensions:
        raise RuntimeError("Source DXF contains no native dimensions")
    original = output_dir / "00_original_dxf_rule_source.png"
    _render_native_dxf(
        dxf_path,
        native_dimensions[0],
        {},
        [],
        extractor.sheet_bbox,
        _native_crop_bounds(rules),
        original,
        (1800, 1450),
    )

    outputs = []
    for mode, selected in (
        ("reliable", [rule for rule in rules if rule.get("evidence_status") == "EXTRACTED_RELIABLE_2D"]),
        ("all", rules),
    ):
        canvas = Image.new("RGB", (3800, 1750), BACKGROUND)
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((20, 20, 3780, 1730), radius=18, fill=PANEL)
        draw.text((55, 42), f"Drawing Rule Round-trip | {component_name} | {mode.upper()}", font=_font(32, True), fill=WHITE)
        draw.text(
            (55, 92),
            f"Locked STEP view={step_view}  mirrored={transform.mirrored}  rotation={transform.rotation_degrees:.4f}°  "
            f"rules={len(selected)}/{len(rules)}",
            font=_font(19), fill=MUTED,
        )
        left_box = (55, 155, 1860, 1680)
        right_box = (1940, 155, 3745, 1680)
        draw.rectangle(left_box, fill=BACKGROUND, outline="#404040", width=2)
        draw.rectangle(right_box, fill=BACKGROUND, outline="#404040", width=2)
        draw.text((75, 170), "ORIGINAL ANNOTATED DXF", font=_font(22, True), fill=WHITE)
        draw.text((1960, 170), "STEP HLR + REPLAYED DRAWING RULES", font=_font(22, True), fill=WHITE)
        with Image.open(original) as source:
            original_image = source.convert("RGB")
        original_image.thumbnail((1740, 1390), Image.Resampling.LANCZOS)
        canvas.paste(original_image, (left_box[0] + (left_box[2] - left_box[0] - original_image.width) // 2, 235))

        world = _expand_bbox(model_bbox, 0.42, 3.4)
        map_point = _mapper(world, (1970, 235, 3715, 1605))
        _draw_model(draw, view_data, map_point)
        drawn = 0
        missing = []
        for rule in selected:
            anchors = rule_step_anchors(rule, transform)
            if _draw_rule(draw, rule, anchors, model_bbox, map_point):
                drawn += 1
            else:
                missing.append(rule["dimension_handle"])
        draw.rectangle((1980, 1620, 2000, 1640), fill=RELIABLE)
        draw.text((2010, 1614), "reliable 2D attachment", font=_font(17), fill=MUTED)
        draw.rectangle((2255, 1620, 2275, 1640), fill=REVIEW)
        draw.text((2285, 1614), "review candidate", font=_font(17), fill=MUTED)
        draw.ellipse((2515, 1620, 2535, 1640), fill=CONTACT)
        draw.text((2545, 1614), "replayed STEP-space anchor", font=_font(17), fill=MUTED)
        output = output_dir / f"01_{mode}_rules_round_trip.png"
        canvas.save(output)
        outputs.append({"mode": mode, "path": str(output), "selected": len(selected), "drawn": drawn, "missing_handles": missing})

    report = {
        "schema_version": 1,
        "method": METHOD,
        "production_updated": False,
        "component_name": component_name,
        "component_label": component_label,
        "step_path": str(step_path),
        "dxf_path": str(dxf_path),
        "step_view": step_view,
        "registration": registration,
        "source_rule_count": len(rules),
        "outputs": outputs,
    }
    (output_dir / "drawing_rule_replay_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", required=True)
    parser.add_argument("--pose-report", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--dxf", required=True)
    parser.add_argument("--component-name", required=True)
    parser.add_argument("--component-label", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = render_replay(
        Path(args.rules).resolve(),
        Path(args.pose_report).resolve(),
        Path(args.step).resolve(),
        Path(args.dxf).resolve(),
        args.component_name,
        args.component_label,
        Path(args.output).resolve(),
    )
    print(json.dumps({"source_rule_count": report["source_rule_count"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
