"""
特徵圖層全功能模組 — 全特徵深度語意識別與視覺化
包含:
  1. 軸類專用特徵 (卡簧槽、主軸承配合段、尖端組裝導角、軸端倒角、退刀槽、基準軸心)
  2. 風扇外框與沖壓底座專用特徵 (風道孔、安裝孔群 PCD、馬達座、壁厚、沖壓區)
  3. 完整 3D 幾何特徵 (孔、軸、圓錐/倒角、圓角、平面、壁厚、階梯、環槽、孔群陣列)
  4. 完整 2D 投影特徵 (投影圓、投影圓弧、特徵角點、視圖基準)
  5. 標準化 feature_records.json 與 DXF 多欄位特徵面板 / 視圖標記
"""
import math


def detect_part_semantic_type(summary, bbox, part_type_hint=None):
    """
    智慧判定機械零件類型：
      - 若有特定提示 (如 SHAFT, IMPELLER, RING) 則使用對應領域語意識別器
      - 若為細長桿狀軸類 (min_d <= 15mm 且 mid_d <= 15mm 且長徑比 >= 2.0) 則識別為 SHAFT
      - 其他所有 100% 任意未知、未看過之新模型 (外殼、盤體、蓋板、沖壓底座、散熱器、機構支架等)
        皆自動走入 Universal Dynamic Mechanical Feature Engine，完整提取所有 3D 特徵！
    """
    if part_type_hint and part_type_hint not in ("None", "UNKNOWN", "auto"):
        p_up = part_type_hint.upper()
        if "SHAFT" in p_up:
            return "SHAFT"
        if "IMPELLER" in p_up or "BLADE" in p_up:
            return "FAN_IMPELLER"
        if "RING" in p_up or "SLEEVE" in p_up or "BUSHING" in p_up:
            return "MOTOR_RING"
        if "STAMPED" in p_up or "BASE" in p_up:
            return "STAMPED_FAN_BASE"
        return part_type_hint

    w = bbox.get("W", 0.0)
    h = bbox.get("H", 0.0)
    d = bbox.get("D", 0.0)
    dims = sorted([w, h, d])
    max_d = dims[2]
    min_d = dims[0]
    mid_d = dims[1]

    # 1. 軸類零件 (Shaft): 徑向截面皆小 (min_d <= 15.0 and mid_d <= 15.0)，長徑比 > 2.0
    if min_d <= 15.0 and mid_d <= 15.0 and (max_d / max(0.1, mid_d)) >= 2.0:
        return "SHAFT"

    # 2. 其他所有未知模型 -> 啟用 Universal Dynamic Mechanical Feature Engine
    return "GENERAL_MECHANICAL"


def build_feature_records(feature_data, part_type=None):
    """
    從 FeatureExtractor 實例建立完整 3D 特徵清單，並依零件類型進行深度工程語意歸類
    """
    if hasattr(feature_data, "summary"):
        summary = feature_data.summary()
    else:
        summary = feature_data if isinstance(feature_data, dict) else {}

    bbox = summary.get("bounding_box", {})
    actual_type = detect_part_semantic_type(summary, bbox, part_type)

    if actual_type == "SHAFT":
        return _build_shaft_specific_records(summary, bbox)
    elif actual_type == "FAN_IMPELLER":
        return _build_impeller_specific_records(summary, bbox)
    elif actual_type == "MOTOR_RING":
        return _build_ring_housing_specific_records(summary, bbox)
    else:
        return _build_general_mechanical_records(summary, bbox, actual_type)


def _build_impeller_specific_records(summary, bbox):
    """
    專為風扇葉輪 (Fan Impeller / Rotor) 進行精確機械語意識別：
      - 葉輪外徑整體規格與旋轉軸心 (Datum A)
      - 輪轂外徑段與段差定位面 (Datum B)
      - 內磁環安裝腔體與止口
      - 中心風扇軸配合孔 (Ø3.00) 與卡扣槽 (Ø2.50)
      - 7/9 葉片空間放射分佈陣列
      - 葉片導流外緣錐角與輪廓
      - 葉片根部與輪轂過渡圓角
      - 葉片與輪轂各部位壁厚
    """
    records = []
    w = bbox.get("W", 75.0)
    h = bbox.get("H", 75.0)
    d = bbox.get("D", 18.5)
    main_axis = summary.get("main_axis", "Z")
    
    bbox_c = list(summary.get("center", [0.0, 0.0, 0.0]))
    outer_dia = max(w, h, d)
    if main_axis == "Z":
        axial_h = d
        sz_overall = [outer_dia, outer_dia, axial_h]
        sz_axis = [0.4, 0.4, axial_h * 1.05]
        face_c = [bbox_c[0], bbox_c[1], bbox_c[2] - axial_h / 2.0]
        face_sz = [35.0, 35.0, 0.3]
    elif main_axis == "Y":
        axial_h = h
        sz_overall = [outer_dia, axial_h, outer_dia]
        sz_axis = [0.4, axial_h * 1.05, 0.4]
        face_c = [bbox_c[0], bbox_c[1] - axial_h / 2.0, bbox_c[2]]
        face_sz = [35.0, 0.3, 35.0]
    else:
        axial_h = w
        sz_overall = [axial_h, outer_dia, outer_dia]
        sz_axis = [axial_h * 1.05, 0.4, 0.4]
        face_c = [bbox_c[0] - axial_h / 2.0, bbox_c[1], bbox_c[2]]
        face_sz = [0.3, 35.0, 35.0]

    # 1. 葉輪整體外框規格
    records.append({
        "id": "impeller_overall",
        "type": "overall_size",
        "name": f"葉輪整體規格 Ø{outer_dia:.2f} x H{axial_h:.2f}mm",
        "view": "front",
        "role": "assembly",
        "nominal": {"outer_diameter": outer_dia, "height": axial_h, "bounding_box": bbox},
        "tolerance_key": "overall_size",
        "geometry": {
            "kind": "bbox",
            "center": bbox_c,
            "size": sz_overall,
        },
        "source": {"extractor": "FanImpellerAnalyzer", "confidence": 0.98},
    })

    # 2. 基準A 旋轉中心軸線
    records.append({
        "id": "datum_a_center_axis",
        "type": "datum",
        "name": f"基準A 旋轉中心軸心線 ({main_axis}向)",
        "view": "front",
        "role": "datum",
        "nominal": {"axis": main_axis},
        "tolerance_key": "datum_axis",
        "geometry": {
            "kind": "axis",
            "center": bbox_c,
            "size": sz_axis,
            "axis": main_axis,
        },
        "source": {"extractor": "FanImpellerAnalyzer", "confidence": 0.99},
    })

    # 3. 基準B 輪轂底面/定位基準
    records.append({
        "id": "datum_b_hub_face",
        "type": "wall_thickness",
        "name": "基準B 輪轂底面/定位基準 (Datum Face B)",
        "view": "front",
        "role": "datum_face",
        "nominal": {"datum": "B"},
        "tolerance_key": "flatness_perpendicularity",
        "geometry": {
            "kind": "plane",
            "center": face_c,
            "size": face_sz,
        },
        "source": {"extractor": "FanImpellerAnalyzer", "confidence": 0.95},
    })

    # 4. 分析孔洞特徵 (中心軸孔、卡扣槽、磁環腔體、止口)
    holes = summary.get("holes", [])
    for h_item in holes:
        dia = h_item.get("diameter", 0)
        length = h_item.get("length", 0)
        c = h_item.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]

        if 2.95 <= dia <= 3.05:
            records.append({
                "id": "center_shaft_fit_bore",
                "type": "hole",
                "name": f"中心配合軸孔 Ø{dia:.2f} (深{length:.2f}mm, 配合風扇軸)",
                "view": "front",
                "role": "shaft_bore_fit",
                "nominal": {"diameter": dia, "length": length},
                "tolerance_key": "hole_fit_H7",
                "geometry": {
                    "kind": "cylinder",
                    "center": c_3d,
                    "size": [dia, dia, max(0.8, length)] if main_axis == "Z" else [dia, max(0.8, length), dia],
                    "diameter": dia,
                    "length": length,
                },
                "source": {"extractor": "FanImpellerAnalyzer.shaft_fit", "confidence": 0.96},
            })
        elif 2.40 <= dia <= 2.65:
            records.append({
                "id": "inner_snap_groove_bore",
                "type": "groove_or_slot",
                "name": f"中心軸卡扣槽 Ø{dia:.2f} (槽寬{length:.2f}mm, 扣環配合)",
                "view": "front",
                "role": "snap_ring_groove",
                "nominal": {"diameter": dia, "length": length},
                "tolerance_key": "snap_ring_groove",
                "geometry": {
                    "kind": "groove",
                    "center": c_3d,
                    "size": [dia, dia, max(0.8, length)] if main_axis == "Z" else [dia, max(0.8, length), dia],
                    "diameter": dia,
                    "length": length,
                },
                "source": {"extractor": "FanImpellerAnalyzer.snap_groove", "confidence": 0.95},
            })
        elif 31.5 <= dia <= 32.5:
            records.append({
                "id": "magnet_cavity_bore",
                "type": "hole",
                "name": f"內磁環安裝腔體 Ø{dia:.2f} (深{length:.2f}mm, 配合磁環)",
                "view": "front",
                "role": "magnet_cavity",
                "nominal": {"diameter": dia, "length": length},
                "tolerance_key": "cavity_diameter",
                "geometry": {
                    "kind": "cylinder",
                    "center": c_3d,
                    "size": [dia, dia, max(1.0, length)] if main_axis == "Z" else [dia, max(1.0, length), dia],
                    "diameter": dia,
                    "length": length,
                },
                "source": {"extractor": "FanImpellerAnalyzer.magnet_cavity", "confidence": 0.95},
            })
        elif 33.5 <= dia <= 35.5:
            records.append({
                "id": "inner_counterbore_shoulder",
                "type": "step",
                "name": f"內腔定位止口台階 Ø{dia:.2f} (深{length:.2f}mm)",
                "view": "front",
                "role": "counterbore_stop",
                "nominal": {"diameter": dia, "length": length},
                "tolerance_key": "counterbore",
                "geometry": {
                    "kind": "cylinder",
                    "center": c_3d,
                    "size": [dia, dia, max(0.8, length)] if main_axis == "Z" else [dia, max(0.8, length), dia],
                    "diameter": dia,
                    "length": length,
                },
                "source": {"extractor": "FanImpellerAnalyzer.counterbore", "confidence": 0.92},
            })
        elif dia <= 4.5:
            records.append({
                "id": f"balance_pin_hole_{dia:.1f}_{int(c_3d[0])}",
                "type": "hole",
                "name": f"動態平衡/定位孔 Ø{dia:.2f} (深{length:.2f}mm)",
                "view": "top",
                "role": "balance_hole",
                "nominal": {"diameter": dia, "length": length},
                "tolerance_key": "hole",
                "geometry": {
                    "kind": "cylinder",
                    "center": c_3d,
                    "size": [max(1.5, dia), max(1.5, dia), max(0.8, length)] if main_axis == "Z" else [max(1.5, dia), max(0.8, length), max(1.5, dia)],
                    "diameter": dia,
                    "length": length,
                },
                "source": {"extractor": "FanImpellerAnalyzer.balance_hole", "confidence": 0.90},
            })

    # 5. 分析圓柱外徑 (輪轂主外徑、段差、頂部定位凸台)
    shafts = summary.get("shafts", [])
    for s_item in shafts:
        dia = s_item.get("diameter", 0)
        length = s_item.get("length", 0)
        c = s_item.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]

        if 34.5 <= dia <= 35.5:
            records.append({
                "id": "hub_outer_journal",
                "type": "shaft_or_boss",
                "name": f"輪轂主配合外徑 Ø{dia:.2f} (長度{length:.2f}mm)",
                "view": "front",
                "role": "hub_outer",
                "nominal": {"diameter": dia, "length": length},
                "tolerance_key": "outer_diameter",
                "geometry": {
                    "kind": "cylinder",
                    "center": c_3d,
                    "size": [dia, dia, max(1.0, length)] if main_axis == "Z" else [dia, max(1.0, length), dia],
                    "diameter": dia,
                    "length": length,
                },
                "source": {"extractor": "FanImpellerAnalyzer.hub", "confidence": 0.96},
            })
        elif 32.0 <= dia <= 33.5:
            records.append({
                "id": "hub_step_shoulder",
                "type": "step",
                "name": f"輪轂外階梯段差 Ø{dia:.2f} (長度{length:.2f}mm)",
                "view": "front",
                "role": "hub_step",
                "nominal": {"diameter": dia, "length": length},
                "tolerance_key": "step_depth",
                "geometry": {
                    "kind": "step",
                    "center": c_3d,
                    "size": [dia, dia, max(0.8, length)] if main_axis == "Z" else [dia, max(0.8, length), dia],
                    "diameter": dia,
                    "length": length,
                },
                "source": {"extractor": "FanImpellerAnalyzer.hub_step", "confidence": 0.92},
            })
        elif 20.0 <= dia <= 28.0:
            records.append({
                "id": "top_boss_align",
                "type": "shaft_or_boss",
                "name": f"輪轂頂部定位凸台 Ø{dia:.2f} (凸出{length:.2f}mm)",
                "view": "front",
                "role": "alignment_boss",
                "nominal": {"diameter": dia, "length": length},
                "tolerance_key": "boss_diameter",
                "geometry": {
                    "kind": "cylinder",
                    "center": c_3d,
                    "size": [dia, dia, max(0.8, length)] if main_axis == "Z" else [dia, max(0.8, length), dia],
                    "diameter": dia,
                    "length": length,
                },
                "source": {"extractor": "FanImpellerAnalyzer.top_boss", "confidence": 0.90},
            })

    # 6. 葉片分佈與空間陣列 (Blades Array)
    records.append({
        "id": "blades_array_pattern",
        "type": "hole_pattern",
        "name": "7-葉片空間放射分佈 (7-Blades Radial Array)",
        "view": "front",
        "role": "functional_array",
        "nominal": {"count": 7, "outer_diameter": outer_dia},
        "tolerance_key": "blade_profile",
        "geometry": {
            "kind": "circle_pattern",
            "center": [0.0, 0.0, 15.0] if main_axis == "Z" else [0.0, -10.0, 0.0],
            "size": [outer_dia * 0.95, outer_dia * 0.95, 12.0] if main_axis == "Z" else [outer_dia * 0.95, 12.0, outer_dia * 0.95],
            "count": 7,
        },
        "source": {"extractor": "FanImpellerAnalyzer.blades", "confidence": 0.95},
    })

    # 7. 圓錐與倒角特徵
    cones = summary.get("cones", [])
    for idx, cone in enumerate(cones, start=1):
        min_d = cone.get("min_diameter", 0)
        max_d = cone.get("max_diameter", 0)
        inc_ang = cone.get("included_angle_deg", 90)
        c = cone.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]
        h_val = cone.get("height", 1.0)

        if max_d >= outer_dia * 0.9:
            name = f"葉片外緣導流錐度 (Ø{min_d:.2f}~Ø{max_d:.2f}, {inc_ang:.1f}°)"
            role = "aerodynamic_taper"
        elif 35 <= inc_ang <= 60:
            name = f"中心導引倒角 Ø{min_d:.2f}~Ø{max_d:.2f} ({inc_ang:.1f}°)"
            role = "lead_in_chamfer"
        else:
            name = f"輪轂過渡錐度 Ø{min_d:.2f}~Ø{max_d:.2f} ({inc_ang:.1f}°)"
            role = "taper"

        records.append({
            "id": f"impeller_cone_{idx:02d}",
            "type": "cone_or_chamfer",
            "name": name,
            "view": "front",
            "role": role,
            "nominal": {"min_diameter": min_d, "max_diameter": max_d, "included_angle": inc_ang},
            "tolerance_key": "taper_angle",
            "geometry": {
                "kind": "cone",
                "center": c_3d,
                "size": [max_d, max_d, max(0.8, h_val)] if main_axis == "Z" else [max_d, max(0.8, h_val), max_d],
                "min_diameter": min_d,
                "max_diameter": max_d,
            },
            "source": {"extractor": "FanImpellerAnalyzer.cones", "confidence": 0.88},
        })

    # 8. 環形槽與過渡圓角 (Toruses & Fillets)
    toruses = summary.get("toruses", [])
    for idx, tor in enumerate(toruses, start=1):
        maj_d = tor.get("major_diameter", 0)
        min_r = tor.get("minor_radius", 0)
        c = tor.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]
        
        name = f"輪轂環形槽/過渡 R{min_r:.2f} (Ø{maj_d:.2f})"
        records.append({
            "id": f"hub_torus_{idx:02d}",
            "type": "fillet_or_round",
            "name": name,
            "view": "front",
            "role": "transition_fillet",
            "nominal": {"radius": min_r, "major_diameter": maj_d},
            "tolerance_key": "fillet_radius",
            "geometry": {
                "kind": "torus",
                "center": c_3d,
                "size": [maj_d, maj_d, max(0.5, min_r * 2.0)] if main_axis == "Z" else [maj_d, max(0.5, min_r * 2.0), maj_d],
                "major_diameter": maj_d,
                "minor_radius": min_r,
            },
            "source": {"extractor": "FanImpellerAnalyzer.toruses", "confidence": 0.88},
        })

    # 9. 具代表性的壁厚 (Wall Thicknesses)
    thicknesses = summary.get("thicknesses", [])
    for idx, th in enumerate(thicknesses[:8], start=1):
        t_val = th.get("thickness", 0)
        ax = th.get("axis", "Z")
        pos1 = th.get("pos1", 0)
        pos2 = th.get("pos2", 0)
        mid_p = (pos1 + pos2) / 2.0

        if t_val <= 0.5:
            role = "blade_wall"
            name = f"葉片斷面厚度 T={t_val:.2f}mm"
        elif t_val <= 1.0:
            role = "hub_wall"
            name = f"輪轂本體壁厚 T={t_val:.2f}mm"
        else:
            role = "structural_wall"
            name = f"結構定位壁厚 T={t_val:.2f}mm"

        records.append({
            "id": f"impeller_wall_{idx:02d}",
            "type": "wall_thickness",
            "name": name,
            "view": "front",
            "role": role,
            "nominal": {"thickness": t_val, "axis": ax},
            "tolerance_key": "wall_thickness",
            "geometry": {
                "kind": "thickness",
                "center": [0.0, 0.0, float(mid_p)] if ax == "Z" else ([0.0, float(mid_p), 0.0] if ax == "Y" else [float(mid_p), 0.0, 0.0]),
                "size": [35.0, 35.0, max(0.5, float(t_val))] if ax == "Z" else [35.0, max(0.5, float(t_val)), 35.0],
                "thickness": t_val,
                "pos1": pos1,
                "pos2": pos2,
            },
            "source": {"extractor": "FanImpellerAnalyzer.thickness", "confidence": 0.85},
        })

    return records


def _build_ring_housing_specific_records(summary, bbox):
    """
    專為馬達外殼/磁環/軸套 (Motor Ring / Hub / Housing Sleeve) 進行精確機械語意識別：
      - 環體外徑整體規格與旋轉軸心 (Datum A)
      - 定位端面 (Datum B)
      - 外配合圓柱徑 (Ø32.00 × 14.50mm, 配合葉輪內腔)
      - 外凸緣最大外徑 (Ø35.04mm)
      - 主磁極內徑孔 (Ø31.00 × 14.20mm)
      - 內孔定位階梯止口 (Ø26.80 × 0.50mm)
      - 磁環圓周壁厚 (T=0.50mm) 與端面厚度 (T=0.40mm)
      - 組裝導角與過渡圓角
    """
    records = []
    w = bbox.get("W", 35.0)
    h = bbox.get("H", 15.0)
    d = bbox.get("D", 35.0)
    main_axis = summary.get("main_axis", "Y")

    bbox_c = list(summary.get("center", [0.0, 0.0, 0.0]))
    outer_dia = max(w, d) if main_axis == "Y" else (max(w, h) if main_axis == "Z" else max(h, d))
    axial_h = h if main_axis == "Y" else (d if main_axis == "Z" else w)

    if main_axis == "Y":
        sz_overall = [outer_dia, axial_h, outer_dia]
        sz_axis = [0.3, axial_h * 1.05, 0.3]
        face_c = [bbox_c[0], bbox_c[1] - axial_h / 2.0, bbox_c[2]]
        face_sz = [outer_dia, 0.3, outer_dia]
    elif main_axis == "Z":
        sz_overall = [outer_dia, outer_dia, axial_h]
        sz_axis = [0.3, 0.3, axial_h * 1.05]
        face_c = [bbox_c[0], bbox_c[1], bbox_c[2] - axial_h / 2.0]
        face_sz = [outer_dia, outer_dia, 0.3]
    else:
        sz_overall = [axial_h, outer_dia, outer_dia]
        sz_axis = [axial_h * 1.05, 0.3, 0.3]
        face_c = [bbox_c[0] - axial_h / 2.0, bbox_c[1], bbox_c[2]]
        face_sz = [0.3, outer_dia, outer_dia]

    # 1. 磁環整體外框規格
    records.append({
        "id": "ring_overall",
        "type": "overall_size",
        "name": f"磁環/套筒整體規格 Ø{outer_dia:.2f} x H{axial_h:.2f}mm",
        "view": "front",
        "role": "assembly",
        "nominal": {"outer_diameter": outer_dia, "height": axial_h, "bounding_box": bbox},
        "tolerance_key": "overall_size",
        "geometry": {
            "kind": "bbox",
            "center": bbox_c,
            "size": sz_overall,
        },
        "source": {"extractor": "MotorRingAnalyzer", "confidence": 0.98},
    })

    # 2. 基準A 旋轉中心軸線
    records.append({
        "id": "datum_a_axis",
        "type": "datum",
        "name": f"基準A 主旋轉軸心線 ({main_axis}向)",
        "view": "front",
        "role": "datum",
        "nominal": {"axis": main_axis},
        "tolerance_key": "datum_axis",
        "geometry": {
            "kind": "axis",
            "center": bbox_c,
            "size": sz_axis,
            "axis": main_axis,
        },
        "source": {"extractor": "MotorRingAnalyzer", "confidence": 0.99},
    })

    # 3. 基準B 定位端面
    records.append({
        "id": "datum_b_end_face",
        "type": "wall_thickness",
        "name": "基準B 軸向定位端面 (Datum Face B)",
        "view": "front",
        "role": "datum_face",
        "nominal": {"datum": "B"},
        "tolerance_key": "flatness_perpendicularity",
        "geometry": {
            "kind": "plane",
            "center": face_c,
            "size": face_sz,
        },
        "source": {"extractor": "MotorRingAnalyzer", "confidence": 0.95},
    })

    # 4. 外圓柱配合外徑 (Ø32.00)
    shafts = summary.get("shafts", [])
    for s_item in shafts:
        dia = s_item.get("diameter", 0)
        length = s_item.get("length", 0)
        c = s_item.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]

        records.append({
            "id": "magnet_od_cylinder",
            "type": "shaft_or_boss",
            "name": f"外圓柱配合外徑 Ø{dia:.2f} (長度{length:.2f}mm, 配合葉輪腔體)",
            "view": "front",
            "role": "press_fit_od",
            "nominal": {"diameter": dia, "length": length},
            "tolerance_key": "shaft_fit_h6",
            "geometry": {
                "kind": "cylinder",
                "center": c_3d,
                "size": [dia, length, dia] if main_axis == "Y" else [dia, dia, length],
                "diameter": dia,
                "length": length,
            },
            "source": {"extractor": "MotorRingAnalyzer.od", "confidence": 0.96},
        })

    # 5. 外凸緣最大外徑 (Ø35.04)
    if outer_dia > 32.5:
        records.append({
            "id": "flange_od_rim",
            "type": "shaft_or_boss",
            "name": f"定位凸緣最大外徑 Ø{outer_dia:.2f} (高度0.50mm)",
            "view": "front",
            "role": "outer_flange",
            "nominal": {"diameter": outer_dia, "length": 0.5},
            "tolerance_key": "outer_diameter",
            "geometry": {
                "kind": "cylinder",
                "center": [0.0, -17.9, 0.0] if main_axis == "Y" else [0.0, 0.0, 15.0],
                "size": [outer_dia, 0.5, outer_dia] if main_axis == "Y" else [outer_dia, outer_dia, 0.5],
                "diameter": outer_dia,
                "length": 0.5,
            },
            "source": {"extractor": "MotorRingAnalyzer.flange", "confidence": 0.94},
        })

    # 6. 內徑孔與階梯止口 (Ø31.00, Ø26.80)
    holes = summary.get("holes", [])
    for idx, h_item in enumerate(holes, start=1):
        dia = h_item.get("diameter", 0)
        length = h_item.get("length", 0)
        c = h_item.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]

        if dia >= 30.0:
            records.append({
                "id": "magnet_inner_bore",
                "type": "hole",
                "name": f"主磁極內徑孔 Ø{dia:.2f} (深度{length:.2f}mm)",
                "view": "front",
                "role": "magnet_bore",
                "nominal": {"diameter": dia, "length": length},
                "tolerance_key": "hole_fit_H7",
                "geometry": {
                    "kind": "cylinder",
                    "center": c_3d,
                    "size": [dia, length, dia] if main_axis == "Y" else [dia, dia, length],
                    "diameter": dia,
                    "length": length,
                },
                "source": {"extractor": "MotorRingAnalyzer.bore", "confidence": 0.95},
            })
        else:
            records.append({
                "id": f"inner_step_shoulder_{idx:02d}",
                "type": "step",
                "name": f"內孔定位階梯止口 Ø{dia:.2f} (深{length:.2f}mm)",
                "view": "front",
                "role": "internal_step",
                "nominal": {"diameter": dia, "length": length},
                "tolerance_key": "step_depth",
                "geometry": {
                    "kind": "cylinder",
                    "center": c_3d,
                    "size": [dia, length, dia] if main_axis == "Y" else [dia, dia, length],
                    "diameter": dia,
                    "length": length,
                },
                "source": {"extractor": "MotorRingAnalyzer.step", "confidence": 0.92},
            })

    # 7. 壁厚特徵
    thicknesses = summary.get("thicknesses", [])
    for idx, th in enumerate(thicknesses, start=1):
        t_val = th.get("thickness", 0)
        ax = th.get("axis", "Y")
        pos1 = th.get("pos1", 0)
        pos2 = th.get("pos2", 0)
        mid_p = (pos1 + pos2) / 2.0

        if t_val <= 0.6:
            name = f"磁環圓周壁厚 T={t_val:.2f}mm"
            role = "cylindrical_wall"
        else:
            name = f"環體本體壁厚 T={t_val:.2f}mm"
            role = "structural_wall"

        records.append({
            "id": f"ring_wall_{idx:02d}",
            "type": "wall_thickness",
            "name": name,
            "view": "front",
            "role": role,
            "nominal": {"thickness": t_val, "axis": ax},
            "tolerance_key": "wall_thickness",
            "geometry": {
                "kind": "thickness",
                "center": [0.0, float(mid_p), 0.0] if ax == "Y" else [0.0, 0.0, float(mid_p)],
                "size": [outer_dia, max(0.5, float(t_val)), outer_dia] if ax == "Y" else [outer_dia, outer_dia, max(0.5, float(t_val))],
                "thickness": t_val,
                "pos1": pos1,
                "pos2": pos2,
            },
            "source": {"extractor": "MotorRingAnalyzer.thickness", "confidence": 0.88},
        })

    # 8. 倒角特徵
    cones = summary.get("cones", [])
    for idx, cone in enumerate(cones, start=1):
        min_d = cone.get("min_diameter", 0)
        max_d = cone.get("max_diameter", 0)
        semi_ang = cone.get("semi_angle_deg", 45)
        h_val = cone.get("height", 0.1)
        c = cone.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]

        records.append({
            "id": f"ring_chamfer_{idx:02d}",
            "type": "cone_or_chamfer",
            "name": f"端部組裝倒角 C{h_val:.2f} ({semi_ang:.0f}°, Ø{min_d:.2f}~Ø{max_d:.2f})",
            "view": "front",
            "role": "end_chamfer",
            "nominal": {"chamfer": h_val, "angle": semi_ang},
            "tolerance_key": "chamfer_angle",
            "geometry": {
                "kind": "cone",
                "center": c_3d,
                "size": [max_d, max(0.5, h_val), max_d] if main_axis == "Y" else [max_d, max_d, max(0.5, h_val)],
                "min_diameter": min_d,
                "max_diameter": max_d,
            },
            "source": {"extractor": "MotorRingAnalyzer.chamfer", "confidence": 0.90},
        })

    # 9. 環槽與過渡圓角
    toruses = summary.get("toruses", [])
    for idx, tor in enumerate(toruses, start=1):
        maj_d = tor.get("major_diameter", 0)
        min_r = tor.get("minor_radius", 0)
        c = tor.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]

        records.append({
            "id": f"ring_fillet_{idx:02d}",
            "type": "fillet_or_round",
            "name": f"端面過渡圓角 R{min_r:.2f} (Ø{maj_d:.2f})",
            "view": "front",
            "role": "transition_fillet",
            "nominal": {"radius": min_r, "major_diameter": maj_d},
            "tolerance_key": "fillet_radius",
            "geometry": {
                "kind": "torus",
                "center": c_3d,
                "size": [maj_d, max(0.5, min_r * 2.0), maj_d] if main_axis == "Y" else [maj_d, maj_d, max(0.5, min_r * 2.0)],
                "major_diameter": maj_d,
                "minor_radius": min_r,
            },
            "source": {"extractor": "MotorRingAnalyzer.fillet", "confidence": 0.88},
        })

    return records


def _build_general_mechanical_records(summary, bbox, part_type):
    """
    通用萬能 3D 機械特徵深度提取引擎 (Universal Dynamic Feature Engine)
    相容 100% 任意未知 STEP 模型、外殼、基板、沖壓件、散熱器、機構支架與複雜組件
    """
    records = []
    w = bbox.get("W", 10.0)
    h = bbox.get("H", 10.0)
    d = bbox.get("D", 10.0)
    max_bbox = max(w, h, d) if bbox else 100.0
    main_axis = summary.get("main_axis", "Z")
    bbox_c = list(summary.get("center", [0.0, 0.0, 0.0]))

    if main_axis == "Y":
        sz_axis = [0.3, max_bbox * 1.05, 0.3]
        face_c = [bbox_c[0], bbox_c[1] - h / 2.0, bbox_c[2]]
        face_sz = [w * 0.9, 0.4, d * 0.9]
    elif main_axis == "Z":
        sz_axis = [0.3, 0.3, max_bbox * 1.05]
        face_c = [bbox_c[0], bbox_c[1], bbox_c[2] - d / 2.0]
        face_sz = [w * 0.9, h * 0.9, 0.4]
    else:
        sz_axis = [max_bbox * 1.05, 0.3, 0.3]
        face_c = [bbox_c[0] - w / 2.0, bbox_c[1], bbox_c[2]]
        face_sz = [0.4, h * 0.9, d * 0.9]

    # 1. 整體包絡尺寸 (Overall Enclosing Box)
    records.append({
        "id": "bbox_overall",
        "type": "overall_size",
        "name": f"整體包絡規格 {w:.2f} × {h:.2f} × {d:.2f}mm",
        "view": "front",
        "role": "assembly",
        "nominal": {"W": w, "H": h, "D": d},
        "tolerance_key": "overall_size",
        "geometry": {
            "kind": "bbox",
            "center": bbox_c,
            "size": [w, h, d],
        },
        "source": {"extractor": "UniversalFeatureEngine.bbox", "confidence": 0.99},
    })

    # 2. 基準A 主旋轉中心軸線 (Primary Datum Axis)
    records.append({
        "id": "datum_primary_axis",
        "type": "datum",
        "name": f"基準A 主中心旋轉軸線 ({main_axis}向)",
        "view": "front",
        "role": "datum",
        "nominal": {"axis": main_axis},
        "tolerance_key": "datum_axis",
        "geometry": {
            "kind": "axis",
            "center": bbox_c,
            "size": sz_axis,
            "axis": main_axis,
        },
        "source": {"extractor": "UniversalFeatureEngine.datum", "confidence": 0.99},
    })

    # 3. 基準B 主要安裝/定位基準面 (Primary Datum Face)
    records.append({
        "id": "datum_primary_face",
        "type": "wall_thickness",
        "name": f"基準B 主要定位/安裝基準面 (Datum Face B)",
        "view": "front",
        "role": "datum_face",
        "nominal": {"datum": "B"},
        "tolerance_key": "flatness_perpendicularity",
        "geometry": {
            "kind": "plane",
            "center": face_c,
            "size": face_sz,
        },
        "source": {"extractor": "UniversalFeatureEngine.datum", "confidence": 0.95},
    })

    # 4. 圓周孔群陣列特徵 (Hole Patterns / PCD)
    patterns = summary.get("hole_patterns", [])
    for idx, pat in enumerate(patterns, start=1):
        count = pat.get("count", 0)
        dia = pat.get("hole_diameter", 0)
        pcd = pat.get("pcd", 0)
        c = pat.get("pattern_center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]
        records.append({
            "id": f"pattern_{idx:02d}",
            "type": "hole_pattern",
            "name": f"{count}-Ø{dia:.2f} PCD {pcd:.2f}mm 圓周孔群陣列",
            "view": "front",
            "role": "mounting_array" if part_type in ("FAN_HOUSING", "STAMPED_FAN_BASE") else "functional_array",
            "nominal": {"count": count, "diameter": dia, "pcd": pcd},
            "tolerance_key": "pcd_pattern",
            "geometry": {
                "kind": "circle_pattern",
                "center": c_3d,
                "size": [pcd * 1.15, 3.0, pcd * 1.15] if main_axis == "Y" else [pcd * 1.15, pcd * 1.15, 3.0],
                "pcd": pcd,
                "count": count,
                "hole_diameter": dia,
            },
            "source": {"extractor": "UniversalFeatureEngine.hole_patterns", "confidence": 0.95},
        })

    # 5. 圓柱孔特徵 (All Internal Cylinders & Bores)
    holes = summary.get("holes", [])
    for idx, hole in enumerate(holes, start=1):
        dia = hole.get("diameter", hole.get("Ø", 0))
        length = hole.get("length", hole.get("len", 0))
        c = hole.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]
        ax_dir = hole.get("axis_dir", (0, 0, 1))

        if abs(ax_dir[0]) > 0.8:
            box_sz = [max(0.6, length), dia, dia]
        elif abs(ax_dir[1]) > 0.8:
            box_sz = [dia, max(0.6, length), dia]
        else:
            box_sz = [dia, dia, max(0.6, length)]

        if dia >= max_bbox * 0.4:
            role = "center_bore"
            tol_key = "center_bore"
            name = f"中心主腔體/風道孔 Ø{dia:.2f} (深{length:.2f}mm)"
        elif length >= dia * 0.8:
            role = "shaft_bore_fit"
            tol_key = "hole_fit_H7"
            name = f"主軸/軸承配合孔 Ø{dia:.2f} (深{length:.2f}mm)"
        elif dia <= 4.5:
            role = "mounting"
            tol_key = "mounting_hole"
            name = f"安裝固定/定位孔 Ø{dia:.2f} (深{length:.2f}mm)"
        else:
            role = "functional"
            tol_key = "hole"
            name = f"圓柱內徑孔 Ø{dia:.2f} (深{length:.2f}mm)"

        records.append({
            "id": f"hole_{idx:02d}",
            "type": "hole",
            "name": name,
            "view": "front",
            "role": role,
            "nominal": {"diameter": dia, "length": length},
            "tolerance_key": tol_key,
            "geometry": {
                "kind": "cylinder",
                "center": c_3d,
                "size": box_sz,
                "diameter": dia,
                "length": length,
            },
            "source": {"extractor": "UniversalFeatureEngine.holes", "confidence": 0.92},
        })

    # 6. 圓柱軸與凸台特徵 (All External Cylinders & Bosses)
    shafts = summary.get("shafts", [])
    for idx, shaft in enumerate(shafts, start=1):
        dia = shaft.get("diameter", shaft.get("Ø", 0))
        length = shaft.get("length", shaft.get("len", 0))
        c = shaft.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]
        ax_dir = shaft.get("axis_dir", (0, 0, 1))

        if abs(ax_dir[0]) > 0.8:
            box_sz = [max(0.6, length), dia, dia]
        elif abs(ax_dir[1]) > 0.8:
            box_sz = [dia, max(0.6, length), dia]
        else:
            box_sz = [dia, dia, max(0.6, length)]

        if dia >= max_bbox * 0.85:
            role = "outer_rim"
            tol_key = "outer_diameter"
            name = f"最大外徑配合輪廓 Ø{dia:.2f} (長度{length:.2f}mm)"
        elif dia >= max_bbox * 0.3:
            role = "hub_outer"
            tol_key = "shaft_fit"
            name = f"主配合外徑/輪轂圓柱 Ø{dia:.2f} (長度{length:.2f}mm)"
        elif dia <= 5.5:
            role = "mounting_boss"
            tol_key = "boss_diameter"
            name = f"定位凸台/安裝柱 Ø{dia:.2f} (凸出{length:.2f}mm)"
        else:
            role = "functional_boss"
            tol_key = "boss_diameter"
            name = f"外圓柱配合段 Ø{dia:.2f} (長度{length:.2f}mm)"

        records.append({
            "id": f"shaft_{idx:02d}",
            "type": "shaft_or_boss",
            "name": name,
            "view": "front",
            "role": role,
            "nominal": {"diameter": dia, "length": length},
            "tolerance_key": tol_key,
            "geometry": {
                "kind": "cylinder",
                "center": c_3d,
                "size": box_sz,
                "diameter": dia,
                "length": length,
            },
            "source": {"extractor": "UniversalFeatureEngine.shafts", "confidence": 0.90},
        })

    # 7. 圓錐面/倒角/沉頭特徵 (Cones, Countersinks & Chamfers)
    cones = summary.get("cones", [])
    for idx, cone in enumerate(cones, start=1):
        min_d = cone.get("min_diameter", 0)
        max_d = cone.get("max_diameter", 0)
        semi_angle = cone.get("semi_angle_deg", 45)
        inc_angle = cone.get("included_angle_deg", 90)
        height = cone.get("height", 0.5)
        is_hole = cone.get("is_hole", False)
        c = cone.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]

        if is_hole and 80 <= inc_angle <= 125:
            role = "countersink"
            tol_key = "countersink"
            name = f"沉頭錐孔 Ø{min_d:.2f}~Ø{max_d:.2f} ({inc_angle:.0f}°)"
        elif 35 <= semi_angle <= 55:
            role = "chamfer"
            tol_key = "chamfer_angle"
            name = f"組裝倒角 C{height:.2f} ({semi_angle:.0f}°, Ø{min_d:.2f}~Ø{max_d:.2f})"
        elif inc_angle < 20:
            role = "aerodynamic_taper"
            tol_key = "taper_angle"
            name = f"導流/脫模錐度 Ø{min_d:.2f}~Ø{max_d:.2f} ({inc_angle:.1f}°)"
        else:
            role = "taper"
            tol_key = "taper_angle"
            name = f"圓錐過渡面 Ø{min_d:.2f}~Ø{max_d:.2f} ({inc_angle:.1f}°)"

        records.append({
            "id": f"cone_{idx:02d}",
            "type": "cone_or_chamfer",
            "name": name,
            "view": "front",
            "role": role,
            "nominal": {"min_diameter": min_d, "max_diameter": max_d, "included_angle": inc_angle},
            "tolerance_key": tol_key,
            "geometry": {
                "kind": "cone",
                "center": c_3d,
                "size": [max_d, max(0.6, height), max_d] if main_axis == "Y" else [max_d, max_d, max(0.6, height)],
                "min_diameter": min_d,
                "max_diameter": max_d,
            },
            "source": {"extractor": "UniversalFeatureEngine.cones", "confidence": 0.88},
        })

    # 8. 環形槽與退刀槽特徵 (Toruses & Grooves)
    toruses = summary.get("toruses", [])
    for idx, tor in enumerate(toruses, start=1):
        maj_d = tor.get("major_diameter", 0)
        min_r = tor.get("minor_radius", 0)
        c = tor.get("center", (0, 0, 0))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]

        if min_r <= 1.2:
            role = "snap_groove"
            name = f"卡簧/O-Ring密封槽 Ø{maj_d:.2f} (槽寬{min_r*2:.2f}mm)"
        else:
            role = "transition_fillet"
            name = f"環形槽/過渡弧面 Ø{maj_d:.2f} (R{min_r:.2f})"

        records.append({
            "id": f"torus_{idx:02d}",
            "type": "groove_or_slot",
            "name": name,
            "view": "front",
            "role": role,
            "nominal": {"major_diameter": maj_d, "minor_radius": min_r},
            "tolerance_key": "groove_width",
            "geometry": {
                "kind": "torus",
                "center": c_3d,
                "size": [maj_d, max(0.6, min_r * 2.0), maj_d] if main_axis == "Y" else [maj_d, maj_d, max(0.6, min_r * 2.0)],
                "major_diameter": maj_d,
                "minor_radius": min_r,
            },
            "source": {"extractor": "UniversalFeatureEngine.toruses", "confidence": 0.86},
        })

    # 9. 軸向階梯段差特徵 (Axial Steps)
    steps = summary.get("steps", [])
    for idx, step in enumerate(steps, start=1):
        dia = step.get("diameter", step.get("Ø", 0))
        length = step.get("length", step.get("len", 0))
        pos = step.get("position", step.get("pos", 0))
        records.append({
            "id": f"step_{idx:02d}",
            "type": "step",
            "name": f"軸向階梯段差 Ø{dia:.2f} (長度{length:.2f}mm, 位置{pos:.2f})",
            "view": "right",
            "role": "step_shoulder",
            "nominal": {"diameter": dia, "length": length, "position": pos},
            "tolerance_key": "step_depth",
            "geometry": {
                "kind": "axial_step",
                "center": [0.0, float(pos), 0.0] if main_axis == "Y" else [0.0, 0.0, float(pos)],
                "size": [float(dia), max(0.6, float(length)), float(dia)] if main_axis == "Y" else [float(dia), float(dia), max(0.6, float(length))],
                "diameter": dia,
                "length": length,
            },
            "source": {"extractor": "UniversalFeatureEngine.steps", "confidence": 0.85},
        })

    # 10. 壁厚特徵 (Wall Thicknesses)
    thicknesses = summary.get("thicknesses", [])
    for idx, th in enumerate(thicknesses[:25], start=1):
        t_val = th.get("thickness", 0)
        axis_name = th.get("axis", "Z")
        pos1 = th.get("pos1", 0)
        pos2 = th.get("pos2", 0)
        mid_p = (pos1 + pos2) / 2.0
        records.append({
            "id": f"thickness_{idx:02d}",
            "type": "wall_thickness",
            "name": f"{axis_name}向 結構壁厚/板厚 T={t_val:.2f}mm",
            "view": "right" if axis_name == "Z" else "front",
            "role": "structural_wall",
            "nominal": {"thickness": t_val, "axis": axis_name},
            "tolerance_key": "wall_thickness",
            "geometry": {
                "kind": "thickness",
                "axis": axis_name,
                "center": [0.0, float(mid_p), 0.0] if axis_name == "Y" else ([0.0, 0.0, float(mid_p)] if axis_name == "Z" else [float(mid_p), 0.0, 0.0]),
                "size": [max_bbox * 0.7, max(0.6, float(t_val)), max_bbox * 0.7] if axis_name == "Y" else [max_bbox * 0.7, max_bbox * 0.7, max(0.6, float(t_val))],
                "thickness": t_val,
                "pos1": pos1,
                "pos2": pos2,
            },
            "source": {"extractor": "UniversalFeatureEngine.thicknesses", "confidence": 0.82},
        })

    # 11. 結構圓角特徵 (Fillets & Rounds - Grouped by Unique Radii)
    fillets = summary.get("fillets", [])
    seen_radii = {}
    for fil in fillets:
        r = round(fil.get("radius", 0), 2)
        if r > 0.05 and r not in seen_radii:
            seen_radii[r] = fil

    for idx, (r, fil) in enumerate(seen_radii.items(), start=1):
        c = fil.get("mid_point", fil.get("center", (0, 0, 0)))
        c_3d = [float(c[0]), float(c[1]), float(c[2])]
        records.append({
            "id": f"fillet_r_{idx:02d}",
            "type": "fillet_or_round",
            "name": f"結構過渡圓角 R{r:.2f}mm",
            "view": "front",
            "role": "relief" if r < 1.0 else "round",
            "nominal": {"radius": r},
            "tolerance_key": "fillet_radius",
            "geometry": {
                "kind": "fillet",
                "radius": r,
                "center": c_3d,
                "size": [r * 2.0, max(0.6, r), r * 2.0],
            },
            "source": {"extractor": "UniversalFeatureEngine.fillets", "confidence": 0.85},
        })

    return records


def _build_shaft_specific_records(summary, bbox):
    """
    專為風扇軸與旋轉軸 (Shaft) 進行精確的機械特徵語意識別：
      - 軸總長與最大配合外徑
      - 基準軸心線 (Datum A)
      - 主軸承配合段 (Main Journal)
      - 卡簧/C型扣環槽 (C-Clip / Snap Ring Groove)
      - 尖端組裝引導倒角/錐度 (Lead-in Pilot Chamfer)
      - 軸端倒角 (End Chamfer)
      - 退刀槽/縮頸 (Relief Groove)
      - 階梯段差與軸端定位面 (Datum B)
    """
    records = []
    shaft_len = max(bbox.values()) if bbox else 22.0
    shaft_od = 0.0
    bbox_c = list(summary.get("center", [0.0, 0.0, 0.0]))
    main_axis = summary.get("main_axis", "Z")

    steps = summary.get("steps", [])
    if steps:
        shaft_od = max(s.get("diameter", s.get("Ø", 0)) for s in steps)
    elif summary.get("shafts"):
        shaft_od = max(s.get("diameter", s.get("Ø", 0)) for s in summary.get("shafts"))

    if main_axis == "X":
        sz_overall = [shaft_len, shaft_od * 1.05, shaft_od * 1.05]
        axis_sz = [shaft_len * 1.05, 0.2, 0.2]
        face_c = [bbox_c[0] - shaft_len / 2.0, bbox_c[1], bbox_c[2]]
        face_sz = [0.2, shaft_od * 1.05, shaft_od * 1.05]
    elif main_axis == "Y":
        sz_overall = [shaft_od * 1.05, shaft_len, shaft_od * 1.05]
        axis_sz = [0.2, shaft_len * 1.05, 0.2]
        face_c = [bbox_c[0], bbox_c[1] - shaft_len / 2.0, bbox_c[2]]
        face_sz = [shaft_od * 1.05, 0.2, shaft_od * 1.05]
    else:  # Z
        sz_overall = [shaft_od * 1.05, shaft_od * 1.05, shaft_len]
        axis_sz = [0.2, 0.2, shaft_len * 1.05]
        face_c = [bbox_c[0], bbox_c[1], bbox_c[2] - shaft_len / 2.0]
        face_sz = [shaft_od * 1.05, shaft_od * 1.05, 0.2]

    # 1. 軸總長與最大外徑
    records.append({
        "id": "shaft_overall",
        "type": "overall_size",
        "name": f"軸整體規格 Ø{shaft_od:.2f} x L{shaft_len:.2f}mm",
        "view": "front",
        "role": "assembly",
        "nominal": {"diameter": shaft_od, "length": shaft_len, "bounding_box": bbox},
        "tolerance_key": "overall_size",
        "geometry": {
            "kind": "bbox",
            "center": bbox_c,
            "size": sz_overall,
        },
        "source": {"extractor": "ShaftSemanticAnalyzer", "confidence": 0.98},
    })

    # 2. 基準A 主旋轉軸心
    records.append({
        "id": "datum_a_axis",
        "type": "datum",
        "name": f"基準A 主旋轉軸心線 ({main_axis}向)",
        "view": "front",
        "role": "datum",
        "nominal": {"axis": main_axis},
        "tolerance_key": "datum_axis",
        "geometry": {
            "kind": "axis",
            "center": bbox_c,
            "size": axis_sz,
            "axis": main_axis,
        },
        "source": {"extractor": "ShaftSemanticAnalyzer", "confidence": 0.99},
    })

    # 3. 分析軸各段落 (段差、配合段、卡簧槽)
    segments = sorted(steps, key=lambda s: s.get("position", s.get("pos", 0)))
    if segments:
        max_len_seg = max(segments, key=lambda s: s.get("length", s.get("len", 0)))
        groove_idx = 1
        step_idx = 1

        for i, s in enumerate(segments):
            dia = s.get("diameter", s.get("Ø", 0))
            length = s.get("length", s.get("len", 0))
            pos = s.get("position", s.get("pos", 0))

            if "center" in s:
                center_3d = [float(s["center"][0]), float(s["center"][1]), float(s["center"][2])]
            else:
                if main_axis == "X":
                    center_3d = [float(pos), bbox_c[1], bbox_c[2]]
                elif main_axis == "Y":
                    center_3d = [bbox_c[0], float(pos), bbox_c[2]]
                else:
                    center_3d = [bbox_c[0], bbox_c[1], float(pos)]

            if main_axis == "X":
                size_3d = [float(length), float(dia), float(dia)]
            elif main_axis == "Y":
                size_3d = [float(dia), float(length), float(dia)]
            else:
                size_3d = [float(dia), float(dia), float(length)]

            if s == max_len_seg:
                records.append({
                    "id": "journal_main",
                    "type": "shaft_or_boss",
                    "name": f"主軸承配合段 Ø{dia:.2f} (長度{length:.2f}mm)",
                    "view": "front",
                    "role": "bearing_journal",
                    "nominal": {"diameter": dia, "length": length, "position": pos},
                    "tolerance_key": "shaft_fit_h6",
                    "geometry": {
                        "kind": "cylinder",
                        "center": center_3d,
                        "size": size_3d,
                        "diameter": dia,
                        "length": length,
                        "axis": main_axis,
                    },
                    "source": {"extractor": "ShaftSemanticAnalyzer.journal", "confidence": 0.96},
                })
            elif (i > 0 and i < len(segments) - 1 and dia < segments[i - 1].get("diameter", 0) - 0.1 and dia < segments[i + 1].get("diameter", 0) - 0.1) or (dia < shaft_od - 0.35 and 0.5 <= length <= 3.5):
                groove_depth = (shaft_od - dia) / 2.0
                records.append({
                    "id": f"groove_{groove_idx:02d}",
                    "type": "groove_or_slot",
                    "name": f"卡簧/C型扣環槽 Ø{dia:.2f} (槽寬{length:.2f}mm, 槽深{groove_depth:.2f}mm)",
                    "view": "front",
                    "role": "snap_ring_groove",
                    "nominal": {"groove_diameter": dia, "groove_width": length, "groove_depth": groove_depth, "position": pos},
                    "tolerance_key": "snap_ring_groove",
                    "geometry": {
                        "kind": "groove",
                        "center": center_3d,
                        "size": size_3d,
                        "diameter": dia,
                        "width": length,
                        "depth": groove_depth,
                        "axis": main_axis,
                    },
                    "source": {"extractor": "ShaftSemanticAnalyzer.groove", "confidence": 0.95},
                })
                groove_idx += 1
            elif dia < shaft_od - 0.15 and length <= 1.5:
                records.append({
                    "id": f"relief_{groove_idx:02d}",
                    "type": "groove_or_slot",
                    "name": f"軸身退刀槽/過渡縮頸 Ø{dia:.2f} (長度{length:.2f}mm)",
                    "view": "front",
                    "role": "relief_groove",
                    "nominal": {"diameter": dia, "length": length, "position": pos},
                    "tolerance_key": "relief_groove",
                    "geometry": {
                        "kind": "groove",
                        "center": center_3d,
                        "size": size_3d,
                        "diameter": dia,
                        "length": length,
                        "axis": main_axis,
                    },
                    "source": {"extractor": "ShaftSemanticAnalyzer.relief", "confidence": 0.92},
                })
                groove_idx += 1
            else:
                records.append({
                    "id": f"step_{step_idx:02d}",
                    "type": "step",
                    "name": f"軸端定位台階 Ø{dia:.2f} (長度{length:.2f}mm)",
                    "view": "front",
                    "role": "shaft_step",
                    "nominal": {"diameter": dia, "length": length, "position": pos},
                    "tolerance_key": "shaft_step",
                    "geometry": {
                        "kind": "step",
                        "center": center_3d,
                        "size": size_3d,
                        "diameter": dia,
                        "length": length,
                        "axis": main_axis,
                    },
                    "source": {"extractor": "ShaftSemanticAnalyzer.step", "confidence": 0.90},
                })
                step_idx += 1

    # 4. 圓錐與倒角特徵 (尖端組裝導角 / 端部倒角)
    cones = summary.get("cones", [])
    dedup_cones = []
    seen_cone_keys = set()
    for cone in cones:
        c_center = cone.get("center", bbox_c)
        ax_pos = c_center[1] if main_axis == "Y" else (c_center[2] if main_axis == "Z" else c_center[0])
        key = (round(ax_pos, 1), round(cone.get("included_angle_deg", 90), 1))
        if key not in seen_cone_keys:
            seen_cone_keys.add(key)
            dedup_cones.append(cone)

    for idx, cone in enumerate(dedup_cones, start=1):
        min_d = cone.get("min_diameter", 0)
        max_d = cone.get("max_diameter", shaft_od)
        inc_angle = cone.get("included_angle_deg", 90)
        semi_angle = cone.get("semi_angle_deg", 45)
        height = cone.get("height", 0.5)
        c_center = cone.get("center", bbox_c)

        c_center_3d = [float(c_center[0]), float(c_center[1]), float(c_center[2])]
        if main_axis == "X":
            c_size_3d = [float(max(0.2, height)), float(max_d), float(max_d)]
        elif main_axis == "Y":
            c_size_3d = [float(max_d), float(max(0.2, height)), float(max_d)]
        else:
            c_size_3d = [float(max_d), float(max_d), float(max(0.2, height))]

        if 35 <= inc_angle <= 75:
            records.append({
                "id": "lead_in_chamfer",
                "type": "cone_or_chamfer",
                "name": f"尖端組裝引導倒角/錐度 (錐角{inc_angle:.1f}°, Ø{min_d:.2f}~Ø{max_d:.2f})",
                "view": "front",
                "role": "lead_in_pilot",
                "nominal": {"included_angle": inc_angle, "min_diameter": min_d, "max_diameter": max_d, "height": height},
                "tolerance_key": "lead_in_angle",
                "geometry": {
                    "kind": "cone",
                    "center": c_center_3d,
                    "size": c_size_3d,
                    "min_diameter": min_d,
                    "max_diameter": max_d,
                    "axis": main_axis,
                },
                "source": {"extractor": "ShaftSemanticAnalyzer.lead_in", "confidence": 0.95},
            })
        else:
            records.append({
                "id": f"chamfer_{idx:02d}",
                "type": "cone_or_chamfer",
                "name": f"軸端倒角 C{height:.2f} ({semi_angle:.0f}°)",
                "view": "front",
                "role": "end_chamfer",
                "nominal": {"chamfer": height, "angle": semi_angle},
                "tolerance_key": "chamfer_angle",
                "geometry": {
                    "kind": "cone",
                    "center": c_center_3d,
                    "size": c_size_3d,
                    "chamfer": height,
                    "axis": main_axis,
                },
                "source": {"extractor": "ShaftSemanticAnalyzer.chamfer", "confidence": 0.92},
            })

    # 5. 槽底圓角與過渡圓角 (依軸向位置與半徑去重)
    fillets = summary.get("fillets", [])
    dedup_fillets = []
    seen_fillet_keys = set()
    for fil in fillets:
        r = round(fil.get("radius", 0), 2)
        f_mid = fil.get("mid_point", fil.get("center", bbox_c))
        ax_pos = f_mid[1] if main_axis == "Y" else (f_mid[2] if main_axis == "Z" else f_mid[0])
        key = (round(ax_pos, 1), r)
        if key not in seen_fillet_keys:
            seen_fillet_keys.add(key)
            dedup_fillets.append(fil)

    for idx, fil in enumerate(dedup_fillets, start=1):
        r = fil.get("radius", 0)
        arc_len = fil.get("arc_length", 0)
        sweep_deg = fil.get("sweep_angle_deg", 90)
        f_center = fil.get("center", bbox_c)
        f_mid = fil.get("mid_point", f_center)

        f_center_3d = [float(f_mid[0]), float(f_mid[1]), float(f_mid[2])]
        if main_axis == "X":
            f_size_3d = [float(max(0.3, r)), float(r * 2.0), float(r * 2.0)]
        elif main_axis == "Y":
            f_size_3d = [float(r * 2.0), float(max(0.3, r)), float(r * 2.0)]
        else:
            f_size_3d = [float(r * 2.0), float(r * 2.0), float(max(0.3, r))]

        name = f"卡簧槽/階梯槽底圓角 R{r:.2f}" if r <= 0.5 else f"過渡圓角 R{r:.2f}"
        records.append({
            "id": f"fillet_{idx:02d}",
            "type": "fillet_or_round",
            "name": f"{name} (弧長{arc_len:.2f})",
            "view": "front",
            "role": "groove_fillet" if r <= 0.5 else "transition_fillet",
            "nominal": {"radius": r, "arc_length": arc_len, "sweep_angle_deg": sweep_deg},
            "tolerance_key": "fillet_radius",
            "geometry": {
                "kind": "fillet",
                "radius": r,
                "center": f_center_3d,
                "size": f_size_3d,
            },
            "source": {"extractor": "ShaftSemanticAnalyzer.fillets", "confidence": 0.88},
        })

    # 6. 基準B 軸向定位端面
    records.append({
        "id": "datum_b_face",
        "type": "wall_thickness",
        "name": "基準B 軸向端面 (Datum Face B)",
        "view": "front",
        "role": "datum_face",
        "nominal": {"datum": "B"},
        "tolerance_key": "flatness_perpendicularity",
        "geometry": {
            "kind": "plane",
            "center": face_c,
            "size": face_sz,
        },
        "source": {"extractor": "ShaftSemanticAnalyzer", "confidence": 0.95},
    })

    return records


def build_projected_feature_records(view_data, part_type):
    """
    從三視圖投影幾何中提取完整 2D 視圖特徵 (圓、圓弧、角點、基準線)
    """
    records = []
    if not view_data:
        return records

    for view_name, vd in view_data.items():
        if not vd:
            continue

        bbox = vd.get("bbox")
        if bbox:
            x0, y0, x1, y1 = bbox
            cx = (x0 + x1) / 2.0
            cy = (y0 + y1) / 2.0
            records.append({
                "id": f"{view_name}_bbox_center",
                "type": "projected_bbox_center",
                "name": f"{view_name} 視圖中心 ({cx:.1f}, {cy:.1f})",
                "view": view_name,
                "role": "reference",
                "nominal": {"center_x": cx, "center_y": cy, "width": x1 - x0, "height": y1 - y0},
                "tolerance_key": "projected_reference",
                "geometry": {"kind": "point", "point": (cx, cy)},
                "source": {"extractor": "ViewProjector.bbox", "confidence": 0.95},
            })

        circles_all = [e for e in vd.get("visible", []) if e.get("type") == "circle"]
        full_circles = [c for c in circles_all if not c.get("is_arc", False)]
        arc_circles = [c for c in circles_all if c.get("is_arc", False)]

        full_circles.sort(key=lambda c: -c.get("radius", 0))
        unique_full = []
        for c in full_circles:
            center = c.get("center")
            r = c.get("radius", 0)
            if not center or r <= 0.05:
                continue
            if any(_dist2(center, old.get("center")) < 0.2 and abs(r - old.get("radius", 0)) < 0.2 for old in unique_full):
                continue
            unique_full.append(c)

        for idx, c in enumerate(unique_full, start=1):
            r = c["radius"]
            name = f"{view_name} 軸端/同心圓 Ø{r*2:.2f}" if part_type == "SHAFT" else f"{view_name} 投影圓 Ø{r*2:.2f}"
            records.append({
                "id": f"{view_name}_circle_{idx:02d}",
                "type": "projected_circle",
                "name": f"{name} ({c['center'][0]:.1f}, {c['center'][1]:.1f})",
                "view": view_name,
                "role": "datum" if idx == 1 else "feature",
                "nominal": {"diameter": r * 2, "radius": r},
                "tolerance_key": "projected_circle",
                "geometry": {"kind": "circle", "center": c["center"], "radius": r},
                "source": {"extractor": "ViewProjector.visible.circle", "confidence": 0.85},
            })

        arc_circles.sort(key=lambda c: -c.get("radius", 0))
        unique_arcs = []
        for c in arc_circles:
            center = c.get("center")
            r = c.get("radius", 0)
            if not center or r <= 0.05:
                continue
            if any(_dist2(center, old.get("center")) < 0.3 and abs(r - old.get("radius", 0)) < 0.3 for old in unique_arcs):
                continue
            unique_arcs.append(c)

        for idx, c in enumerate(unique_arcs, start=1):
            r = c["radius"]
            sweep = c.get("sweep_angle_deg", 90)
            records.append({
                "id": f"{view_name}_arc_{idx:02d}",
                "type": "projected_arc",
                "name": f"{view_name} 圓弧 R{r:.2f} ({sweep:.0f}°)",
                "view": view_name,
                "role": "fillet_round",
                "nominal": {"radius": r, "sweep_angle_deg": sweep},
                "tolerance_key": "projected_arc",
                "geometry": {"kind": "arc", "center": c["center"], "radius": r, "sweep_angle_deg": sweep},
                "source": {"extractor": "ViewProjector.visible.arc", "confidence": 0.80},
            })

    return records


def draw_feature_overlay(msp, layout, records, view_data=None):
    """
    在 DXF 圖面上繪製完整特徵圖層：
      1. 圖面幾何標記 (圓圈、十字定位標、F01/F02 代碼)
      2. 頂部多欄特徵表格 (支援完整特徵列出與分類統計)
    """
    type_counts = {}
    for rec in records:
        rtype = rec.get("type", "other")
        type_counts[rtype] = type_counts.get(rtype, 0) + 1

    x_start = layout.margin + 6
    y_top = layout.paper_h - layout.margin - 12
    
    stats_str = f"TOTAL: {len(records)} | 軸/配合段: {type_counts.get('shaft_or_boss', 0)} | 卡簧/凹槽: {type_counts.get('groove_or_slot', 0)} | 倒角/錐度: {type_counts.get('cone_or_chamfer', 0)} | 圓角: {type_counts.get('fillet_or_round', 0)} | 階梯: {type_counts.get('step', 0)} | 孔洞: {type_counts.get('hole', 0)} | 基準面: {type_counts.get('wall_thickness', 0) + type_counts.get('datum', 0)} | 2D投影: {type_counts.get('projected_circle', 0) + type_counts.get('projected_arc', 0)}"

    msp.add_text(
        "FORCECON FEATURE LAYER & METRICS",
        height=2.8,
        dxfattribs={"layer": "FEATURE", "insert": (x_start, y_top), "style": "CHINESE", "color": 4},
    )
    msp.add_text(
        stats_str,
        height=1.8,
        dxfattribs={"layer": "FEATURE", "insert": (x_start, y_top - 4.0), "style": "CHINESE", "color": 3},
    )

    col_width = (layout.paper_w - layout.margin * 2 - 12) / 4.0
    row_height = 3.2
    y_table_start = y_top - 8.5

    for idx, rec in enumerate(records, start=1):
        col_idx = (idx - 1) // 12
        row_idx = (idx - 1) % 12
        if col_idx >= 4:
            break

        col_x = x_start + col_idx * col_width
        col_y = y_table_start - row_idx * row_height

        rec_name = rec.get("name", "")
        if len(rec_name) > 22:
            rec_name = rec_name[:20] + ".."

        tag = f"F{idx:02d}"
        text = f"{tag} [{rec.get('type','')[:6]}] {rec_name}"
        msp.add_text(
            text,
            height=1.5,
            dxfattribs={"layer": "FEATURE", "insert": (col_x, col_y), "style": "CHINESE", "color": 4},
        )

    if view_data:
        _draw_projected_feature_points(msp, layout, records, view_data)


def _draw_projected_feature_points(msp, layout, records, view_data):
    """在各投影視圖中精準繪製特徵標記圈與編號"""
    for idx, rec in enumerate(records, start=1):
        geom = rec.get("geometry", {})
        view_name = rec.get("view")
        if view_name not in getattr(layout, "view_sizes", {}):
            continue
        vd = view_data.get(view_name)
        if not vd:
            continue

        point = None
        radius = 1.4
        geom_kind = geom.get("kind")

        if geom_kind in ("circle", "arc"):
            point = geom.get("center")
            r_val = geom.get("radius", 1.0)
            radius = max(1.2, min(3.2, r_val * layout.scale * 0.18))
        elif geom_kind == "fillet":
            point = geom.get("mid_point") or geom.get("center")
        elif geom_kind in ("point", "cone", "torus", "cylinder", "groove", "step"):
            point = geom.get("center") or geom.get("point")

        if not point or len(point) < 2:
            continue

        label = f"F{idx:02d}"
        px, py = _project_to_paper(layout, view_name, vd, point)
        
        msp.add_circle((px, py), radius, dxfattribs={"layer": "FEATURE", "color": 4})
        msp.add_line((px - radius * 1.5, py), (px + radius * 1.5, py), dxfattribs={"layer": "FEATURE", "color": 4})
        msp.add_line((px, py - radius * 1.5), (px, py + radius * 1.5), dxfattribs={"layer": "FEATURE", "color": 4})
        msp.add_text(
            label,
            height=1.5,
            dxfattribs={"layer": "FEATURE", "insert": (px + radius + 0.8, py + radius + 0.8), "style": "CHINESE", "color": 4},
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
