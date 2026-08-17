"""
新版智慧特徵導向標註引擎 (Smart Annotation Engine & Preset System)
獨立模組 — 完全不影響舊版標註與產圖流程

功能:
  1. TemplateManager: 樣板管理器 (載入、儲存、刪除、比對與一鍵套用風格化樣板)
  2. SmartAnnotationEngine: 由使用者自選之 3D 特徵與公差組態，即時轉換為 2D 視圖標註任務並排版渲染 DXF/PDF/PNG
"""
import os
import re
import json
import uuid
import math
from typing import Dict, List, Any, Optional

try:
    from auto_2d_drawing.config import TEMPLATES_DIR, OUTPUT_DIR, DIM_STYLE, VIEW_CONFIG
    from auto_2d_drawing.dimension_task import DimensionTask
    from auto_2d_drawing.layout_engine import LayoutEngine
    from auto_2d_drawing.dxf_drawer import DrawingLayout, DxfDrawer
    from auto_2d_drawing.title_block import TitleBlock, setup_document
    from auto_2d_drawing.pdf_exporter import export_pdf, export_png
except ImportError:
    from config import TEMPLATES_DIR, OUTPUT_DIR, DIM_STYLE, VIEW_CONFIG
    from dimension_task import DimensionTask
    from layout_engine import LayoutEngine
    from dxf_drawer import DrawingLayout, DxfDrawer
    from title_block import TitleBlock, setup_document
    from pdf_exporter import export_pdf, export_png


class TemplateManager:
    """樣板管理器：負責管理標註風格偏好樣板"""

    def __init__(self, templates_dir: Optional[str] = None):
        self.templates_dir = templates_dir or TEMPLATES_DIR
        if not os.path.exists(self.templates_dir):
            os.makedirs(self.templates_dir, exist_ok=True)

    def list_templates(self) -> List[Dict[str, Any]]:
        """列出所有可用樣板"""
        templates = []
        if not os.path.exists(self.templates_dir):
            return templates

        for filename in sorted(os.listdir(self.templates_dir)):
            if filename.lower().endswith(".json"):
                file_path = os.path.join(self.templates_dir, filename)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if "id" not in data:
                        data["id"] = os.path.splitext(filename)[0]
                    templates.append(data)
                except Exception as e:
                    print(f"Error loading template {filename}: {e}")
        return templates

    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """取得單一樣板"""
        file_path = os.path.join(self.templates_dir, f"{template_id}.json")
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def save_template(self, template_data: Dict[str, Any]) -> Dict[str, Any]:
        """儲存或建立新樣板"""
        template_id = template_data.get("id")
        if not template_id:
            name_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', template_data.get("name", "custom_preset")).lower()
            template_id = f"{name_slug}_{uuid.uuid4().hex[:6]}"
            template_data["id"] = template_id

        file_path = os.path.join(self.templates_dir, f"{template_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(template_data, f, indent=2, ensure_ascii=False)
        return template_data

    def delete_template(self, template_id: str) -> bool:
        """刪除自訂樣板 (預設標準樣板不可刪除)"""
        if template_id.endswith("_standard_preset"):
            raise ValueError("系統內建標準樣板不可刪除")
        file_path = os.path.join(self.templates_dir, f"{template_id}.json")
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False

    def match_and_apply(self, template: Dict[str, Any], feature_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        將樣板規則動態比對至特定特徵清單，全自動填入勾選狀態、視圖與公差偏好
        """
        rules = template.get("rules", [])
        updated_records = []

        for feat in feature_records:
            feat_copy = dict(feat)
            feat_type = feat.get("type", "")
            feat_role = str(feat.get("role", ""))
            matched = False

            for rule in rules:
                rule_type = rule.get("feature_type", "")
                role_pattern = rule.get("role_pattern")

                # 比對特徵類型
                if rule_type and rule_type != feat_type:
                    continue

                # 比對角色子模式
                if role_pattern and not re.search(role_pattern, feat_role, re.IGNORECASE):
                    continue

                # 命中規則
                feat_copy["enabled"] = rule.get("enabled", True)
                feat_copy["preferred_view"] = rule.get("preferred_view", feat.get("view", "front"))
                feat_copy["dim_type"] = rule.get("dim_type", "LINEAR")
                feat_copy["tolerance"] = rule.get("tolerance", "")
                feat_copy["side"] = rule.get("side", "BOTTOM")
                feat_copy["baseline"] = rule.get("baseline", "NONE")
                feat_copy["rank"] = rule.get("rank", 1)
                matched = True
                break

            if not matched:
                feat_copy["enabled"] = False
                feat_copy["preferred_view"] = feat.get("view", "front")
                feat_copy["tolerance"] = ""

            updated_records.append(feat_copy)

        return updated_records


class SmartAnnotationEngine:
    """新版智慧特徵導向標註引擎"""

    def __init__(self, layout: Optional[DrawingLayout] = None):
        self.layout = layout
        self.layout_engine = LayoutEngine(layout) if layout else None

    def convert_features_to_tasks(self, feature_records: List[Dict[str, Any]], view_data: Dict[str, Any]) -> Dict[str, List[DimensionTask]]:
        """
        將使用者選定的特徵清單轉換為各視圖的 DimensionTask
        """
        view_tasks: Dict[str, List[DimensionTask]] = {
            "front": [], "back": [], "top": [], "right": [], "left": []
        }

        for feat in feature_records:
            # 檢查是否啟用
            if not feat.get("enabled", False):
                continue

            target_view = feat.get("preferred_view", feat.get("view", "front"))
            if target_view not in view_tasks:
                target_view = "front"

            vd = view_data.get(target_view, {})
            bbox = vd.get("bbox", (0, 0, 100, 100))
            sw = bbox[2] - bbox[0]
            sh = bbox[3] - bbox[1]

            f_type = feat.get("type", "")
            f_nom = feat.get("nominal", {})
            f_geom = feat.get("geometry", {})
            tol_str = feat.get("tolerance", "")
            custom_side = feat.get("side", "BOTTOM")
            custom_baseline = feat.get("baseline", "NONE")
            rank = feat.get("rank", 1)

            # 1. 整體包絡尺寸 (Overall Size)
            if f_type == "overall_size":
                w = float(f_nom.get("W", f_nom.get("outer_diameter", sw)))
                h = float(f_nom.get("H", f_nom.get("height", sh)))
                view_tasks[target_view].append(
                    DimensionTask(
                        dim_type="LINEAR",
                        value=w,
                        start_proj=(0.0, 0.0),
                        end_proj=(w, 0.0),
                        p1=(0.0, 0.0),
                        p2=(w, 0.0),
                        side="BOTTOM",
                        baseline="NONE",
                        rank=2,
                        text=f"{w:.2f}{(' ' + tol_str) if tol_str else ''}",
                        tolerance=tol_str,
                    )
                )
                view_tasks[target_view].append(
                    DimensionTask(
                        dim_type="LINEAR",
                        value=h,
                        start_proj=(w, 0.0),
                        end_proj=(w, h),
                        p1=(w, 0.0),
                        p2=(w, h),
                        side="RIGHT",
                        baseline="NONE",
                        rank=2,
                        text=f"{h:.2f}{(' ' + tol_str) if tol_str else ''}",
                        tolerance=tol_str,
                    )
                )

            # 2. 基準軸心線 (Datum Axis)
            elif f_type == "datum":
                view_tasks[target_view].append(
                    DimensionTask(
                        dim_type="CENTERLINES",
                        value=0.0,
                        start_proj=(0.0, -sh/2.0),
                        end_proj=(0.0, sh/2.0),
                        p1=(0.0, -sh/2.0),
                        p2=(0.0, sh/2.0),
                        side="TOP",
                        baseline="NONE",
                        rank=1,
                        text="基準A (Datum A)",
                        center=(0.0, 0.0),
                    )
                )

            # 3. 圓柱孔特徵 (Holes)
            elif f_type == "hole":
                dia = float(f_nom.get("diameter", f_geom.get("diameter", 0.0)))
                c = f_geom.get("center", [0.0, 0.0, 0.0])
                p_y = float(c[1]) if len(c) > 1 else 0.0

                label = f"Ø{dia:.2f}{(' ' + tol_str) if tol_str else ''}"
                view_tasks[target_view].append(
                    DimensionTask(
                        dim_type="DIAMETER",
                        value=dia,
                        start_proj=(-dia/2.0, p_y),
                        end_proj=(dia/2.0, p_y),
                        p1=(-dia/2.0, p_y),
                        p2=(dia/2.0, p_y),
                        side="LEFT",
                        baseline="NONE",
                        rank=rank,
                        text=label,
                        tolerance=tol_str,
                    )
                )

            # 4. 圓柱軸與凸台特徵 (Shafts / Bosses)
            elif f_type == "shaft_or_boss":
                dia = float(f_nom.get("diameter", f_geom.get("diameter", 0.0)))
                c = f_geom.get("center", [0.0, 0.0, 0.0])
                p_y = float(c[1]) if len(c) > 1 else 0.0

                label = f"Ø{dia:.2f}{(' ' + tol_str) if tol_str else ''}"
                view_tasks[target_view].append(
                    DimensionTask(
                        dim_type="DIAMETER",
                        value=dia,
                        start_proj=(-dia/2.0, p_y),
                        end_proj=(dia/2.0, p_y),
                        p1=(-dia/2.0, p_y),
                        p2=(dia/2.0, p_y),
                        side="LEFT" if custom_side == "LEFT" else "RIGHT",
                        baseline="NONE",
                        rank=rank,
                        text=label,
                        tolerance=tol_str,
                    )
                )

            # 5. PCD 圓周孔群陣列 (Hole Patterns)
            elif f_type == "hole_pattern":
                count = f_nom.get("count", 4)
                dia = float(f_nom.get("diameter", 3.0))
                pcd = float(f_nom.get("pcd", 30.0))
                label = f"{count}-Ø{dia:.2f} PCD {pcd:.2f}{(' ' + tol_str) if tol_str else ''}"
                view_tasks[target_view].append(
                    DimensionTask(
                        dim_type="DIAMETER",
                        value=pcd,
                        start_proj=(-pcd/2.0, 0.0),
                        end_proj=(pcd/2.0, 0.0),
                        p1=(-pcd/2.0, 0.0),
                        p2=(pcd/2.0, 0.0),
                        side="RIGHT",
                        baseline="NONE",
                        rank=rank,
                        text=label,
                        center=(0.0, 0.0),
                        tolerance=tol_str,
                    )
                )

            # 6. 圓錐、倒角、沉頭 (Cones / Chamfers)
            elif f_type == "cone_or_chamfer":
                min_d = float(f_nom.get("min_diameter", 0.0))
                max_d = float(f_nom.get("max_diameter", 0.0))
                semi_ang = float(f_nom.get("included_angle", 90.0)) / 2.0
                c = f_geom.get("center", [0.0, 0.0, 0.0])
                p_y = float(c[1]) if len(c) > 1 else 0.0

                label = f"C{max(0.2, (max_d - min_d)/2.0):.2f} ({semi_ang:.0f}°){(' ' + tol_str) if tol_str else ''}"
                view_tasks[target_view].append(
                    DimensionTask(
                        dim_type="LEADER",
                        value=max_d,
                        start_proj=(max_d/2.0, p_y),
                        end_proj=(max_d/2.0 + 5.0, p_y + 5.0),
                        p1=(max_d/2.0, p_y),
                        p2=(max_d/2.0 + 5.0, p_y + 5.0),
                        side="TOP",
                        baseline="NONE",
                        rank=rank,
                        text=label,
                        tolerance=tol_str,
                    )
                )

            # 7. 環形槽與卡簧槽 (Toruses / Grooves)
            elif f_type == "groove_or_slot":
                maj_d = float(f_nom.get("major_diameter", 0.0))
                min_r = float(f_nom.get("minor_radius", 0.5))
                c = f_geom.get("center", [0.0, 0.0, 0.0])
                p_y = float(c[1]) if len(c) > 1 else 0.0

                label = f"槽寬 {min_r*2.0:.2f} (Ø{maj_d:.2f}){(' ' + tol_str) if tol_str else ''}"
                view_tasks[target_view].append(
                    DimensionTask(
                        dim_type="LINEAR",
                        value=min_r * 2.0,
                        start_proj=(-maj_d/2.0, p_y - min_r),
                        end_proj=(-maj_d/2.0, p_y + min_r),
                        p1=(-maj_d/2.0, p_y - min_r),
                        p2=(-maj_d/2.0, p_y + min_r),
                        side="LEFT",
                        baseline=custom_baseline,
                        rank=rank,
                        text=label,
                        tolerance=tol_str,
                    )
                )

            # 8. 階梯段差 (Steps)
            elif f_type == "step":
                length = float(f_nom.get("length", 0.0))
                pos = float(f_nom.get("position", 0.0))

                label = f"階梯長 {length:.2f}{(' ' + tol_str) if tol_str else ''}"
                view_tasks[target_view].append(
                    DimensionTask(
                        dim_type="LINEAR",
                        value=length,
                        start_proj=(0.0, pos),
                        end_proj=(0.0, pos + length),
                        p1=(0.0, pos),
                        p2=(0.0, pos + length),
                        side="BOTTOM" if custom_side == "BOTTOM" else "RIGHT",
                        baseline=custom_baseline,
                        rank=rank,
                        text=label,
                        tolerance=tol_str,
                    )
                )

            # 9. 結構壁厚與板厚 (Wall Thicknesses)
            elif f_type == "wall_thickness":
                th = float(f_nom.get("thickness", 1.0))
                label = f"T={th:.2f}{(' ' + tol_str) if tol_str else ''}"
                view_tasks[target_view].append(
                    DimensionTask(
                        dim_type="LINEAR",
                        value=th,
                        start_proj=(0.0, 0.0),
                        end_proj=(0.0, th),
                        p1=(0.0, 0.0),
                        p2=(0.0, th),
                        side="BOTTOM",
                        baseline="NONE",
                        rank=rank,
                        text=label,
                        tolerance=tol_str,
                    )
                )

            # 10. 過渡圓角 (Fillets)
            elif f_type == "fillet_or_round":
                r = float(f_nom.get("radius", 0.5))
                c = f_geom.get("center", [0.0, 0.0, 0.0])
                label = f"R{r:.2f}{(' ' + tol_str) if tol_str else ''}"
                view_tasks[target_view].append(
                    DimensionTask(
                        dim_type="NOTE",
                        value=r,
                        start_proj=(float(c[0]), float(c[1])),
                        end_proj=(float(c[0]) + 4.0, float(c[1]) + 4.0),
                        p1=(float(c[0]), float(c[1])),
                        p2=(float(c[0]) + 4.0, float(c[1]) + 4.0),
                        side="TOP",
                        baseline="NONE",
                        rank=rank,
                        text=label,
                        tolerance=tol_str,
                    )
                )

        return view_tasks

    def render_custom_drawing(
        self,
        dxf_path: str,
        pdf_path: str,
        png_path: str,
        feature_records: List[Dict[str, Any]],
        view_data: Dict[str, Any],
        title_info: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """
        排版並渲染客製化工程圖 (產出 DXF, PDF, PNG)
        """
        doc = setup_document()
        msp = doc.modelspace()

        # 1. 轉換特徵為各視圖標註任務
        all_tasks = self.convert_features_to_tasks(feature_records, view_data)

        # 2. 自動計算圖面佈局與縮放比
        view_sizes = {vn: view_data[vn]['size'] for vn in ['front', 'top', 'right'] if vn in view_data}
        layout = DrawingLayout(view_sizes, all_tasks)
        self.layout = layout
        self.layout_engine = LayoutEngine(layout)

        # 3. 繪製圖框與標題欄
        title_block = TitleBlock()
        info = title_info or {}
        title_block.draw(
            msp,
            part_name=info.get("part_name", "CUSTOM PART"),
            drawing_no=info.get("drawing_no", "DWG-CUSTOM-001"),
            revision=info.get("revision", "R00"),
            scale_text=layout.get_scale_text(),
            material=info.get("material", "AL / SUS"),
            model_code=info.get("model_code", "CUSTOM")
        )

        # 4. 繪製三視圖邊緣線
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

        # 5. 渲染標註任務
        for vn, tasks in all_tasks.items():
            if vn not in view_data or not tasks:
                continue
            vd = view_data[vn]
            ox, oy = layout.get_view_offset(vn)
            sw, sh = layout.get_scaled_size(vn)
            self.layout_engine.render(msp, tasks, ox, oy, sw, sh, vd)

        # 6. 儲存 DXF
        os.makedirs(os.path.dirname(dxf_path), exist_ok=True)
        doc.saveas(dxf_path)

        # 7. 匯出 PDF 與 PNG
        export_pdf(doc, msp, pdf_path, dark_bg=True)
        export_png(doc, msp, png_path, dark_bg=True)

        return {
            "dxf": dxf_path,
            "pdf": pdf_path,
            "png": png_path,
        }
