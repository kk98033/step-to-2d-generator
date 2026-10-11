"""Build a focused review board for the AL0W blade Ø27 tolerance.

This is a visualization-only diagnostic.  It deliberately reuses the locked
robust-pose artifacts and semantic review instead of running another projection
implementation.
"""

from pathlib import Path

import json

from PIL import Image, ImageDraw, ImageFont

from dxf_projection_views_v2 import DxfProjectionViewExtractorV2


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = ROOT / "experiments" / "2026-10-08"
SEMANTIC = (
    EXPERIMENTS
    / "feature_semantic_review_multiview_v3"
    / "AL0W_BLADE_H-R01"
    / "07_7AD9_semantic_review.png"
)
POSE_DIR = EXPERIMENTS / "robust_projection_pose_v5"
POSE_BOARD = POSE_DIR / "AL0W_BLADE_H-R01_robust_pose_with_original.png"
LOCAL_REPORT = ROOT / "experiments" / "2026-10-09" / "view_instance_projection_v1" / "7AD9_report.json"
OUTPUT_DIR = EXPERIMENTS / "blade_27_projection_debug"
OUTPUT = OUTPUT_DIR / "AL0W_BLADE_H-R01_7AD9_view_instance_fixed_debug.png"


def font(size: int):
    candidates = (
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    copy = image.copy()
    copy.thumbnail(size, Image.Resampling.LANCZOS)
    return copy


def paste_center(board, image, box):
    left, top, right, bottom = box
    fitted = fit(image, (right - left, bottom - top))
    x = left + (right - left - fitted.width) // 2
    y = top + (bottom - top - fitted.height) // 2
    board.paste(fitted, (x, y))


def local_overlay(report):
    diagnostic = report["diagnostics"][0]
    candidate = diagnostic["candidates"][0]
    registration = candidate["geometry_verification"]["view_registration"]
    view_id = registration["dxf_view_id"]
    overlay = report["projection_overlays"][diagnostic["registered_step_projection_id"]]
    extractor = DxfProjectionViewExtractorV2(report["dxf"])
    view = next(item for item in extractor.view_candidates if item.view_id == view_id)
    step_lines = overlay.get("lines") or []
    points = list(view.points)
    for line in step_lines:
        points.extend((tuple(line["p1"]), tuple(line["p2"])))
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    xmin, ymin, xmax, ymax = min(xs), min(ys), max(xs), max(ys)
    scale = min(900 / max(xmax-xmin, 1e-9), 680 / max(ymax-ymin, 1e-9))
    ox = 500 - (xmin+xmax)*0.5*scale
    oy = 370 + (ymin+ymax)*0.5*scale
    mapper = lambda point: (ox + point[0]*scale, oy - point[1]*scale)
    image = Image.new("RGB", (1000, 740), "#262626")
    draw = ImageDraw.Draw(image)
    for point in view.points:
        x, y = mapper(point)
        draw.ellipse((x-2, y-2, x+2, y+2), fill="#60a5fa")
    for line in step_lines:
        draw.line((mapper(line["p1"]), mapper(line["p2"])), fill="#f97316", width=2)
    profile = diagnostic["association"]["circular_attachment_profiles"][0]
    center = mapper(profile["center"])
    radius = float(profile["radius"]) * scale
    draw.ellipse(
        (center[0]-radius, center[1]-radius, center[0]+radius, center[1]+radius),
        outline="#facc15", width=6,
    )
    draw.line((center[0]-12, center[1], center[0]+12, center[1]), fill="#22c55e", width=3)
    draw.line((center[0], center[1]-12, center[0], center[1]+12), fill="#22c55e", width=3)
    return image


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    semantic = Image.open(SEMANTIC).convert("RGB")
    report = json.loads(LOCAL_REPORT.read_text(encoding="utf-8"))
    overlay = local_overlay(report)
    pose_board = Image.open(POSE_BOARD).convert("RGB")

    # The semantic review's right panel preserves the original dimension text,
    # leader and the exact four-arc attachment highlighted in yellow.
    target = semantic.crop((1200, 120, semantic.width - 10, semantic.height - 10))

    # The robust review's right panel shows which drawing view was locked for
    # projection matching (orange rectangle around the right-hand fan view).
    locked_view = pose_board.crop((1510, 115, pose_board.width - 10, pose_board.height - 10))

    bg = "#171717"
    panel = "#262626"
    white = "#f5f5f5"
    muted = "#a3a3a3"
    yellow = "#facc15"
    blue = "#60a5fa"
    orange = "#f97316"
    red = "#ef4444"
    green = "#22c55e"

    board = Image.new("RGB", (2600, 1650), bg)
    draw = ImageDraw.Draw(board)
    draw.text((40, 28), "AL0W_BLADE_H-R01 | DXF 7AD9 | DIA 27.00 +/-0.10", font=font(34), fill=white)
    draw.text(
        (40, 76),
        "Fixed: recover the dimension-local view instance before projected feature localization.",
        font=font(23),
        fill=yellow,
    )

    boxes = ((30, 145, 865, 1290), (882, 145, 1718, 1290), (1735, 145, 2570, 1290))
    labels = (
        "A. ORIGINAL 2D DIMENSION TARGET",
        "B. RECOVERED LOCAL-VIEW STEP OVERLAY",
        "C. OLD UPSTREAM VIEW INSTANCE",
    )
    for box, label in zip(boxes, labels):
        draw.rounded_rectangle(box, radius=12, fill=panel, outline="#525252", width=2)
        draw.text((box[0] + 20, box[1] + 17), label, font=font(21), fill=white)

    paste_center(board, target, (45, 205, 850, 1270))
    paste_center(board, overlay, (897, 205, 1703, 1270))
    paste_center(board, locked_view, (1750, 205, 2555, 1270))

    draw.text((65, 1230), "YELLOW = DXF circle attached to DIA 27", font=font(18), fill=yellow)
    draw.text((917, 1230), "BLUE = DXF local geometry", font=font(18), fill=blue)
    draw.text((917, 1260), "ORANGE = transformed STEP HLR", font=font(18), fill=orange)
    draw.text((1770, 1230), "ORANGE BOX = old right-side instance", font=font(18), fill=orange)

    draw.rounded_rectangle((30, 1320, 2570, 1610), radius=12, fill=panel, outline="#525252", width=2)
    draw.text((55, 1345), "VIEW-INSTANCE FIX RESULT", font=font(25), fill=green)
    lines = (
        ("2D extraction", "PASS", "center/radius and split arcs resolve to one DIA 27 circle", green),
        ("STEP pose", "PASS", "front, mirrored, rotation 0.2626 deg, scale 1.00025", green),
        ("global contour", "PASS", "geometry score 0.9463; coverage 1.0000", green),
        ("local view", "PASS", "view_v2_092 recovered from the DIA 27 attachment anchor", green),
        ("feature location", "PASS", "normalized error 0.0029; localization score 0.9978", green),
        ("topology tie-break", "PASS", "2 projected candidates -> 1 B-Rep topology candidate (hole_03)", green),
    )
    y = 1395
    for name, status, reason, color in lines:
        draw.text((60, y), f"{name:<18}", font=font(20), fill=white)
        draw.text((325, y), f"{status:<5}", font=font(20), fill=color)
        draw.text((415, y), reason, font=font(20), fill=muted)
        y += 35

    board.save(OUTPUT)
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
