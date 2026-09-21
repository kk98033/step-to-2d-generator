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
from dataclasses import dataclass, asdict
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

    @staticmethod
    def _strip_autocad_formatting(text: str) -> str:
        """移除 AutoCAD MTEXT 格式標籤 (如 {\\f...;}, \\P, \\C2; 等)"""
        cleaned = re.sub(r'\\f[^;]+;', '', text)
        cleaned = re.sub(r'\\[A-Za-z0-9_]+(?:;|\s)?', '', cleaned)
        cleaned = cleaned.replace('{', '').replace('}', '').strip()
        return cleaned

    def extract_from_file(self, dxf_path: str) -> List[ExtractedDimension]:
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
                if dim_item:
                    extracted_list.append(dim_item)
            elif e_type in ('MTEXT', 'TEXT'):
                dim_item = self._parse_text_entity(entity, file_name)
                if dim_item:
                    extracted_list.append(dim_item)

        return extracted_list

    # =========================================================================
    # 解析 DIMENSION 實體
    # =========================================================================
    def _parse_dimension_entity(self, entity, file_name: str) -> Optional[ExtractedDimension]:
        raw_text = getattr(entity.dxf, 'text', '')
        meas_val = float(getattr(entity.dxf, 'actual_measurement', 0.0))
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

        return ExtractedDimension(
            dim_type=dim_type,
            nominal_value=round(nominal, 3),
            raw_text=raw_text,
            prefix=prefix,
            tolerance_config=tol_cfg,
            points=points,
            layer=layer,
            drawing_file=file_name
        )

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

        return ExtractedDimension(
            dim_type="NOTE_DIM",
            nominal_value=round(nominal, 3),
            raw_text=text_str,
            prefix=prefix,
            tolerance_config=tol_cfg,
            points=points,
            layer=getattr(entity.dxf, 'layer', '0'),
            drawing_file=file_name
        )

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
