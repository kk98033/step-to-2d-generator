"""
2D DXF 公差與尺寸抽取器 (DXF Tolerance Extractor)
=============================================================================
功能:
1. 使用 ezdxf 深度解析 DXF 檔案中的 DIMENSION, MTEXT, TEXT, LEADER 實體。
2. 自動解碼 AutoCAD 特殊公差格式:
   - 堆疊公差: \\S+0.040^0.000; 或 {\\H0.7x;\\S+0.02^-0.01;}
   - 對稱公差: %%p0.05 或 ±0.05 (%%P)
   - ISO 配合代號: h6, p6, g6, js6, h11, H7, H8 等
   - 前綴符號: Φ, Ø, R, C, T=, PCD
3. 輸出結構化 2D 尺寸標註清單，供 CAD-RAG 歷史知識庫關聯使用。
=============================================================================
"""

import os
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field
import ezdxf


@dataclass
class ExtractedDimension:
    """從 2D DXF 提取出的單一尺寸與公差記錄"""
    dim_type: str                      # LINEAR, DIAMETER, RADIAL, LEADER, ANGULAR, NOTE_DIM
    nominal_value: float               # 名義尺寸 (e.g. 3.00, 21.40)
    raw_text: str                      # DXF 原始文字 (e.g. "%%p0.05", "Φ3.00 h6")
    prefix: str                        # "Φ", "R", "C", "T=", ""
    tolerance_config: Dict[str, Any]   # {"mode": "FIT", "fit_class": "h6", "upper_dev": 0.0, "lower_dev": -0.006}
    points: Dict[str, List[float]]     # {"defpoint": [x, y], "text_pos": [x, y]}
    layer: str                         # 圖層名稱
    drawing_file: str                  # 來源圖檔檔名
    entity_handle: str = ""            # DXF entity handle，用於回查與畫面高亮
    source_entity_type: str = ""       # DIMENSION / MTEXT / TEXT
    dimension_category: str = "UNKNOWN"
    validation_status: str = "REVIEW_REQUIRED"
    extraction_confidence: float = 0.0
    is_feature_dimension: bool = False
    validation_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DxfToleranceExtractor:
    """
    DXF 尺寸與公差提取引擎
    """
    # 常用 ISO 配合等級匹配正則 (僅限標準等級 5~13: h6, p6, g6, js6, h11, H7, H8 等)
    FIT_PATTERN = re.compile(r'\b([hpgjsHPGJS](?:[5-9]|1[0-3]))\b')
    # AutoCAD 堆疊公差正則: \S+0.040^0; 或 \S+0.02^-0.01;
    STACK_TOL_PATTERN = re.compile(r'\\S([+-]?[0-9.]+)\^([+-]?[0-9.]*);?')
    # 對稱公差正則: %%p0.05 或 ±0.05 (不區分大小寫 %%P)
    SYM_TOL_PATTERN = re.compile(r'(?:%%[pP]|±|\+\/-)\s*([0-9.]+)', re.IGNORECASE)
    # 雙向上下偏差正則: +0.040/-0.000 或 (+0.040/0.000)
    DUAL_TOL_PATTERN = re.compile(r'[\(]?([+-][0-9.]+)\s*[\/|\^]\s*([+-]?[0-9.]+)[\)]?')

    TITLE_BLOCK_LAYER_TOKENS = (
        "圖框", "图框", "title", "border", "frame", "format", "sheet",
    )
    NON_DIMENSION_TEXT_TOKENS = (
        "kg", "rpm", "watt", "tdp", "rohs", "cpu", "vga", "fan",
        "weight", "重量", "轉速", "转速", "熱阻", "热阻", "瓦數", "瓦数",
    )

    @staticmethod
    def _strip_autocad_formatting(text: str) -> str:
        """移除 AutoCAD MTEXT 格式標籤 (如 {\\f...;}, \\P, \\C2; 等)"""
        cleaned = re.sub(r'\\f[^;]+;', '', text)
        cleaned = re.sub(r'\\[A-Za-z0-9_]+(?:;|\s)?', '', cleaned)
        cleaned = cleaned.replace('{', '').replace('}', '').strip()
        return cleaned

    def extract_from_file(self, dxf_path: str, include_rejected: bool = False) -> List[ExtractedDimension]:
        """從 DXF 檔案提取所有尺寸與公差"""
        if not os.path.exists(dxf_path):
            return []

        try:
            doc = ezdxf.readfile(dxf_path)
            msp = doc.modelspace()
        except Exception:
            return []

        extracted_list: List[ExtractedDimension] = []
        file_name = os.path.basename(dxf_path)

        for entity in msp:
            e_type = entity.dxftype()
            if e_type in ('DIMENSION', 'ARC_DIMENSION', 'RADIAL_DIMENSION', 'DIAMETER_DIMENSION'):
                dim_item = self._parse_dimension_entity(entity, file_name)
                if dim_item and (include_rejected or dim_item.validation_status != "REJECTED"):
                    extracted_list.append(dim_item)
            elif e_type in ('MTEXT', 'TEXT'):
                dim_item = self._parse_text_entity(entity, file_name)
                if dim_item and (include_rejected or dim_item.validation_status != "REJECTED"):
                    extracted_list.append(dim_item)

        return extracted_list

    # =========================================================================
    # 解析 DIMENSION 實體
    # =========================================================================
    def _parse_dimension_entity(self, entity, file_name: str) -> Optional[ExtractedDimension]:
        raw_text = getattr(entity.dxf, 'text', '')
        # ``actual_measurement`` is not a reliable DXF attribute in ezdxf.
        # Associative dimensions must be measured from their definition points.
        try:
            meas_val = float(entity.get_measurement())
        except (AttributeError, TypeError, ValueError):
            meas_val = float(getattr(entity.dxf, 'actual_measurement', 0.0) or 0.0)
        layer = getattr(entity.dxf, 'layer', '0')

        # 取得錨定座標點
        defpoint = getattr(entity.dxf, 'defpoint', (0.0, 0.0, 0.0))
        defpoint2 = getattr(entity.dxf, 'defpoint2', (0.0, 0.0, 0.0))
        text_mid = getattr(entity.dxf, 'text_midpoint', (0.0, 0.0, 0.0))

        points = {
            "defpoint": [round(defpoint.x, 3), round(defpoint.y, 3)],
            "defpoint2": [round(defpoint2.x, 3), round(defpoint2.y, 3)] if hasattr(defpoint2, 'x') else [0.0, 0.0],
            "text_mid": [round(text_mid.x, 3), round(text_mid.y, 3)] if hasattr(text_mid, 'x') else [0.0, 0.0],
        }

        # 判斷尺寸類型
        dim_type_int = getattr(entity.dxf, 'dimtype', 0) & 7
        if dim_type_int in (0, 1):
            dim_type = "LINEAR"
        elif dim_type_int == 3:
            dim_type = "DIAMETER"
        elif dim_type_int == 4:
            dim_type = "RADIAL"
        elif dim_type_int == 2:
            dim_type = "ANGULAR"
        else:
            dim_type = "LINEAR"

        # 解析文字中的名義值、前綴與公差
        prefix, nominal, tol_cfg = self._decode_tolerance_string(raw_text, fallback_val=meas_val)
        native_tol = self._read_dimension_style_tolerance(entity)
        if tol_cfg.get("mode") == "NONE" and native_tol:
            tol_cfg = native_tol

        reasons = ["DXF DIMENSION 實體提供實測值與定義點"]
        if native_tol:
            reasons.append("Tolerance deviations were read from DIMSTYLE/XDATA overrides.")
        status = "AUTO_VALIDATED" if meas_val > 0.0 else "REVIEW_REQUIRED"
        confidence = 0.95 if meas_val > 0.0 else 0.55
        if tol_cfg.get("mode") == "NONE":
            status = "NO_TOLERANCE"
            confidence = min(confidence, 0.80)
        if dim_type == "ANGULAR":
            category = "ANGULAR"
        elif dim_type == "DIAMETER" or prefix == "Φ":
            category = "DIAMETER"
        elif dim_type == "RADIAL" or prefix == "R":
            category = "RADIUS"
        elif prefix == "C":
            category = "CHAMFER"
        else:
            category = "LINEAR"

        return ExtractedDimension(
            dim_type=dim_type,
            nominal_value=round(nominal, 3),
            raw_text=raw_text,
            prefix=prefix,
            tolerance_config=tol_cfg,
            points=points,
            layer=layer,
            drawing_file=file_name,
            entity_handle=str(getattr(entity.dxf, 'handle', '') or ''),
            source_entity_type=entity.dxftype(),
            dimension_category=category,
            validation_status=status,
            extraction_confidence=confidence,
            is_feature_dimension=True,
            validation_reasons=reasons,
        )

    @staticmethod
    def _read_dimension_style_tolerance(entity) -> Optional[Dict[str, Any]]:
        """Read native AutoCAD DIMTOL/DIMTP/DIMTM tolerance overrides."""
        try:
            override = entity.override()
            if not bool(override.get("dimtol", 0)):
                return None
            upper = abs(float(override.get("dimtp", 0.0) or 0.0))
            lower_mag = abs(float(override.get("dimtm", upper) or upper))
            return {
                "mode": "CUSTOM_LIMITS",
                "upper_dev": upper,
                "lower_dev": -lower_mag,
                "source": "DXF_DIMSTYLE",
            }
        except (AttributeError, TypeError, ValueError):
            return None

    # =========================================================================
    # 解析 TEXT / MTEXT 實體 (若包含獨立標註或公差)
    # =========================================================================
    def _parse_text_entity(self, entity, file_name: str) -> Optional[ExtractedDimension]:
        text_str = getattr(entity.dxf, 'text', '')
        if not text_str or len(text_str) < 2:
            return None

        # 過濾純備註文字，只挑選含有尺寸與公差特徵的文字
        has_dim_feature = any(sym in text_str for sym in ['Φ', 'Ø', '%%p', '%%P', '±', 'h6', 'p6', 'H7', 'C0.', 'R0.', 'T='])
        if not has_dim_feature:
            return None

        insert_pt = getattr(entity.dxf, 'insert', (0.0, 0.0, 0.0))
        points = {
            "defpoint": [round(insert_pt.x, 3), round(insert_pt.y, 3)],
            "text_mid": [round(insert_pt.x, 3), round(insert_pt.y, 3)]
        }

        prefix, nominal, tol_cfg = self._decode_tolerance_string(text_str, fallback_val=0.0)
        if nominal <= 0.0 and tol_cfg.get("mode") == "NONE":
            return None
        # MTEXT control codes (for example ``\C256``) can resemble chamfer
        # notation.  Plain notes without an actual tolerance or dimensional
        # prefix are outside this extractor's scope.
        if tol_cfg.get("mode") == "NONE" and not prefix:
            return None

        clean_text = self._strip_autocad_formatting(text_str)
        clean_lower = clean_text.lower()
        layer = str(getattr(entity.dxf, 'layer', '0') or '0')
        layer_lower = layer.lower()
        reasons: List[str] = []

        contains_non_dimensional_context = any(token in clean_lower for token in self.NON_DIMENSION_TEXT_TOKENS)
        is_long_note = len(clean_text) > 160 or clean_text.count('\\P') >= 2 or text_str.count('\\P') >= 2
        if contains_non_dimensional_context or is_long_note:
            reasons.append("文字包含重量、轉速、功率或長篇 Notes，不可當作特徵尺寸")
            status = "REJECTED"
            confidence = 0.05
            is_feature = False
            category = "NON_DIMENSION_NOTE"
        else:
            nominal_without_tol = self._extract_nominal_outside_tolerance(text_str)
            only_tolerance_token = nominal_without_tol is None
            in_title_block = any(token in layer_lower for token in self.TITLE_BLOCK_LAYER_TOKENS)

            if only_tolerance_token:
                # %%P0.25 這類字串是圖面一般公差值，不是「0.25 ±0.25」特徵尺寸。
                nominal = 0.0
                status = "DRAWING_DEFAULT"
                confidence = 0.90 if in_title_block else 0.70
                is_feature = False
                category = "GENERAL_TOLERANCE_NOTE"
                reasons.append("只含公差值、沒有名義尺寸，归類為圖面一般公差")
            else:
                nominal = nominal_without_tol
                category = self._classify_text_dimension(prefix, clean_text)
                status = "AUTO_VALIDATED" if tol_cfg.get("mode") != "NONE" else "REVIEW_REQUIRED"
                confidence = 0.82 if tol_cfg.get("mode") != "NONE" else 0.55
                is_feature = True
                reasons.append("文字同時具有獨立名義尺寸與公差語法")

        return ExtractedDimension(
            dim_type="NOTE_DIM",
            nominal_value=round(nominal, 3),
            raw_text=text_str,
            prefix=prefix,
            tolerance_config=tol_cfg,
            points=points,
            layer=layer,
            drawing_file=file_name,
            entity_handle=str(getattr(entity.dxf, 'handle', '') or ''),
            source_entity_type=entity.dxftype(),
            dimension_category=category,
            validation_status=status,
            extraction_confidence=confidence,
            is_feature_dimension=is_feature,
            validation_reasons=reasons,
        )

    def _extract_nominal_outside_tolerance(self, text: str) -> Optional[float]:
        """只從公差語法之外取出名義尺寸，避免將 %%P0.25 的 0.25 當成名義值。"""
        candidate = self._strip_autocad_formatting(text).replace("<>", " ")
        candidate = self.STACK_TOL_PATTERN.sub(" ", candidate)
        candidate = self.DUAL_TOL_PATTERN.sub(" ", candidate)
        candidate = self.SYM_TOL_PATTERN.sub(" ", candidate)
        candidate = self.FIT_PATTERN.sub(" ", candidate)
        candidate = re.sub(r'(?i)(mm|deg|°)', ' ', candidate)
        candidate = candidate.replace("Φ", " ").replace("Ø", " ")
        candidate = re.sub(r'(?<![A-Za-z])[RCT]=?', ' ', candidate)
        matches = re.findall(r'(?<![A-Za-z%])([0-9]+(?:\.[0-9]+)?)(?![A-Za-z%])', candidate)
        if not matches:
            return None
        try:
            return float(matches[0])
        except ValueError:
            return None

    @staticmethod
    def _classify_text_dimension(prefix: str, clean_text: str) -> str:
        if prefix == "Φ":
            return "DIAMETER"
        if prefix == "R":
            return "RADIUS"
        if prefix == "C":
            return "CHAMFER"
        if "°" in clean_text or "deg" in clean_text.lower():
            return "ANGULAR"
        return "LINEAR"

    # =========================================================================
    # 公差字串解碼器 (Tolerance String Decoder)
    # =========================================================================
    def _decode_tolerance_string(self, text: str, fallback_val: float = 0.0) -> Tuple[str, float, Dict[str, Any]]:
        """
        將 DXF 文字解析為 (prefix, nominal_value, tolerance_config)
        """
        prefix = ""
        nominal = fallback_val
        clean_text = self._strip_autocad_formatting(text)
        clean_text = clean_text.replace("<>", f"{fallback_val:.2f}" if fallback_val > 0 else "")

        # 1. 提取前綴
        if "Φ" in clean_text or "Ø" in clean_text or "%%c" in clean_text.lower():
            prefix = "Φ"
        elif clean_text.strip().startswith("R") or " R" in clean_text:
            prefix = "R"
        elif clean_text.strip().startswith("C") or " C" in clean_text:
            prefix = "C"
        elif "T=" in clean_text:
            prefix = "T="

        # 2. 提取名義尺寸 (若 text 中含有數值)
        num_matches = re.findall(r'([0-9]+\.[0-9]+|[0-9]+)', clean_text)
        if num_matches and fallback_val <= 0.01:
            try:
                nominal = float(num_matches[0])
            except ValueError:
                pass

        # 3. 提取公差模式 (優先匹配對稱與上下偏差，再匹配 ISO 配合等級)
        tol_config = {"mode": "NONE"}

        # 3.1 檢查對稱公差 (e.g. %%p0.05, %%P10 或 ±0.05)
        sym_match = self.SYM_TOL_PATTERN.search(text)
        if sym_match:
            try:
                dev_val = float(sym_match.group(1))
                tol_config = {
                    "mode": "CUSTOM_SYMMETRIC",
                    "dev": dev_val
                }
                return prefix, nominal, tol_config
            except ValueError:
                pass

        # 3.2 檢查 AutoCAD 堆疊公差 (e.g. \S+0.040^0;)
        stack_match = self.STACK_TOL_PATTERN.search(text)
        if stack_match:
            try:
                u_str = stack_match.group(1)
                l_str = stack_match.group(2) if stack_match.group(2) else "0.0"
                u_val = float(u_str)
                l_val = float(l_str)
                tol_config = {
                    "mode": "CUSTOM_LIMITS",
                    "upper_dev": u_val,
                    "lower_dev": l_val
                }
                return prefix, nominal, tol_config
            except ValueError:
                pass

        # 3.3 檢查雙向上下偏差 (e.g. +0.040/-0.000)
        dual_match = self.DUAL_TOL_PATTERN.search(clean_text)
        if dual_match:
            try:
                u_val = float(dual_match.group(1))
                l_val = float(dual_match.group(2))
                tol_config = {
                    "mode": "CUSTOM_LIMITS",
                    "upper_dev": u_val,
                    "lower_dev": l_val
                }
                return prefix, nominal, tol_config
            except ValueError:
                pass

        # 3.4 檢查 ISO 配合代號 (e.g. h6, p6, H7)
        fit_match = self.FIT_PATTERN.search(clean_text)
        if fit_match:
            fit_cls = fit_match.group(1)
            is_hole = fit_cls.isupper()
            tol_config = {
                "mode": "FIT",
                "fit_class": fit_cls,
                "is_hole": is_hole
            }
            return prefix, nominal, tol_config

        return prefix, nominal, tol_config
