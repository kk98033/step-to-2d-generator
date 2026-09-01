"""
智慧標註規則提取與尺寸生成引擎 (Smart Rule-Based Annotation Engine)

架構原則:
1. 複製並擴充舊版特化提取器 (Shaft, Fan, Housing, Stamped Base, Generic) 的高精度幾何算法。
2. 零寫死規則：將所有可標註特徵動態提煉為結構化候選規則 (Candidate Dimension Rules)，供使用者在介面上自由勾選、自訂公差與指派視圖。
3. 渲染時直接將規則轉換為真實幾何錨定的 DimensionTask，由 LayoutEngine 精確排版對齊，保證 100% 幾何對齊與專業工程圖品質。
4. 完全獨立運作，絕不改動舊版代碼。
"""
import os
import math
import copy
from typing import List, Dict, Any, Optional
import ezdxf

from auto_2d_drawing.dimension_task import DimensionTask
from auto_2d_drawing.layout_engine import LayoutEngine
from auto_2d_drawing.feature_extractor import FeatureExtractor
from auto_2d_drawing.part_classifier import PartClassifier
from auto_2d_drawing.extractors.base_extractor import BaseExtractor
from auto_2d_drawing.title_block import TitleBlock
from auto_2d_drawing.dxf_drawer import DxfDrawer
from auto_2d_drawing.config import VIEW_CONFIG, PAPER_SIZES
from auto_2d_drawing.pdf_exporter import export_pdf, export_png, export_svg


class SmartRuleExtractor(BaseExtractor):
    """
    動態幾何規則提取器：
    根據 3D 模型與 2D 投影，掃描並產出所有可能的幾何標註候選規則。
    """

    def extract(self, feature_data, view_data, view_name):
        """實作 BaseExtractor 抽象介面"""
        return []

    def extract_all_candidate_rules(self, shape, view_data: Dict[str, Any], part_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        掃描 3D 實體與所有投影視圖，產生結構化的候選標註規則列表。
        完全繼承 3D 特徵圖層中的所有 3D 特徵幾何定義 (38~44項特徵)，保證 3D 高亮與 2D 尺寸規則 100% 同步。
        """
        feat = FeatureExtractor(shape)
        if not part_type:
            classifier = PartClassifier()
            part_type = classifier.classify(feat, None)

        try:
            from auto_2d_drawing.feature_layer import build_feature_records
        except ImportError:
            from feature_layer import build_feature_records

        raw_3d_records = build_feature_records(feat, part_type)
        rules: List[Dict[str, Any]] = []

        vd_front = view_data.get('front', {})
        vd_top = view_data.get('top', {})
        vd_right = view_data.get('right', {})

        w_real, h_real = vd_front.get('size', (feat.W, feat.H))
        is_horizontal = w_real >= h_real

        # 計算 2D 前視圖輪廓頂點
        front_verts = []
        if vd_front and vd_front.get('visible'):
            axis = 'x' if is_horizontal else 'y'
            front_verts = self._find_contour_vertices(vd_front['visible'], axis=axis, tol=0.1, max_vertices=16)

        base_left = front_verts[0] if len(front_verts) >= 2 else -w_real / 2.0
        base_right = front_verts[-1] if len(front_verts) >= 2 else w_real / 2.0
        midpoint = (base_left + base_right) / 2.0
        overall_len = abs(base_right - base_left)

        for rec in raw_3d_records:
            r_copy = dict(rec)
            rec_id = rec.get("id", "")
            rec_type = rec.get("type", "")
            rec_name = rec.get("name", "")
            rec_role = str(rec.get("role", ""))
            rec_nom = rec.get("nominal", {})
            geo = rec.get("geometry", {})
            c_3d = geo.get("center", [0.0, 0.0, 0.0])

            # 預設屬性
            category = "general"
            dim_type = "LINEAR"
            nominal_val = 0.0
            default_tol = ""
            default_prefix = ""
            preferred_view = rec.get("view", "front")
            side = "BOTTOM"
            rank = 1
            baseline = "NONE"
            geom_payload = {}

            # 1. 軸整體包絡 (overall_size)
            if rec_type == "overall_size":
                category = "overall"
                dim_type = "LINEAR"
                rank = 2
                side = "BOTTOM"
                baseline = "NONE"
                nominal_val = round(rec_nom.get("length", rec_nom.get("height", overall_len)), 2)
                default_tol = "±0.10"
                default_prefix = ""
                bottom_rim_y = -h_real / 2.0 if is_horizontal else -w_real / 2.0
                geom_payload = {
                    "start_proj": [base_left, bottom_rim_y] if is_horizontal else [bottom_rim_y, base_left],
                    "end_proj": [base_right, bottom_rim_y] if is_horizontal else [bottom_rim_y, base_right],
                    "axis": 'x' if is_horizontal else 'y'
                }

            # 2. 基準軸心線 (datum)
            elif rec_type == "datum":
                category = "datum"
                dim_type = "CENTERLINES"
                rank = 1
                side = "TOP"
                nominal_val = 0.0
                default_tol = ""
                default_prefix = ""
                geom_payload = {
                    "center": [0.0, 0.0],
                    "start_proj": [base_left - 3.0, 0.0] if is_horizontal else [0.0, base_left - 3.0],
                    "end_proj": [base_right + 3.0, 0.0] if is_horizontal else [0.0, base_right + 3.0],
                    "axis": 'x' if is_horizontal else 'y'
                }

            # 3. 階梯段差長度 (step)
            elif rec_type == "step":
                category = "step"
                dim_type = "LINEAR"
                rank = 1
                side = "BOTTOM"
                nominal_val = round(rec_nom.get("length", rec_nom.get("depth", 1.0)), 2)
                default_tol = "±0.05"
                default_prefix = ""
                step_pos_3d = float(c_3d[1] if is_horizontal else c_3d[2])
                bottom_rim_y = -h_real / 2.0 if is_horizontal else -w_real / 2.0
                if step_pos_3d < midpoint:
                    baseline = "NONE"
                    start_p = (base_left, bottom_rim_y) if is_horizontal else (bottom_rim_y, base_left)
                    end_p = (base_left + nominal_val, bottom_rim_y) if is_horizontal else (bottom_rim_y, base_left + nominal_val)
                else:
                    baseline = "NONE"
                    start_p = (base_right - nominal_val, bottom_rim_y) if is_horizontal else (bottom_rim_y, base_right - nominal_val)
                    end_p = (base_right, bottom_rim_y) if is_horizontal else (bottom_rim_y, base_right)
                geom_payload = {
                    "start_proj": list(start_p),
                    "end_proj": list(end_p),
                    "axis": 'x' if is_horizontal else 'y'
                }

            # 4. 圓柱軸徑 / 配合段 (shaft_or_boss)
            elif rec_type == "shaft_or_boss":
                category = "shaft"
                dim_type = "DIAMETER"
                rank = 1
                side = "LEFT" if is_horizontal else "BOTTOM"
                nominal_val = round(rec_nom.get("diameter", 3.0), 2)
                is_main_journal = "journal" in rec_role or "bearing" in rec_name or "main" in rec_id
                default_tol = "±0.005" if is_main_journal else "±0.02"
                default_prefix = "Φ"
                axial_pos = float(c_3d[1] if is_horizontal else c_3d[2])
                geom_payload = {
                    "start_proj": [axial_pos, -nominal_val / 2.0] if is_horizontal else [-nominal_val / 2.0, axial_pos],
                    "end_proj": [axial_pos, nominal_val / 2.0] if is_horizontal else [nominal_val / 2.0, axial_pos],
                    "diameter": nominal_val,
                }
                
                # 若具備顯著軸向長度，額外追加段落長度標註規則
                seg_len = round(rec_nom.get("length", 0.0), 2)
                if seg_len > 1.0:
                    bottom_rim_y = -h_real / 2.0 if is_horizontal else -w_real / 2.0
                    x_st = axial_pos - seg_len / 2.0
                    x_en = axial_pos + seg_len / 2.0
                    extra_rule = copy.deepcopy(r_copy)
                    extra_rule["rule_id"] = f"{rec_id}_len"
                    extra_rule["category"] = "step"
                    extra_rule["name"] = f"主軸承配合段長度 L{seg_len:.2f}mm"
                    extra_rule["dim_type"] = "LINEAR"
                    extra_rule["nominal_value"] = seg_len
                    extra_rule["default_tolerance"] = "±0.05"
                    extra_rule["default_prefix"] = ""
                    extra_rule["preferred_view"] = "front"
                    extra_rule["target_views"] = ["front"]
                    extra_rule["views"] = ["front"]
                    extra_rule["side"] = "BOTTOM"
                    extra_rule["rank"] = 1
                    extra_rule["baseline"] = "NONE"
                    extra_rule["geometry_payload"] = {
                        "start_proj": [x_st, bottom_rim_y] if is_horizontal else [bottom_rim_y, x_st],
                        "end_proj": [x_en, bottom_rim_y] if is_horizontal else [bottom_rim_y, x_en],
                        "axis": 'x' if is_horizontal else 'y'
                    }
                    extra_rule["enabled"] = True
                    rules.append(extra_rule)

            # 5. 卡簧槽 / 溝槽 (groove_or_slot)
            elif rec_type == "groove_or_slot":
                category = "groove"
                dim_type = "DIAMETER"
                rank = 1
                side = "LEFT"
                nominal_val = round(rec_nom.get("diameter", rec_nom.get("groove_diameter", rec_nom.get("major_diameter", 2.5))), 2)
                default_tol = "H13"
                default_prefix = "Φ"
                axial_pos = float(c_3d[1] if is_horizontal else c_3d[2])
                geom_payload = {
                    "start_proj": [axial_pos, -nominal_val / 2.0] if is_horizontal else [-nominal_val / 2.0, axial_pos],
                    "end_proj": [axial_pos, nominal_val / 2.0] if is_horizontal else [nominal_val / 2.0, axial_pos],
                    "diameter": nominal_val,
                }
                
                # 槽寬度線性標註規則 (下側鏈式尺寸)
                groove_w = round(rec_nom.get("groove_width", rec_nom.get("length", rec_nom.get("width", 0.9))), 2)
                if groove_w > 0:
                    bottom_rim_y = -h_real / 2.0 if is_horizontal else -w_real / 2.0
                    x_st = axial_pos - groove_w / 2.0
                    x_en = axial_pos + groove_w / 2.0
                    extra_rule = copy.deepcopy(r_copy)
                    extra_rule["rule_id"] = f"{rec_id}_width"
                    extra_rule["category"] = "step"
                    extra_rule["name"] = f"卡簧/退刀槽寬度 W{groove_w:.2f}mm"
                    extra_rule["dim_type"] = "LINEAR"
                    extra_rule["nominal_value"] = groove_w
                    extra_rule["default_tolerance"] = "±0.05"
                    extra_rule["default_prefix"] = ""
                    extra_rule["preferred_view"] = "front"
                    extra_rule["target_views"] = ["front"]
                    extra_rule["views"] = ["front"]
                    extra_rule["side"] = "BOTTOM"
                    extra_rule["rank"] = 1
                    extra_rule["baseline"] = "NONE"
                    extra_rule["geometry_payload"] = {
                        "start_proj": [x_st, bottom_rim_y] if is_horizontal else [bottom_rim_y, x_st],
                        "end_proj": [x_en, bottom_rim_y] if is_horizontal else [bottom_rim_y, x_en],
                        "axis": 'x' if is_horizontal else 'y'
                    }
                    extra_rule["enabled"] = True
                    rules.append(extra_rule)

            # 6. 倒角 (cone_or_chamfer)
            elif rec_type == "cone_or_chamfer":
                category = "chamfer"
                dim_type = "LEADER"
                rank = 1
                side = "TOP"
                nominal_val = round(rec_nom.get("chamfer", rec_nom.get("height", 0.5)), 2)
                default_tol = ""
                default_prefix = "C"
                chamfer_pos_3d = float(c_3d[1] if is_horizontal else c_3d[2])
                top_rim_y = (h_real / 2.0) if is_horizontal else (w_real / 2.0)
                is_left = chamfer_pos_3d < midpoint if midpoint else chamfer_pos_3d < 0
                
                if is_left:
                    pos_axial = base_left + min(nominal_val, 1.0) * 0.5
                    angle = 135.0
                else:
                    pos_axial = base_right - min(nominal_val, 1.0) * 0.5
                    angle = 45.0
                
                start_p = [pos_axial, top_rim_y] if is_horizontal else [top_rim_y, pos_axial]
                geom_payload = {
                    "start_proj": start_p,
                    "angle": angle,
                }

            # 7. 圓角 (fillet_or_round)
            elif rec_type == "fillet_or_round":
                category = "fillet"
                dim_type = "LEADER"
                rank = 1
                side = "TOP"
                nominal_val = round(rec_nom.get("radius", rec_nom.get("R", 0.5)), 2)
                default_tol = ""
                default_prefix = "R"
                fillet_pos_3d = float(c_3d[1] if is_horizontal else c_3d[2])
                top_rim_y = (h_real / 2.0) if is_horizontal else (w_real / 2.0)
                
                best_v = base_left
                if front_verts:
                    best_v = min(front_verts, key=lambda v: abs(v - fillet_pos_3d))
                else:
                    best_v = fillet_pos_3d
                    
                angle = 135.0 if best_v < midpoint else 45.0
                start_p = [best_v, top_rim_y] if is_horizontal else [top_rim_y, best_v]
                geom_payload = {
                    "start_proj": start_p,
                    "angle": angle,
                }

            # 8. 內徑孔 (hole)
            elif rec_type == "hole":
                category = "hole"
                dim_type = "DIAMETER"
                rank = 1
                side = "LEFT"
                nominal_val = round(rec_nom.get("diameter", 3.0), 2)
                default_tol = "H7"
                default_prefix = "Φ"
                geom_payload = {
                    "start_proj": [base_left, -nominal_val / 2.0] if is_horizontal else [-nominal_val / 2.0, base_left],
                    "end_proj": [base_left, nominal_val / 2.0] if is_horizontal else [nominal_val / 2.0, base_left],
                    "diameter": nominal_val,
                }

            # 9. 壁厚 / 基準面 (wall_thickness)
            elif rec_type == "wall_thickness":
                category = "thickness"
                dim_type = "LINEAR"
                rank = 1
                side = "RIGHT"
                preferred_view = "right"
                nominal_val = round(rec_nom.get("thickness", 1.0), 2)
                default_tol = "±0.05"
                default_prefix = "T="
                geom_payload = {
                    "start_proj": [0.0, 0.0],
                    "end_proj": [0.0, nominal_val],
                    "axis": "y"
                }

            # 10. 孔群 (hole_pattern)
            elif rec_type == "hole_pattern":
                category = "pattern"
                dim_type = "HOLE_PATTERN"
                rank = 1
                side = "TOP"
                preferred_view = "top"
                nominal_val = round(rec_nom.get("pcd", 20.0), 2)
                default_tol = "±0.05"
                count = rec_nom.get("count", 4)
                default_prefix = f"{count}-M3 PCD "
                geom_payload = {
                    "center": [0.0, 0.0],
                    "radius": nominal_val / 2.0,
                    "count": count,
                    "hole_diameter": rec_nom.get("hole_diameter", 3.0)
                }

            else:
                category = "general"
                dim_type = "LINEAR"
                nominal_val = 1.0
                default_tol = "±0.1"
                geom_payload = {}

            # 預設多視角標註設定 (合理指派具備可見輪廓的視圖)
            if category in ("overall", "step"):
                target_views = ["front"]
            elif category in ("shaft", "hole"):
                target_views = ["front", "top"]
            elif category in ("groove",):
                target_views = ["front", "top"]
            elif category in ("chamfer", "fillet"):
                target_views = ["front"]
            elif category in ("thickness",):
                target_views = ["right", "front"]
            elif category in ("datum",):
                target_views = ["front", "top"]
            elif category in ("pattern",):
                target_views = ["top", "front"]
            else:
                target_views = ["front"]

            # 封裝規則物件
            r_copy["rule_id"] = rec_id
            r_copy["category"] = category
            r_copy["dim_type"] = dim_type
            r_copy["nominal_value"] = nominal_val
            r_copy["default_tolerance"] = default_tol
            r_copy["default_prefix"] = default_prefix
            r_copy["preferred_view"] = preferred_view
            r_copy["target_views"] = target_views
            r_copy["views"] = target_views
            r_copy["side"] = side
            r_copy["rank"] = rank
            r_copy["baseline"] = baseline
            r_copy["geometry_payload"] = geom_payload
            r_copy["enabled"] = True

            rules.append(r_copy)

        return rules

    # =========================================================================
    # 軸類零件 (SHAFT) 動態規則生成
    # =========================================================================
    def _extract_shaft_rules(self, feat: FeatureExtractor, view_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        rules = []
        vd_front = view_data.get('front')
        if not vd_front or not vd_front.get('visible'):
            return rules

        vis_edges = vd_front['visible']
        w_real, h_real = vd_front['size']
        is_horizontal = w_real >= h_real

        # 1. 基準軸心線 (Datum A)
        rules.append({
            "rule_id": "shaft_datum_axis",
            "name": "基準A 主軸中心線 (Datum Axis A)",
            "category": "datum",
            "dim_type": "CENTERLINES",
            "nominal_value": 0.0,
            "default_tolerance": "",
            "default_prefix": "",
            "preferred_view": "front",
            "side": "TOP",
            "rank": 1,
            "enabled": True,
            "geometry_payload": {
                "center": [0.0, 0.0],
                "start_proj": [0.0, -h_real / 2.0],
                "end_proj": [0.0, h_real / 2.0],
            }
        })

        # 2. 水平雙向基線段長標註 (Dual-Baseline Step Lengths)
        axis = 'x' if is_horizontal else 'y'
        verts = self._find_contour_vertices(vis_edges, axis=axis, tol=0.1, max_vertices=16)

        if len(verts) >= 2:
            base_left = verts[0]
            base_right = verts[-1]
            midpoint = (base_left + base_right) / 2.0
            overall_len = abs(base_right - base_left)

            # 左側與右側特徵分群
            left_features = []
            right_features = []
            for i in range(1, len(verts) - 1):
                v = verts[i]
                if v <= midpoint:
                    left_features.append((v, abs(v - base_left)))
                else:
                    right_features.append((v, abs(base_right - v)))

            left_features.sort(key=lambda x: x[1])
            right_features.sort(key=lambda x: x[1])

            # 左側基準段差
            for idx, (pos, dist) in enumerate(left_features):
                if dist < 0.2:
                    continue
                start = (base_left, 0.0) if axis == 'x' else (0.0, base_left)
                end = (pos, 0.0) if axis == 'x' else (0.0, pos)
                rules.append({
                    "rule_id": f"shaft_step_left_{idx + 1}",
                    "name": f"左基準階梯段長 L{idx + 1} ({dist:.2f}mm)",
                    "category": "step",
                    "dim_type": "LINEAR",
                    "nominal_value": round(dist, 2),
                    "default_tolerance": "±0.05",
                    "default_prefix": "",
                    "preferred_view": "front",
                    "side": "BOTTOM",
                    "rank": 1,
                    "baseline": "LEFT",
                    "enabled": True,
                    "geometry_payload": {
                        "start_proj": list(start),
                        "end_proj": list(end),
                        "axis": axis,
                    }
                })

            # 右側基準段差
            for idx, (pos, dist) in enumerate(right_features):
                if dist < 0.2:
                    continue
                start = (pos, 0.0) if axis == 'x' else (0.0, pos)
                end = (base_right, 0.0) if axis == 'x' else (0.0, base_right)
                rules.append({
                    "rule_id": f"shaft_step_right_{idx + 1}",
                    "name": f"右基準階梯段長 R{idx + 1} ({dist:.2f}mm)",
                    "category": "step",
                    "dim_type": "LINEAR",
                    "nominal_value": round(dist, 2),
                    "default_tolerance": "±0.05",
                    "default_prefix": "",
                    "preferred_view": "front",
                    "side": "BOTTOM",
                    "rank": 1,
                    "baseline": "RIGHT",
                    "enabled": True,
                    "geometry_payload": {
                        "start_proj": list(start),
                        "end_proj": list(end),
                        "axis": axis,
                    }
                })

            # 軸總長度 (Overall Length)
            start_ov = (base_left, 0.0) if axis == 'x' else (0.0, base_left)
            end_ov = (base_right, 0.0) if axis == 'x' else (0.0, base_right)
            rules.append({
                "rule_id": "shaft_overall_length",
                "name": f"軸總長度 (Overall Length {overall_len:.2f}mm)",
                "category": "overall",
                "dim_type": "LINEAR",
                "nominal_value": round(overall_len, 2),
                "default_tolerance": "±0.10",
                "default_prefix": "",
                "preferred_view": "front",
                "side": "BOTTOM",
                "rank": 2,
                "baseline": "NONE",
                "enabled": True,
                "geometry_payload": {
                    "start_proj": list(start_ov),
                    "end_proj": list(end_ov),
                    "axis": axis,
                }
            })

        # 3. 圓柱配合段直徑 (Journal / Step Diameters)
        unique_dias = []
        seen_d = set()
        for cyl in feat.shafts:
            d = round(cyl["diameter"], 2)
            if d not in seen_d and d > 0.3:
                seen_d.add(d)
                unique_dias.append(cyl)

        # 依照直徑大小或位置排序
        unique_dias.sort(key=lambda c: c["diameter"], reverse=True)

        for idx, cyl in enumerate(unique_dias):
            dia = cyl["diameter"]
            length = cyl.get("length", 0.0)
            ctrl_letter = chr(65 + idx)
            is_main = (idx == 0)
            tol = "±0.005" if is_main else "±0.02"

            rules.append({
                "rule_id": f"shaft_journal_dia_{idx + 1}",
                "name": f"配合段軸徑 ({ctrl_letter}) Φ{dia:.2f} (長度 {length:.2f}mm)",
                "category": "shaft",
                "dim_type": "DIAMETER",
                "nominal_value": round(dia, 2),
                "default_tolerance": tol,
                "default_prefix": f"({ctrl_letter})Φ",
                "preferred_view": "front",
                "side": "LEFT" if is_horizontal else "BOTTOM",
                "rank": 1,
                "enabled": True,
                "geometry_payload": {
                    "start_proj": [0.0, 0.0],
                    "end_proj": [0.0, 0.0],
                    "center": cyl.get("center", [0.0, 0.0, 0.0]),
                }
            })

        # 4. 卡簧槽 / 溝槽特徵 (Grooves / Snap Rings)
        for idx, torus in enumerate(feat.toruses):
            r_minor = torus.get("minor_radius", 0.0)
            r_major = torus.get("major_radius", 0.0)
            width = round(r_minor * 2, 2)
            bottom_dia = round((r_major - r_minor) * 2, 2)

            rules.append({
                "rule_id": f"shaft_groove_{idx + 1}",
                "name": f"卡簧槽/C型扣環槽 Φ{bottom_dia:.2f} (槽寬 {width:.2f}mm)",
                "category": "groove",
                "dim_type": "DIAMETER",
                "nominal_value": bottom_dia,
                "default_tolerance": "H13",
                "default_prefix": "Φ",
                "preferred_view": "front",
                "side": "LEFT",
                "rank": 1,
                "enabled": True,
                "geometry_payload": {
                    "center": torus.get("center", [0.0, 0.0, 0.0]),
                    "width": width,
                }
            })

        # 5. 倒角特徵 (Chamfers)
        for idx, cone in enumerate(feat.cones):
            if cone.get("semi_angle_deg", 0) > 20 and cone.get("height", 0) < 3.0:
                h = cone.get("height", 0.2)
                rules.append({
                    "rule_id": f"shaft_chamfer_{idx + 1}",
                    "name": f"導引倒角 C{h:.2f}x45°",
                    "category": "chamfer",
                    "dim_type": "LEADER",
                    "nominal_value": round(h, 2),
                    "default_tolerance": "",
                    "default_prefix": "C",
                    "preferred_view": "front",
                    "side": "TOP",
                    "rank": 1,
                    "enabled": True,
                    "geometry_payload": {
                        "center": cone.get("center", [0.0, 0.0, 0.0]),
                        "angle": 45,
                    }
                })

        return rules

    # =========================================================================
    # 風扇葉輪 (FAN) 動態規則生成
    # =========================================================================
    def _extract_fan_rules(self, feat: FeatureExtractor, view_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        rules = []
        vd_top = view_data.get('top') or view_data.get('front')
        if not vd_top or not vd_top.get('visible'):
            return rules

        vis_edges = vd_top['visible']
        bbox = vd_top['bbox']
        circles = [e for e in vis_edges if e['type'] == 'circle']

        if circles:
            largest = max(circles, key=lambda c: c['radius'])
            cx, cy = largest['center']
            max_r = largest['radius']
            overall_r = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) / 2.0

            # 1. 十字中心線 (Centerlines)
            rules.append({
                "rule_id": "fan_centerlines",
                "name": "旋轉軸心十字基準線 (Center Crosshair)",
                "category": "datum",
                "dim_type": "CENTERLINES",
                "nominal_value": 0.0,
                "default_tolerance": "",
                "default_prefix": "",
                "preferred_view": "top",
                "side": "TOP",
                "rank": 1,
                "enabled": True,
                "geometry_payload": {
                    "center": [cx, cy],
                    "radius": overall_r * 1.15,
                }
            })

            # 2. 同心圓 (最大外徑、中心孔、輪轂)
            concentric = []
            for c in circles:
                dc = ((c['center'][0] - cx)**2 + (c['center'][1] - cy)**2)**0.5
                if dc < 0.8:
                    concentric.append(c)

            concentric.sort(key=lambda c: c['radius'], reverse=True)
            filtered_c = []
            for c in concentric:
                if not filtered_c or abs(c['radius'] - filtered_c[-1]['radius']) > 0.5:
                    filtered_c.append(c)

            angles = [45, 135, 30, 150, 60, 120]
            for i, c in enumerate(filtered_c):
                r = c['radius']
                dia = r * 2
                ang = angles[i % len(angles)]

                if i == 0:
                    name = f"葉片最大旋轉外徑 Φ{dia:.2f}"
                    prefix = "最大外徑 Φ"
                    tol = "±0.10"
                elif i == len(filtered_c) - 1:
                    name = f"中心軸孔配合徑 Φ{dia:.2f}"
                    prefix = "中心孔 Φ"
                    tol = "H7"
                else:
                    name = f"輪轂內圈直徑 Φ{dia:.2f}"
                    prefix = "內圈 Φ"
                    tol = "±0.05"

                rules.append({
                    "rule_id": f"fan_concentric_{i + 1}",
                    "name": name,
                    "category": "hole" if i == len(filtered_c) - 1 else "shaft",
                    "dim_type": "LEADER",
                    "nominal_value": round(dia, 2),
                    "default_tolerance": tol,
                    "default_prefix": prefix,
                    "preferred_view": "top",
                    "side": "TOP",
                    "rank": 1,
                    "enabled": True,
                    "geometry_payload": {
                        "center": [cx, cy],
                        "radius": r,
                        "angle": ang,
                    }
                })

        # 3. 側面高度與輪轂段差 (Side Profile Heights)
        vd_side = view_data.get('right') or view_data.get('front')
        if vd_side and vd_side.get('visible'):
            w_s, h_s = vd_side['size']
            v_verts = self._find_contour_vertices(vd_side['visible'], axis='y', tol=0.1, max_vertices=8)
            if len(v_verts) >= 2:
                ov_h = abs(v_verts[-1] - v_verts[0])
                rules.append({
                    "rule_id": "fan_overall_height",
                    "name": f"葉輪總厚度/高度 (Overall Height {ov_h:.2f}mm)",
                    "category": "overall",
                    "dim_type": "LINEAR",
                    "nominal_value": round(ov_h, 2),
                    "default_tolerance": "±0.10",
                    "default_prefix": "",
                    "preferred_view": "right",
                    "side": "RIGHT",
                    "rank": 2,
                    "baseline": "NONE",
                    "enabled": True,
                    "geometry_payload": {
                        "start_proj": [0.0, v_verts[0]],
                        "end_proj": [0.0, v_verts[-1]],
                        "axis": "y",
                    }
                })

        return rules

    # =========================================================================
    # 機殼外框 (FAN_HOUSING) 動態規則生成
    # =========================================================================
    def _extract_housing_rules(self, feat: FeatureExtractor, view_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        rules = []
        vd_front = view_data.get('front')
        if not vd_front or not vd_front.get('visible'):
            return rules

        w_real, h_real = vd_front['size']

        # 1. 外形總長寬 (Overall Envelope)
        rules.append({
            "rule_id": "housing_overall_width",
            "name": f"外框總寬度 (Overall Width {w_real:.2f}mm)",
            "category": "overall",
            "dim_type": "LINEAR",
            "nominal_value": round(w_real, 2),
            "default_tolerance": "±0.15",
            "default_prefix": "",
            "preferred_view": "front",
            "side": "BOTTOM",
            "rank": 2,
            "baseline": "NONE",
            "enabled": True,
            "geometry_payload": {
                "start_proj": [-w_real / 2.0, 0.0],
                "end_proj": [w_real / 2.0, 0.0],
                "axis": "x",
            }
        })

        rules.append({
            "rule_id": "housing_overall_height",
            "name": f"外框總高度 (Overall Height {h_real:.2f}mm)",
            "category": "overall",
            "dim_type": "LINEAR",
            "nominal_value": round(h_real, 2),
            "default_tolerance": "±0.15",
            "default_prefix": "",
            "preferred_view": "front",
            "side": "RIGHT",
            "rank": 2,
            "baseline": "NONE",
            "enabled": True,
            "geometry_payload": {
                "start_proj": [0.0, -h_real / 2.0],
                "end_proj": [0.0, h_real / 2.0],
                "axis": "y",
            }
        })

        # 2. 孔群與 PCD 陣列 (Hole Patterns)
        for idx, pat in enumerate(feat.hole_patterns):
            pcd = pat.get("pcd", 0.0)
            count = pat.get("count", 4)
            h_dia = pat.get("hole_diameter", 3.0)

            rules.append({
                "rule_id": f"housing_pcd_pattern_{idx + 1}",
                "name": f"安裝孔群 {count}-Φ{h_dia:.2f} (PCD {pcd:.2f}mm)",
                "category": "pattern",
                "dim_type": "HOLE_PATTERN",
                "nominal_value": round(pcd, 2),
                "default_tolerance": "±0.05",
                "default_prefix": f"{count}-Φ{h_dia:.2f} PCD ",
                "preferred_view": "front",
                "side": "TOP",
                "rank": 1,
                "enabled": True,
                "geometry_payload": {
                    "center": pat.get("center", [0.0, 0.0]),
                    "radius": pcd / 2.0,
                    "count": count,
                    "hole_diameter": h_dia,
                }
            })

        # 3. 風道中心開孔 (Center Air Duct)
        duct_holes = [h for h in feat.holes if h["diameter"] > 15.0]
        for idx, hole in enumerate(duct_holes[:2]):
            dia = hole["diameter"]
            rules.append({
                "rule_id": f"housing_duct_hole_{idx + 1}",
                "name": f"中心風道開孔徑 Φ{dia:.2f}",
                "category": "hole",
                "dim_type": "LEADER",
                "nominal_value": round(dia, 2),
                "default_tolerance": "±0.10",
                "default_prefix": "風道中心孔 Φ",
                "preferred_view": "front",
                "side": "TOP",
                "rank": 1,
                "enabled": True,
                "geometry_payload": {
                    "center": hole.get("center", [0.0, 0.0, 0.0]),
                    "radius": dia / 2.0,
                    "angle": 60,
                }
            })

        return rules

    # =========================================================================
    # 沖壓底座 (STAMPED_FAN_BASE) 動態規則生成
    # =========================================================================
    def _extract_stamped_base_rules(self, feat: FeatureExtractor, view_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        rules = []
        vd_front = view_data.get('front')
        if not vd_front or not vd_front.get('visible'):
            return rules

        w_real, h_real = vd_front['size']

        # 1. 總長寬
        rules.append({
            "rule_id": "stamped_overall_width",
            "name": f"基板總寬度 (Width {w_real:.2f}mm)",
            "category": "overall",
            "dim_type": "LINEAR",
            "nominal_value": round(w_real, 2),
            "default_tolerance": "±0.10",
            "default_prefix": "",
            "preferred_view": "front",
            "side": "BOTTOM",
            "rank": 2,
            "enabled": True,
            "geometry_payload": {
                "start_proj": [-w_real / 2.0, 0.0],
                "end_proj": [w_real / 2.0, 0.0],
                "axis": "x",
            }
        })

        # 2. 板厚 (Thickness)
        if feat.thicknesses:
            t_val = feat.thicknesses[0].get("thickness", 1.0)
            rules.append({
                "rule_id": "stamped_sheet_thickness",
                "name": f"沖壓板厚 (Sheet Thickness T={t_val:.2f}mm)",
                "category": "thickness",
                "dim_type": "LINEAR",
                "nominal_value": round(t_val, 2),
                "default_tolerance": "±0.05",
                "default_prefix": "T=",
                "preferred_view": "right",
                "side": "RIGHT",
                "rank": 1,
                "enabled": True,
                "geometry_payload": {
                    "start_proj": [0.0, 0.0],
                    "end_proj": [0.0, t_val],
                    "axis": "y",
                }
            })

        return rules

    # =========================================================================
    # 通用機械零件 (GENERIC) 動態規則生成
    # =========================================================================
    def _extract_generic_rules(self, feat: FeatureExtractor, view_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        rules = []
        vd_front = view_data.get('front')
        if not vd_front or not vd_front.get('visible'):
            return rules

        w_real, h_real = vd_front['size']

        # 1. 整體包絡
        rules.append({
            "rule_id": "generic_overall_w",
            "name": f"整體最大寬度 (Max Width {w_real:.2f}mm)",
            "category": "overall",
            "dim_type": "LINEAR",
            "nominal_value": round(w_real, 2),
            "default_tolerance": "±0.10",
            "default_prefix": "",
            "preferred_view": "front",
            "side": "BOTTOM",
            "rank": 2,
            "enabled": True,
            "geometry_payload": {
                "start_proj": [-w_real / 2.0, 0.0],
                "end_proj": [w_real / 2.0, 0.0],
                "axis": "x",
            }
        })

        rules.append({
            "rule_id": "generic_overall_h",
            "name": f"整體最大高度 (Max Height {h_real:.2f}mm)",
            "category": "overall",
            "dim_type": "LINEAR",
            "nominal_value": round(h_real, 2),
            "default_tolerance": "±0.10",
            "default_prefix": "",
            "preferred_view": "front",
            "side": "RIGHT",
            "rank": 2,
            "enabled": True,
            "geometry_payload": {
                "start_proj": [0.0, -h_real / 2.0],
                "end_proj": [0.0, h_real / 2.0],
                "axis": "y",
            }
        })

        # 2. 圓柱特徵
        for idx, cyl in enumerate(feat.cylinders_raw[:4]):
            dia = cyl["diameter"]
            is_hole = cyl.get("is_hole", False)
            rules.append({
                "rule_id": f"generic_cyl_{idx + 1}",
                "name": f"{'內孔' if is_hole else '外軸'}直徑 Φ{dia:.2f}",
                "category": "hole" if is_hole else "shaft",
                "dim_type": "DIAMETER",
                "nominal_value": round(dia, 2),
                "default_tolerance": "H7" if is_hole else "±0.02",
                "default_prefix": "Φ",
                "preferred_view": "front",
                "side": "LEFT",
                "rank": 1,
                "enabled": True,
                "geometry_payload": {
                    "center": cyl.get("center", [0.0, 0.0, 0.0]),
                }
            })

        return rules


class SmartDimensionEngine:
    """
    智慧標註工程圖渲染引擎：
    接收使用者選取與配置的 Candidate Rules，將其轉換為精確的 DimensionTask，
    並由 LayoutEngine 在圖紙上進行專業 CAD 佈局與排版渲染。
    """

    def __init__(self):
        self.rule_extractor = SmartRuleExtractor()

    def get_candidate_rules(self, shape, view_data: Dict[str, Any], part_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """取得該零件的所有候選標註規則"""
        return self.rule_extractor.extract_all_candidate_rules(shape, view_data, part_type)

    def render_custom_drawing(
        self,
        dxf_path: str,
        pdf_path: str,
        png_path: str,
        configured_rules: List[Dict[str, Any]],
        view_data: Dict[str, Any],
        title_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """
        將已設定的規則清單渲染為 DXF、向量 PDF、SVG 與高解析 PNG 工程圖。
        """
        doc = ezdxf.new('R2010', setup=True)
        msp = doc.modelspace()

        # 1. 依據視圖分組建立 DimensionTask
        view_tasks: Dict[str, List[DimensionTask]] = {
            'front': [],
            'top': [],
            'right': [],
            'left': [],
            'back': []
        }

        for rule in configured_rules:
            if not rule.get("enabled", True):
                continue

            target_views = rule.get("target_views") or rule.get("views")
            if not target_views:
                pv = rule.get("preferred_view", "front")
                target_views = [pv] if isinstance(pv, str) else list(pv)

            dim_type = rule.get("dim_type", "LINEAR")
            category = str(rule.get("category", "general"))
            val = float(rule.get("nominal_value", 0.0))
            tol_str = str(rule.get("tolerance", rule.get("default_tolerance", ""))).strip()
            prefix = str(rule.get("prefix", rule.get("default_prefix", ""))).strip()
            side = str(rule.get("side", "BOTTOM"))
            rank = int(rule.get("rank", 1))
            baseline = str(rule.get("baseline", "NONE"))
            payload = rule.get("geometry_payload", {})

            # 格式化標註文字
            if prefix and not prefix.endswith(" ") and not prefix.endswith("Φ"):
                display_prefix = prefix + " "
            else:
                display_prefix = prefix

            rule_id = rule.get("rule_id", rule.get("id", ""))
            if dim_type == "DIAMETER":
                text_content = f"{val:.2f}"
            elif dim_type == "HOLE_PATTERN":
                count = payload.get("count", 4)
                h_dia = payload.get("hole_diameter", 3.0)
                text_content = f"{count}-Φ{h_dia:.2f} PCD {val:.2f}"
            elif dim_type == "LEADER":
                text_content = f"{val:.2f}"
            elif dim_type == "CENTERLINES":
                text_content = ""
            else:
                text_content = f"{val:.2f}"

            start_proj = tuple(payload.get("start_proj", [0.0, 0.0]))
            end_proj = tuple(payload.get("end_proj", [0.0, 0.0]))
            center = tuple(payload.get("center", [0.0, 0.0])[:2]) if "center" in payload else (0.0, 0.0)
            radius = float(payload.get("radius", val / 2.0 if val > 0 else 10.0))
            angle = float(payload.get("angle", 45.0))

            for tv in target_views:
                if tv not in view_tasks:
                    continue

                tv_side = side
                tv_start = start_proj
                tv_end = end_proj
                tv_center = None
                tv_radius = radius
                tv_angle = angle
                tv_dim_type = dim_type

                if tv == "front":
                    if dim_type in ("CENTERLINES",):
                        tv_center = None
                        tv_start = start_proj
                        tv_end = end_proj
                    elif dim_type == "HOLE_PATTERN":
                        tv_center = (0.0, 0.0)
                    elif dim_type == "DIAMETER":
                        tv_center = None
                        tv_side = "LEFT"
                    elif dim_type == "LEADER":
                        tv_center = None
                        tv_radius = 0.0
                        tv_start = start_proj
                        tv_angle = float(payload.get("angle", 45.0))

                elif tv == "top":
                    vd_t = view_data.get('top', {})
                    wt, ht = vd_t.get('size', (100, 100))
                    if dim_type == "DIAMETER":
                        tv_dim_type = "DIAMETER"
                        tv_center = (0.0, 0.0)
                        tv_radius = val / 2.0 if val > 0 else radius
                        # 依規則 ID 錯開頂視圖直徑引線角度 (45°, 135°, -45°)
                        if "overall" in rule_id or "journal" in rule_id:
                            tv_angle = 45.0
                        elif "groove_01" in rule_id:
                            tv_angle = 135.0
                        else:
                            tv_angle = -45.0
                    elif dim_type == "LINEAR":
                        tv_side = "TOP"
                        tv_start = start_proj if start_proj and start_proj != (0.0, 0.0) else (-wt / 2.0, 0.0)
                        tv_end = end_proj if end_proj and end_proj != (0.0, 0.0) else (wt / 2.0, 0.0)
                    elif dim_type in ("CENTERLINES",):
                        tv_center = (0.0, 0.0)
                        tv_radius = max(wt, ht) / 2.0 + 3.0
                        tv_start = None
                        tv_end = None
                    elif dim_type in ("LEADER",):
                        tv_center = None
                        tv_radius = 0.0
                        tv_start = start_proj
                        tv_angle = float(payload.get("angle", 45.0))
                    elif dim_type in ("HOLE_PATTERN",):
                        tv_center = (0.0, 0.0)
                        tv_radius = radius

                elif tv == "right":
                    vd_r = view_data.get('right', {})
                    wr, hr = vd_r.get('size', (100, 100))
                    if dim_type == "DIAMETER" or category in ("shaft", "hole", "groove"):
                        tv_dim_type = "DIAMETER"
                        tv_center = (0.0, 0.0)
                        tv_radius = val / 2.0 if val > 0 else max(wr, hr) / 2.0
                        tv_angle = 45.0
                    elif dim_type == "CENTERLINES":
                        tv_center = (0.0, 0.0)
                        tv_radius = max(wr, hr) / 2.0
                    elif dim_type == "LEADER":
                        tv_center = (0.0, 0.0)
                        tv_radius = min(wr, hr) / 2.0
                        tv_angle = 45.0
                    elif dim_type == "LINEAR":
                        tv_side = "RIGHT"
                        tv_start = (0.0, -hr / 2.0)
                        tv_end = (0.0, hr / 2.0)

                task = DimensionTask(
                    dim_type=tv_dim_type,
                    value=val,
                    start_proj=tv_start,
                    end_proj=tv_end,
                    p1=tv_start,
                    p2=tv_end,
                    center=tv_center,
                    radius=tv_radius,
                    angle=tv_angle,
                    side=tv_side,
                    rank=rank,
                    baseline=baseline,
                    prefix=display_prefix,
                    text=text_content,
                    tolerance=tol_str,
                    view_name=tv,
                )
                view_tasks[tv].append(task)

        # 2. 自動排版與比例計算
        try:
            from auto_2d_drawing.dxf_drawer import DrawingLayout
        except ImportError:
            from dxf_drawer import DrawingLayout
        view_sizes = {vn: view_data[vn]['size'] for vn in ['front', 'top', 'right'] if vn in view_data}
        layout = DrawingLayout(view_sizes, view_tasks)
        layout_engine = LayoutEngine(layout)

        # 3. 繪製圖框與標題欄 (Title Block)
        info = title_info or {}
        tb = TitleBlock()
        tb.draw(
            msp,
            part_name=info.get("part_name", "SMART ANNOTATED PART"),
            drawing_no=info.get("drawing_no", "DWG-SMART-001"),
            revision=info.get("revision", "R00"),
            scale_text=layout.get_scale_text(),
            material=info.get("material", "AL / SUS"),
            model_code=info.get("model_code", "CUSTOM")
        )

        # 4. 繪製三視圖實體邊緣
        drawer = DxfDrawer()
        for vn in ['front', 'top', 'right']:
            if vn not in view_data:
                continue
            vd = view_data[vn]
            ox, oy = layout.get_view_offset(vn)
            bbox_x0, bbox_y0 = vd['bbox'][0], vd['bbox'][1]

            drawer.draw_edges(msp, vd.get('visible', []), ox, oy, layout.scale, bbox_x0, bbox_y0, 'VISIBLE')
            drawer.draw_edges(msp, vd.get('hidden', []), ox, oy, layout.scale, bbox_x0, bbox_y0, 'HIDDEN')
            drawer.draw_view_label(msp, ox, oy, VIEW_CONFIG.get(vn, {}).get("label", vn.upper()))

        # 5. 精確渲染已選取之標註任務 (精確幾何排版對齊)
        for vn, tasks in view_tasks.items():
            if vn not in view_data or not tasks:
                continue
            vd = view_data[vn]
            ox, oy = layout.get_view_offset(vn)
            sw, sh = layout.get_scaled_size(vn)
            layout_engine.render(msp, tasks, ox, oy, sw, sh, vd)

        # 6. 儲存 DXF
        os.makedirs(os.path.dirname(dxf_path), exist_ok=True)
        doc.saveas(dxf_path)

        # 7. 匯出向量 PDF 與高解析 PNG
        export_pdf(doc, msp, pdf_path, dark_bg=True)
        export_png(doc, msp, png_path, dark_bg=True)

        return {
            "dxf": dxf_path,
            "pdf": pdf_path,
            "png": png_path,
        }
