"""
特徵圖層輸出。

Feature record 結構保留給未來公差模型使用:
  {
    id, type, name, view, role,
    nominal, tolerance_key,
    geometry: {kind, center/radius/position...},
    source: {extractor, confidence}
  }
"""


def build_feature_records(feature_data, part_type):
    summary = feature_data.summary()
    records = []

    bbox = summary.get("bounding_box", {})
    records.append({
        "id": "bbox_overall",
        "type": "overall_size",
        "name": "外框整體尺寸",
        "view": "front",
        "role": "assembly",
        "nominal": bbox,
        "tolerance_key": "overall_size",
        "geometry": {"kind": "bbox", "size": bbox},
        "source": {"extractor": "FeatureExtractor.summary", "confidence": 0.95},
    })

    for idx, hole in enumerate(summary.get("holes", [])[:12], start=1):
        dia = hole.get("Ø", 0)
        max_bbox = max(bbox.values()) if bbox else 0
        records.append({
            "id": f"hole_{idx}",
            "type": "hole",
            "name": f"孔 Ø{dia:.2f}",
            "view": "front",
            "role": "mounting" if part_type == "FAN_HOUSING" and dia < max_bbox * 0.25 else "functional",
            "nominal": {"diameter": dia, "length": hole.get("len", 0)},
            "tolerance_key": "mounting_hole" if part_type == "FAN_HOUSING" else "hole",
            "geometry": {"kind": "diameter", "diameter": dia},
            "source": {"extractor": "FeatureExtractor.holes", "confidence": 0.75},
        })

    for idx, shaft in enumerate(summary.get("shafts", [])[:12], start=1):
        dia = shaft.get("Ø", 0)
        records.append({
            "id": f"shaft_{idx}",
            "type": "shaft_or_boss",
            "name": f"軸/凸台 Ø{dia:.2f}",
            "view": "front",
            "role": "datum" if idx == 1 else "functional",
            "nominal": {"diameter": dia, "length": shaft.get("len", 0)},
            "tolerance_key": "boss_diameter",
            "geometry": {"kind": "diameter", "diameter": dia},
            "source": {"extractor": "FeatureExtractor.shafts", "confidence": 0.75},
        })

    for idx, step in enumerate(summary.get("steps", [])[:10], start=1):
        records.append({
            "id": f"step_{idx}",
            "type": "step",
            "name": f"階梯 Ø{step.get('Ø', 0):.2f}",
            "view": "right",
            "role": "manufacturing",
            "nominal": {
                "diameter": step.get("Ø", 0),
                "length": step.get("len", 0),
                "position": step.get("pos", 0),
            },
            "tolerance_key": "step_depth",
            "geometry": {"kind": "axial_step", "position": step.get("pos", 0)},
            "source": {"extractor": "FeatureExtractor.steps", "confidence": 0.65},
        })

    if part_type == "STAMPED_FAN_BASE":
        records.extend([
            {
                "id": "datum_a_center_axis",
                "type": "datum",
                "name": "基準A 中央旋轉軸",
                "view": "front",
                "role": "datum",
                "nominal": {},
                "tolerance_key": "datum_axis",
                "geometry": {"kind": "axis", "axis": "center"},
                "source": {"extractor": "StampedFanBaseExtractor", "confidence": 0.9},
            },
            {
                "id": "blade_stamping_zone",
                "type": "stamped_blade_zone",
                "name": "葉片/導流沖壓區",
                "view": "front",
                "role": "functional",
                "nominal": {},
                "tolerance_key": "blade_profile_zone",
                "geometry": {"kind": "annular_zone"},
                "source": {"extractor": "StampedFanBaseExtractor", "confidence": 0.7},
            },
            {
                "id": "stamping_process_notes",
                "type": "process_requirement",
                "name": "沖壓方向/毛邊/平面度製程要求",
                "view": "front",
                "role": "manufacturing",
                "nominal": {},
                "tolerance_key": "stamping_process",
                "geometry": {"kind": "note"},
                "source": {"extractor": "StampedFanBaseExtractor", "confidence": 0.8},
            },
        ])

    return records


def build_projected_feature_records(view_data, part_type):
    records = []
    if not view_data:
        return records

    for view_name, vd in view_data.items():
        if not vd:
            continue
        bbox = vd.get("bbox")
        if bbox:
            x0, y0, x1, y1 = bbox
            records.append({
                "id": f"{view_name}_bbox_center",
                "type": "projected_bbox_center",
                "name": f"{view_name} 投影中心",
                "view": view_name,
                "role": "reference",
                "nominal": {},
                "tolerance_key": "projected_reference",
                "geometry": {"kind": "point", "point": ((x0 + x1) / 2.0, (y0 + y1) / 2.0)},
                "source": {"extractor": "ViewProjector.bbox", "confidence": 0.9},
            })

        circles = [e for e in vd.get("visible", []) if e.get("type") == "circle"]
        circles.sort(key=lambda c: c.get("radius", 0), reverse=True)
        picked = []
        for c in circles:
            center = c.get("center")
            radius = c.get("radius", 0)
            if not center or radius <= 0:
                continue
            if any(_dist2(center, old.get("center")) < 0.5 and abs(radius - old.get("radius", 0)) < 0.5 for old in picked):
                continue
            picked.append(c)
            if len(picked) >= 18:
                break

        for idx, c in enumerate(picked, start=1):
            records.append({
                "id": f"{view_name}_circle_{idx}",
                "type": "projected_circle",
                "name": f"{view_name} 圓/孔候選 {idx}",
                "view": view_name,
                "role": "datum" if idx == 1 and part_type in ("STAMPED_FAN_BASE", "FAN_HOUSING") else "feature",
                "nominal": {"diameter": c["radius"] * 2},
                "tolerance_key": "projected_circle",
                "geometry": {"kind": "circle", "center": c["center"], "radius": c["radius"]},
                "source": {"extractor": "ViewProjector.visible.circle", "confidence": 0.7},
            })

    return records


def draw_feature_overlay(msp, layout, records, view_data=None):
    drawable_views = set(getattr(layout, "view_sizes", {}).keys())
    panel_records = []
    for rec in records:
        geom_kind = rec.get("geometry", {}).get("kind")
        rec_view = rec.get("view")
        if geom_kind in ("circle", "point") and rec_view not in drawable_views:
            continue
        panel_records.append(rec)

    x = layout.margin + 8
    y = layout.paper_h - layout.margin - 42
    msp.add_text(
        "FEATURE LAYER",
        height=2.8,
        dxfattribs={"layer": "FEATURE", "insert": (x, y), "style": "CHINESE", "color": 4},
    )

    for idx, rec in enumerate(panel_records[:18], start=1):
        line_y = y - idx * 4.2
        text = f"F{idx:02d} [{rec['type']}] {rec['name']} / {rec['role']} / tol:{rec['tolerance_key']}"
        msp.add_text(
            text,
            height=1.8,
            dxfattribs={"layer": "FEATURE", "insert": (x, line_y), "style": "CHINESE", "color": 4},
        )

    if view_data:
        _draw_projected_feature_points(msp, layout, panel_records, view_data)


def _draw_projected_feature_points(msp, layout, records, view_data):
    for idx, rec in enumerate(records[:18], start=1):
        geom = rec.get("geometry", {})
        view_name = rec.get("view")
        if view_name not in getattr(layout, "view_sizes", {}):
            continue
        vd = view_data.get(view_name)
        if not vd:
            continue

        point = None
        radius = 1.4
        if geom.get("kind") == "circle":
            point = geom.get("center")
            radius = max(1.2, min(3.0, geom.get("radius", 1.0) * layout.scale * 0.18))
        elif geom.get("kind") == "point":
            point = geom.get("point")
        if not point:
            continue

        label = f"F{idx:02d}"
        px, py = _project_to_paper(layout, view_name, vd, point)
        msp.add_circle((px, py), radius, dxfattribs={"layer": "FEATURE", "color": 4})
        msp.add_line((px - radius * 1.6, py), (px + radius * 1.6, py), dxfattribs={"layer": "FEATURE", "color": 4})
        msp.add_line((px, py - radius * 1.6), (px, py + radius * 1.6), dxfattribs={"layer": "FEATURE", "color": 4})
        msp.add_text(
            label,
            height=1.6,
            dxfattribs={"layer": "FEATURE", "insert": (px + radius + 1.0, py + radius + 1.0), "style": "CHINESE", "color": 4},
        )


def _project_to_paper(layout, view_name, vd, point):
    ox, oy = layout.get_view_offset(view_name)
    bbox = vd["bbox"]
    scale = layout.scale
    return (
        ox + (point[0] - bbox[0]) * scale,
        oy + (point[1] - bbox[1]) * scale,
    )


def _dist2(p1, p2):
    if not p1 or not p2:
        return 999999.0
    return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5
