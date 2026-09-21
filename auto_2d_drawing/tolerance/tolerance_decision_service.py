"""
智慧公差決策服務 (Tolerance Decision Service)
=============================================================================
核心職責:
1. 接收 3D 模型實體與候選標註規則列表。
2. 構建 3D 特徵關係圖 (Feature Relation Graph - FRG)。
3. 調用特徵級案例庫 (CAD-RAG) 檢索歷史審定依據。
4. 結合 Tier 1~3 門檻決策機制 (Confidence Gating & Evidence Attachment)。
5. 調用 ISO 286 查表引擎輸出 100% 精確之公差偏差數值 (+0.000 / -0.006 mm)，杜絕數值幻覺。
6. 回傳結構化公差推薦結果，供 Web 前端即時呈現與一鍵確認。
=============================================================================
"""

import os
import sys
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict

# Ensure workspace root in path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_ws_root = os.path.abspath(os.path.join(_current_dir, "..", ".."))
if _ws_root not in sys.path:
    sys.path.insert(0, _ws_root)

from auto_2d_drawing.tolerance.feature_graph import FeatureGraphExtractor, FeatureRelationGraph, FeatureNode
from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase
from auto_2d_drawing.tolerance.iso_tolerance_table import (
    lookup_iso_fit_deviation, format_tolerance_dimension
)


@dataclass
class ToleranceRecommendation:
    """單一特徵的公差推薦決策結果"""
    rule_id: str
    feature_type: str
    inferred_role: str
    nominal_value: float
    recommended_mode: str               # FIT, GROOVE, CUSTOM_SYMMETRIC, CUSTOM_LIMITS, NONE
    fit_class: Optional[str] = None     # h6, p6, g6, H7, etc.
    upper_dev: float = 0.0              # +0.000
    lower_dev: float = 0.0              # -0.006
    formatted_display: str = ""         # "Φ3.00 h6 (+0.000 / -0.006)"
    confidence: float = 0.0             # 0.0 ~ 1.0
    tier_level: str = "TIER_1"          # TIER_1_RAG_MATCH, TIER_2_RULE_INFERENCE, TIER_3_GENERAL_FALLBACK
    evidence_sources: List[str] = None  # 參考依據清單
    reasoning_description: str = ""     # 推薦理由

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("evidence_sources") is None:
            d["evidence_sources"] = []
        
        # 產生供前端直接使用的 tolerance_config 與 tolerance_str
        mode = self.recommended_mode
        tol_cfg = {"mode": mode}
        tol_str = ""
        
        if mode == "FIT":
            tol_cfg["fit_class"] = self.fit_class or "h6"
            tol_cfg["upper_dev"] = self.upper_dev
            tol_cfg["lower_dev"] = self.lower_dev
            tol_str = self.fit_class or "h6"
        elif mode == "GROOVE":
            tol_cfg["upper_dev"] = self.upper_dev
            tol_cfg["lower_dev"] = self.lower_dev
            tol_str = f"(+{self.upper_dev:.3f}/{self.lower_dev:.3f})"
        elif mode == "CUSTOM_SYMMETRIC":
            tol_cfg["dev"] = abs(self.upper_dev)
            tol_str = f"±{abs(self.upper_dev):.2f}"
        elif mode == "CUSTOM_LIMITS":
            tol_cfg["upper_dev"] = self.upper_dev
            tol_cfg["lower_dev"] = self.lower_dev
            tol_str = f"(+{self.upper_dev:.3f}/{self.lower_dev:.3f})"
        else:
            tol_str = ""

        d["tolerance_config"] = tol_cfg
        d["tolerance_str"] = tol_str
        return d


class ToleranceDecisionService:
    """
    獨立外掛式公差推薦與決策服務
    """
    def __init__(self, case_base: Optional[FeatureCaseBase] = None):
        self.frg_extractor = FeatureGraphExtractor()
        self.case_base = case_base or FeatureCaseBase()

    def recommend_for_rules(
        self,
        shape,
        candidate_rules: List[Dict[str, Any]],
        view_data: Optional[Dict[str, Any]] = None,
        part_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        為所有候選標註規則進行智慧公差推薦分析
        """
        # 1. 建立 3D 特徵關係圖 (FRG)
        graph = self.frg_extractor.build_graph(shape, part_type=part_type)

        recommendations: Dict[str, Dict[str, Any]] = {}
        high_conf_count = 0

        for rule in candidate_rules:
            rule_id = rule.get("rule_id", rule.get("id", ""))
            cat = rule.get("category", "")
            dim_type = rule.get("dim_type", "")
            nom_val = float(rule.get("nominal_value", 0.0))
            is_dia = (dim_type == "DIAMETER")

            # 在 FRG 中尋找對應之特徵節點
            matched_node = self._match_rule_to_node(rule, graph)

            # 執行三層推薦決策 (Tier 1~3)
            rec = self._evaluate_recommendation(rule_id, cat, is_dia, nom_val, matched_node, graph.part_type)
            recommendations[rule_id] = rec.to_dict()

            if rec.confidence >= 0.85:
                high_conf_count += 1

        return {
            "part_type": graph.part_type,
            "total_rules": len(candidate_rules),
            "high_confidence_count": high_conf_count,
            "recommendations": recommendations,
            "feature_graph": graph.to_dict()
        }

    # =========================================================================
    # 規則與特徵節點空間映射 (Rule-to-Node Matcher)
    # =========================================================================
    def _match_rule_to_node(self, rule: Dict[str, Any], graph: FeatureRelationGraph) -> Optional[FeatureNode]:
        r_cat = rule.get("category", "")
        r_val = float(rule.get("nominal_value", 0.0))
        r_payload = rule.get("geometry_payload", {})

        for node in graph.nodes:
            nom = node.nominal
            n_dia = nom.get("diameter", nom.get("groove_diameter", nom.get("radius", 0.0) * 2))
            n_len = nom.get("length", nom.get("groove_width", nom.get("chamfer_height", 0.0)))

            if r_cat == "groove" and node.feature_type == "retaining_ring_groove":
                if abs(n_dia - r_val) < 0.15 or abs(nom.get("groove_width", 0) - r_val) < 0.15:
                    return node
            elif r_cat == "shaft" and node.feature_type == "shaft_segment":
                if abs(n_dia - r_val) < 0.08:
                    return node
            elif r_cat == "chamfer" and node.feature_type == "pilot_chamfer":
                if abs(n_len - r_val) < 0.2:
                    return node
            elif r_cat == "step" and node.feature_type in ("locating_shoulder", "shaft_segment"):
                if abs(n_len - r_val) < 0.15:
                    return node

        return None

    # =========================================================================
    # 核心三層決策評估 (Tier 1~3 Decision Evaluator)
    # =========================================================================
    def _evaluate_recommendation(
        self,
        rule_id: str,
        category: str,
        is_diameter: bool,
        nominal_val: float,
        node: Optional[FeatureNode],
        part_type: str
    ) -> ToleranceRecommendation:
        # 若有節點，優先執行 CAD-RAG 檢索
        if node:
            rag_matches = self.case_base.search_similar_cases(node, part_type=part_type, top_k=2)
            if rag_matches:
                top_case, sim_score = rag_matches[0]

                # === Tier 1: 高信心度歷史案例匹配 (Similarity >= 0.85) ===
                if sim_score >= 0.85:
                    t_cfg = top_case.tolerance_config
                    mode = t_cfg.get("mode", "FIT")
                    fit_cls = t_cfg.get("fit_class")

                    # 防護：長度/段長尺寸絕不能套用軸孔配合代號 (如 h6)
                    if not is_diameter and mode == "FIT":
                        mode = "NONE"
                        fit_cls = None
                        u_dev, l_dev = 0.0, 0.0
                        desc = f"歷史案例段長，採用未注公差 (ISO 2768-m)。"
                    else:
                        u_dev, l_dev = self._compute_exact_devs(nominal_val, mode, fit_cls, is_hole=t_cfg.get("is_hole", False), custom_cfg=t_cfg)
                        desc = top_case.description
                    formatted = format_tolerance_dimension(nominal_val, is_diameter=is_diameter, tol_config={
                        "mode": mode, "fit_class": fit_cls, "upper_dev": u_dev, "lower_dev": l_dev
                    })

                    return ToleranceRecommendation(
                        rule_id=rule_id,
                        feature_type=node.feature_type,
                        inferred_role=node.inferred_role or top_case.inferred_role,
                        nominal_value=nominal_val,
                        recommended_mode=mode,
                        fit_class=fit_cls,
                        upper_dev=u_dev,
                        lower_dev=l_dev,
                        formatted_display=formatted,
                        confidence=round(top_case.confidence * sim_score, 2),
                        tier_level="TIER_1_RAG_MATCH",
                        evidence_sources=[top_case.case_id, top_case.evidence_source],
                        reasoning_description=top_case.description
                    )

                # === Tier 2: 語意啟發式推論 (Similarity 0.65 ~ 0.85) ===
                elif sim_score >= 0.60:
                    role = node.inferred_role
                    
                    if not is_diameter:
                        # 線性階梯段長 / 槽寬等長度尺寸，嚴禁套用 ISO 軸孔配合代號 (如 h6)
                        if category in ("step",) or "len" in rule_id or "width" in rule_id:
                            mode = "NONE"
                            fit_cls = None
                            u_dev, l_dev = 0.05, -0.05
                            desc = f"段落定位階梯長度，採用未注公差 (ISO 2768-m) 或 ±0.05mm 線性工程公差。"
                        elif category in ("overall",):
                            mode = "CUSTOM_SYMMETRIC"
                            fit_cls = None
                            u_dev, l_dev = 0.10, -0.10
                            desc = f"整體包絡總長度，推薦 ±0.10mm 線性工程公差。"
                        else:
                            mode = "NONE"
                            fit_cls = None
                            u_dev, l_dev = 0.0, 0.0
                            desc = f"過渡/非配合線性特徵，採用未注公差 (ISO 2768-m)。"
                    else:
                        # 直徑尺寸才允許套用配合公差
                        if role == "BEARING_JOURNAL":
                            fit_cls = "h6"
                            mode = "FIT"
                            u_dev, l_dev = lookup_iso_fit_deviation(nominal_val, fit_cls, is_hole=False)
                            desc = f"特徵鄰接卡簧槽/定位軸肩，判定為軸承安裝段，推薦 h6 精密配合。"
                        elif role == "PRESS_FIT_HUB":
                            fit_cls = "p6"
                            mode = "FIT"
                            u_dev, l_dev = lookup_iso_fit_deviation(nominal_val, fit_cls, is_hole=False)
                            desc = f"位於軸端，判定為葉輪/輪轂壓配段，推薦 p6 過盈配合。"
                        elif role == "RETAINING_RING_GROOVE":
                            fit_cls = "H13"
                            mode = "GROOVE"
                            u_dev, l_dev = 0.040, 0.000
                            desc = f"標準卡簧槽直徑，依據 JIS B2804 推薦 (+0.040/0.000mm) / H13。"
                        else:
                            fit_cls = None
                            mode = "NONE"
                            u_dev, l_dev = 0.0, 0.0
                            desc = f"一般過渡外徑，採用 ISO 2768-m 未注公差。"

                    formatted = format_tolerance_dimension(nominal_val, is_diameter=is_diameter, tol_config={
                        "mode": mode, "fit_class": fit_cls, "upper_dev": u_dev, "lower_dev": l_dev
                    })

                    return ToleranceRecommendation(
                        rule_id=rule_id,
                        feature_type=node.feature_type,
                        inferred_role=role or "FUNCTIONAL_JOURNAL",
                        nominal_value=nominal_val,
                        recommended_mode=mode,
                        fit_class=fit_cls,
                        upper_dev=u_dev,
                        lower_dev=l_dev,
                        formatted_display=formatted,
                        confidence=0.85,
                        tier_level="TIER_2_RULE_INFERENCE",
                        evidence_sources=[f"ROLE_INFERENCE:{role}", top_case.case_id],
                        reasoning_description=desc
                    )

        # === Tier 3: 基礎保底 (General Fallback / ISO 2768-m) ===
        if not is_diameter:
            if category in ("overall",):
                mode = "CUSTOM_SYMMETRIC"
                fit_cls = None
                u_dev, l_dev = 0.10, -0.10
                desc = "整體包絡總長度，推薦 ±0.10mm 一般線性公差。"
                conf = 0.95
            elif category in ("step",) or "len" in rule_id or "width" in rule_id:
                mode = "NONE"
                fit_cls = None
                u_dev, l_dev = 0.05, -0.05
                desc = "定位台階長度，推薦採用未注公差 (ISO 2768-m)。"
                conf = 0.90
            else:
                mode = "NONE"
                fit_cls = None
                u_dev, l_dev = 0.0, 0.0
                desc = "非配合線性特徵，採用 ISO 2768-m 未注公差。"
                conf = 0.95
        else:
            if category in ("groove",) or "groove" in rule_id:
                mode = "GROOVE"
                fit_cls = "H13"
                u_dev, l_dev = 0.040, 0.000
                desc = "標準退刀/卡簧槽直徑，推薦 JIS B2804 (+0.040/0.000mm) / H13。"
                conf = 0.90
            else:
                mode = "NONE"
                fit_cls = None
                u_dev, l_dev = 0.0, 0.0
                desc = "一般非配合過渡特徵，採用 ISO 2768-m 未注公差。"
                conf = 0.95

        formatted = format_tolerance_dimension(nominal_val, is_diameter=is_diameter, tol_config={
            "mode": mode, "fit_class": fit_cls, "upper_dev": u_dev, "lower_dev": l_dev, "dev": u_dev
        })

        return ToleranceRecommendation(
            rule_id=rule_id,
            feature_type=category,
            inferred_role="GENERAL_FEATURE",
            nominal_value=nominal_val,
            recommended_mode=mode,
            fit_class=fit_cls,
            upper_dev=u_dev,
            lower_dev=l_dev,
            formatted_display=formatted,
            confidence=conf,
            tier_level="TIER_3_GENERAL_FALLBACK",
            evidence_sources=["ISO_2768_M_STANDARD"],
            reasoning_description=desc
        )

    # =========================================================================
    # 精確偏差查表計算器 (Exact Deviation Computer)
    # =========================================================================
    def _compute_exact_devs(
        self,
        nominal_val: float,
        mode: str,
        fit_class: Optional[str],
        is_hole: bool = False,
        custom_cfg: Optional[Dict[str, Any]] = None
    ) -> Tuple[float, float]:
        if mode == "FIT" and fit_class:
            return lookup_iso_fit_deviation(nominal_val, fit_class, is_hole=is_hole)
        elif mode == "GROOVE":
            u = custom_cfg.get("upper_dev", 0.040) if custom_cfg else 0.040
            l = custom_cfg.get("lower_dev", 0.000) if custom_cfg else 0.000
            return u, l
        elif mode == "CUSTOM_SYMMETRIC" and custom_cfg:
            d = custom_cfg.get("dev", 0.05)
            return d, -d
        elif mode == "CUSTOM_LIMITS" and custom_cfg:
            return custom_cfg.get("upper_dev", 0.0), custom_cfg.get("lower_dev", 0.0)
        else:
            return 0.0, 0.0
