"""Lazy, cached visual assets for the tolerance extraction audit UI.

The audit page intentionally uses a server-rendered model snapshot instead of
starting one WebGL scene per historical case.  A snapshot is generated only
after an engineer opens a drawing and selects a tolerance.
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from auto_2d_drawing.step_reader import load_step


BACKGROUND = "#171717"
PANEL = "#262626"
MESH_EDGE = (54, 68, 82)
HIGHLIGHT = "#f97316"
TEXT = "#f5f5f5"
MUTED = "#a3a3a3"


def _font(size: int, bold: bool = False):
    candidates = (
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _read_binary_stl(data: bytes) -> List[Tuple[Tuple[float, float, float], ...]]:
    if len(data) < 84:
        return []
    triangle_count = struct.unpack_from("<I", data, 80)[0]
    if 84 + triangle_count * 50 != len(data):
        return []
    triangles = []
    offset = 84
    for _ in range(triangle_count):
        values = struct.unpack_from("<12fH", data, offset)
        triangles.append((
            (values[3], values[4], values[5]),
            (values[6], values[7], values[8]),
            (values[9], values[10], values[11]),
        ))
        offset += 50
    return triangles


def _read_ascii_stl(data: bytes) -> List[Tuple[Tuple[float, float, float], ...]]:
    vertices: List[Tuple[float, float, float]] = []
    triangles = []
    for raw_line in data.decode("ascii", errors="ignore").splitlines():
        fields = raw_line.strip().split()
        if len(fields) == 4 and fields[0].lower() == "vertex":
            vertices.append(tuple(float(value) for value in fields[1:4]))
            if len(vertices) == 3:
                triangles.append(tuple(vertices))
                vertices = []
    return triangles


def _read_stl(path: Path) -> List[Tuple[Tuple[float, float, float], ...]]:
    data = path.read_bytes()
    return _read_binary_stl(data) or _read_ascii_stl(data)


def _feature_box(metadata: Dict[str, Any]) -> Tuple[List[float], List[float]] | None:
    source = dict(metadata.get("matched_feature_source_info") or {})
    geometry = dict(source.get("geometry") or {})
    center = geometry.get("center") or source.get("center")
    size = geometry.get("size")
    if isinstance(center, (list, tuple)) and len(center) >= 3:
        center = [float(value) for value in center[:3]]
    else:
        topology = dict((metadata.get("topology_mapping") or {}).get("face") or {})
        center = topology.get("center")
        source = topology
        if not isinstance(center, (list, tuple)) or len(center) < 3:
            return None
        center = [float(value) for value in center[:3]]

    if isinstance(size, (list, tuple)) and len(size) >= 3:
        return center, [max(abs(float(value)), 0.25) for value in size[:3]]

    radius = float(source.get("radius") or geometry.get("radius") or 0.0)
    length = float(source.get("length") or geometry.get("length") or 0.0)
    axis = source.get("axis_dir") or source.get("axis") or geometry.get("axis_dir")
    if radius <= 0.0 or length <= 0.0 or not isinstance(axis, (list, tuple)) or len(axis) < 3:
        return None
    norm = math.sqrt(sum(float(value) ** 2 for value in axis[:3])) or 1.0
    direction = [abs(float(value) / norm) for value in axis[:3]]
    diameter = radius * 2.0
    size = [direction[index] * length + (1.0 - direction[index]) * diameter for index in range(3)]
    return center, [max(value, 0.25) for value in size]


def _corners(center: Sequence[float], size: Sequence[float]) -> List[List[float]]:
    return [
        [
            float(center[axis]) + (0.5 if mask & (1 << axis) else -0.5) * float(size[axis])
            for axis in range(3)
        ]
        for mask in range(8)
    ]


def _render_snapshot(
    triangles: Sequence[Tuple[Tuple[float, float, float], ...]],
    metadata: Dict[str, Any],
    output_path: Path,
) -> None:
    width, height = 1100, 760
    image = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(image)
    if not triangles:
        draw.text((40, 40), "Model preview unavailable", fill=TEXT, font=_font(24, True))
        image.save(output_path)
        return

    points = [point for triangle in triangles for point in triangle]
    center = [sum(point[axis] for point in points) / len(points) for axis in range(3)]
    azimuth, elevation = math.radians(35), math.radians(28)
    ca, sa = math.cos(azimuth), math.sin(azimuth)
    ce, se = math.cos(elevation), math.sin(elevation)

    def transform(point: Sequence[float]):
        x, y, z = (float(point[index]) - center[index] for index in range(3))
        x1, y1 = ca * x - sa * y, sa * x + ca * y
        return x1, ce * y1 - se * z, se * y1 + ce * z

    transformed = [tuple(transform(point) for point in triangle) for triangle in triangles]
    flat = [point for triangle in transformed for point in triangle]
    xs = [point[0] for point in flat]
    ys = [point[1] for point in flat]
    scale = min(970.0 / max(max(xs) - min(xs), 1e-9), 590.0 / max(max(ys) - min(ys), 1e-9))

    def screen_transformed(point: Sequence[float]):
        return width / 2 + point[0] * scale, height / 2 - 10 - point[1] * scale

    def screen(point: Sequence[float]):
        return screen_transformed(transform(point))

    order = sorted(range(len(transformed)), key=lambda index: sum(p[2] for p in transformed[index]) / 3.0)
    stride = max(1, len(order) // 60000)
    for triangle_index in order[::stride]:
        triangle = transformed[triangle_index]
        a, b, c = triangle
        u = tuple(b[index] - a[index] for index in range(3))
        v = tuple(c[index] - a[index] for index in range(3))
        normal = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        normal_length = math.sqrt(sum(value * value for value in normal))
        light = 0.55 if normal_length <= 1e-12 else 0.34 + 0.58 * abs(normal[2] / normal_length)
        color = tuple(int(base * light + fill * (1.0 - light)) for base, fill in zip((58, 96, 132), (166, 172, 178)))
        draw.polygon([screen_transformed(point) for point in triangle], fill=color, outline=MESH_EDGE)

    feature_box = _feature_box(metadata)
    if feature_box:
        box_center, box_size = feature_box
        projected = [screen(point) for point in _corners(box_center, box_size)]
        edges = (
            (0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
            (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7),
        )
        for start, end in edges:
            draw.line((projected[start], projected[end]), fill=HIGHLIGHT, width=5)
        anchor = screen(box_center)
        label = str(metadata.get("matched_feature_id") or metadata.get("canonical_feature_id") or "matched feature")
        draw.rounded_rectangle((anchor[0] + 10, anchor[1] - 38, anchor[0] + 310, anchor[1] + 5), 6, fill=BACKGROUND, outline=HIGHLIGHT, width=2)
        draw.text((anchor[0] + 20, anchor[1] - 33), label, fill=HIGHLIGHT, font=_font(18, True))

    draw.text((28, 24), "Original STEP / matched 3D feature", fill=TEXT, font=_font(22, True))
    draw.text((28, height - 36), "Orange box: feature linked to the selected DXF tolerance", fill=MUTED, font=_font(16))
    image.save(output_path, optimize=True)


def ensure_model_preview(case_id: str, step_path: str, metadata: Dict[str, Any], cache_dir: str) -> str:
    """Return a cached PNG path for one historical case."""
    source = Path(step_path)
    if not source.is_file():
        raise FileNotFoundError(step_path)
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    source_signature = f"{source.resolve()}:{source.stat().st_mtime_ns}:{source.stat().st_size}"
    source_digest = hashlib.sha1(source_signature.encode("utf-8")).hexdigest()[:16]
    case_digest = hashlib.sha1(f"{case_id}:{source_signature}".encode("utf-8")).hexdigest()[:20]
    preview_path = root / f"{case_digest}.png"
    if preview_path.exists():
        return str(preview_path)

    stl_path = root / f"{source_digest}.stl"
    if not stl_path.exists():
        from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
        from OCC.Core.StlAPI import StlAPI_Writer

        shape = load_step(str(source))
        BRepMesh_IncrementalMesh(shape, 0.18, False, 0.30, True).Perform()
        temporary_stl = stl_path.with_suffix(".tmp.stl")
        if not StlAPI_Writer().Write(shape, str(temporary_stl)):
            raise RuntimeError(f"STL export failed: {source}")
        os.replace(temporary_stl, stl_path)

    temporary_png = preview_path.with_suffix(".tmp.png")
    _render_snapshot(_read_stl(stl_path), metadata, temporary_png)
    os.replace(temporary_png, preview_path)
    return str(preview_path)
