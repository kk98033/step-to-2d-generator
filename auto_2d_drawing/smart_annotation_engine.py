"""
新版智慧特徵導向標註引擎 (Smart Annotation Engine & Preset System)
獨立模組 — 完全不影響舊版標註與產圖流程

功能:
  1. TemplateManager: 樣板管理器 (載入、儲存、刪除、比對與一鍵套用風格化樣板)
  2. SmartAnnotationEngine: 由候選標註規則提取器與尺寸排版引擎組成，實現高精度幾何對齊與客製化出圖
"""
import os
import re
import json
import uuid
from typing import Dict, List, Any, Optional

try:
    from auto_2d_drawing.config import TEMPLATES_DIR
    from auto_2d_drawing.smart_extractors.smart_rule_engine import SmartRuleExtractor, SmartDimensionEngine
except ImportError:
    from config import TEMPLATES_DIR
    from smart_extractors.smart_rule_engine import SmartRuleExtractor, SmartDimensionEngine


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

    def match_and_apply(self, template: Dict[str, Any], candidate_rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        將樣板規則動態比對至候選規則清單，全自動填入勾選狀態、視圖與公差偏好
        """
        rules = template.get("rules", [])
        updated_rules = []

        for item in candidate_rules:
            item_copy = dict(item)
            cat = str(item.get("category", "")).lower()
            dim_type = str(item.get("dim_type", "")).lower()
            rule_id = str(item.get("rule_id", "")).lower()
            name = str(item.get("name", "")).lower()

            matched = False
            for r in rules:
                target_type = str(r.get("feature_type", "")).lower()
                role_pattern = r.get("role_pattern")

                # 比對類別或維度類型
                match_cat = (
                    not target_type or
                    target_type == 'all' or
                    target_type in cat or
                    cat in target_type or
                    target_type in dim_type or
                    target_type in rule_id
                )

                if match_cat:
                    if role_pattern:
                        if not (re.search(role_pattern, name, re.IGNORECASE) or re.search(role_pattern, rule_id, re.IGNORECASE)):
                            continue

                    item_copy["enabled"] = r.get("enabled", True)
                    if "preferred_view" in r:
                        item_copy["preferred_view"] = r["preferred_view"]
                    if "tolerance" in r:
                        item_copy["tolerance"] = r["tolerance"]
                    if "side" in r:
                        item_copy["side"] = r["side"]
                    matched = True
                    break

            if not matched:
                item_copy["enabled"] = False

            updated_rules.append(item_copy)

        return updated_rules


class SmartAnnotationEngine:
    """新版智慧特徵導向標註引擎（薄 Orchestrator）"""

    def __init__(self):
        self.engine = SmartDimensionEngine()

    def get_candidate_rules(self, shape, view_data: Dict[str, Any], part_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """提取該零件之所有候選標註規則"""
        return self.engine.get_candidate_rules(shape, view_data, part_type)

    def render_custom_drawing(
        self,
        dxf_path: str,
        pdf_path: str,
        png_path: str,
        feature_records: List[Dict[str, Any]],
        view_data: Dict[str, Any],
        title_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """渲染已選規則為 DXF / PDF / SVG / PNG"""
        return self.engine.render_custom_drawing(
            dxf_path=dxf_path,
            pdf_path=pdf_path,
            png_path=png_path,
            configured_rules=feature_records,
            view_data=view_data,
            title_info=title_info
        )
