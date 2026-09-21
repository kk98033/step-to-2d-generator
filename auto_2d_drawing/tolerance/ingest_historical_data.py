"""
歷史圖檔資料集批次萃取與入庫管線 (Historical CAD Data Ingestion Pipeline)
=============================================================================
功能:
1. 遞迴掃描指定之歷史資料夾 (如 D:\\School\\力致\\力致_ref 與 new_data)。
2. 自動配對同名或相關聯之 3D STEP 模型與 2D DXF 工程圖。
3. 結合 3D 空間特徵關係圖 (FRG) 與 2D DXF 尺寸公差抽取器，進行 3D-2D 特徵級關聯配對。
4. 將配對成功且具備有效工程公差之歷史特徵自動萃取寫入案例庫 (feature_case_base.json)。
=============================================================================
"""

import os
import sys
import json
import math
from typing import List, Dict, Any, Tuple, Optional

# Ensure workspace root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_ws_root = os.path.abspath(os.path.join(_current_dir, "..", ".."))
if _ws_root not in sys.path:
    sys.path.insert(0, _ws_root)

from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone

from auto_2d_drawing.tolerance.feature_graph import FeatureGraphExtractor, FeatureRelationGraph, FeatureNode
from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor, ExtractedDimension
from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase


class HistoricalDataIngestor:
    """
    歷史 STEP-DXF 資料集批次萃取入庫引擎
    """
    def __init__(self, case_base: Optional[FeatureCaseBase] = None):
        self.case_base = case_base or FeatureCaseBase()
        self.frg_extractor = FeatureGraphExtractor()
        self.dxf_extractor = DxfToleranceExtractor()

    def scan_and_ingest_directories(self, search_dirs: List[str], max_models: int = 50) -> Dict[str, Any]:
        """
        掃描多個歷史資料夾並執行批次配對入庫
        """
        step_map: Dict[str, str] = {}
        dxf_map: Dict[str, str] = {}

        print(">>> 正在搜尋歷史 STEP 與 DXF 檔案...")
        for s_dir in search_dirs:
            if not os.path.exists(s_dir):
                print(f"Warning: Directory not found: {s_dir}")
                continue

            for root, dirs, files in os.walk(s_dir):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    base = os.path.splitext(f)[0].lower()
                    # 去除前綴如 "new_", "old_" 以便最大化配對
                    clean_base = base.replace("new_", "").replace("old_", "").strip()
                    full_p = os.path.join(root, f)

                    if ext in ('.stp', '.step'):
                        if clean_base not in step_map:
                            step_map[clean_base] = full_p
                    elif ext in ('.dxf',):
                        if clean_base not in dxf_map:
                            dxf_map[clean_base] = full_p

        print(f">>> 發現 3D STEP 檔案: {len(step_map)} 個 | 2D DXF 檔案: {len(dxf_map)} 個")

        # 找出同名或配對成功之檔案
        matched_pairs: List[Tuple[str, str, str]] = []
        for base_key, step_p in step_map.items():
            if base_key in dxf_map:
                matched_pairs.append((base_key, step_p, dxf_map[base_key]))

        print(f">>> 精確同名配對成功: {len(matched_pairs)} 組模型圖檔")

        # 若同名配對較少，追加部分 3D STEP 獨立特徵萃取
        processed_count = 0
        total_ingested_cases = 0
        error_count = 0

        for base_key, step_p, dxf_p in matched_pairs[:max_models]:
            try:
                print(f"[{processed_count + 1}/{min(len(matched_pairs), max_models)}] 正在處理: {os.path.basename(step_p)} <-> {os.path.basename(dxf_p)}")
                ingested = self._process_pair(step_p, dxf_p)
                total_ingested_cases += ingested
                processed_count += 1
            except Exception as e:
                print(f"  Error processing pair {base_key}: {e}")
                error_count += 1

        self.case_base.save_db()
        print(f"\n============================================================")
        print(f"  歷史資料萃取入庫完成!")
        print(f"  處理配對組數: {processed_count} 組 | 新增歷史特徵案例: {total_ingested_cases} 筆")
        print(f"  案例庫現有總案例數: {len(self.case_base.cases)} 筆")
        print(f"============================================================")

        return {
            "processed_pairs": processed_count,
            "new_cases": total_ingested_cases,
            "total_cases_in_db": len(self.case_base.cases),
            "errors": error_count
        }

    # =========================================================================
    # 處理單一 STEP-DXF 配對組
    # =========================================================================
    def _process_pair(self, step_path: str, dxf_path: str) -> int:
        reader = STEPControl_Reader()
        status = reader.ReadFile(step_path)
        if status != IFSelect_RetDone:
            return 0
        reader.TransferRoots()
        shape = reader.OneShape()

        # 1. 提取 3D FRG
        graph = self.frg_extractor.build_graph(shape)
        if not graph.nodes:
            return 0

        # 2. 提取 2D DXF 尺寸與公差
        dxf_dims = self.dxf_extractor.extract_from_file(dxf_path)
        if not dxf_dims:
            return 0

        # 3. 執行 3D-2D 幾何與數值配對 (Value & Context Matcher)
        new_cases_count = 0
        step_base = os.path.splitext(os.path.basename(step_path))[0]

        for node in graph.nodes:
            nom = node.nominal
            target_val = nom.get("diameter", nom.get("groove_diameter", nom.get("length", 0.0)))
            if target_val <= 0.01:
                continue

            # 在 2D DXF 中尋找名義尺寸接近之標註
            best_match: Optional[ExtractedDimension] = None
            best_diff = 0.08  # 容差 0.08mm 內

            for dim in dxf_dims:
                diff = abs(dim.nominal_value - target_val)
                if diff < best_diff:
                    # 優先挑選具備非 NONE 公差之標註
                    if best_match is None or (best_match.tolerance_config.get("mode") == "NONE" and dim.tolerance_config.get("mode") != "NONE"):
                        best_match = dim
                        best_diff = diff

            if best_match and best_match.tolerance_config.get("mode") != "NONE":
                case_id = f"HIST_{step_base}_{node.id}"
                new_case = ToleranceCase(
                    case_id=case_id,
                    part_type=graph.part_type,
                    feature_type=node.feature_type,
                    inferred_role=node.inferred_role or "GENERAL_FEATURE",
                    nominal_dimensions=node.nominal,
                    neighbor_types=node.neighbor_types,
                    boundary_position=node.boundary_position,
                    tolerance_config=best_match.tolerance_config,
                    confidence=0.90,
                    evidence_source=os.path.basename(dxf_path),
                    description=f"歷史工程圖 {os.path.basename(dxf_path)} 審定之 {node.feature_type} 公差 (名義值: {best_match.nominal_value:.2f})"
                )
                self.case_base.add_case(new_case)
                new_cases_count += 1

        return new_cases_count


if __name__ == "__main__":
    search_paths = [
        r"D:\School\力致\力致_ref",
        r"D:\School\力致\new_data",
        r"d:\School\力致\app\step-to-2d-generator\models"
    ]
    ingestor = HistoricalDataIngestor()
    ingestor.scan_and_ingest_directories(search_paths, max_models=30)
