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
import re
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
    evidence_cases: List[Dict[str, Any]] = None
    retrieval_trace: Dict[str, Any] = None
    reasoning_description: str = ""     # 推薦理由
    is_hole: bool = False
    decision_status: str = "REVIEW_REQUIRED"
    confidence_basis: str = "UNCALIBRATED_HEURISTIC"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("evidence_sources") is None:
            d["evidence_sources"] = []
        if d.get("evidence_cases") is None:
            d["evidence_cases"] = []
        if d.get("retrieval_trace") is None:
            d["retrieval_trace"] = {}
        
        # 產生供前端直接使用的 tolerance_config 與 tolerance_str
        mode = self.recommended_mode
        tol_cfg = {"mode": mode}
        tol_str = ""
        
        if mode == "FIT":
            tol_cfg["fit_class"] = self.fit_class or "h6"
            tol_cfg["upper_dev"] = self.upper_dev
            tol_cfg["lower_dev"] = self.lower_dev
            tol_cfg["is_hole"] = self.is_hole
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
        part_type: Optional[str] = None,
        product_family: Optional[str] = None,
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
            rec = self._evaluate_recommendation(
                rule_id,
                cat,
                is_dia,
                nom_val,
                matched_node,
                graph.part_type,
                product_family,
                dimension_category=dim_type,
            )
            recommendations[rule_id] = rec.to_dict()

            if rec.confidence >= 0.85:
                high_conf_count += 1

        return {
            "part_type": graph.part_type,
            "product_family": product_family or "",
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
            elif r_cat == "step" and node.feature_type == "locating_shoulder":
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
        part_type: str,
        product_family: Optional[str] = None,
        dimension_category: Optional[str] = None,
    ) -> ToleranceRecommendation:
        eligible_case_count = sum(1 for case in self.case_base.cases if case.is_retrieval_eligible())
        evidence_cases: List[Dict[str, Any]] = []
        retrieval_trace: Dict[str, Any] = {
            "query_feature_type": node.feature_type if node else category,
            "query_dimension_kind": "DIAMETER" if is_diameter else "LINEAR",
            "product_family": product_family or "",
            "eligible_case_count": eligible_case_count,
            "retrieved_case_count": 0,
            "compatible_case_count": 0,
            "unverified_candidate_count": 0,
            "same_family_candidate_count": 0,
            "searched_case_ids": [],
            "decision_source": "GENERAL_FALLBACK",
        }

        # 若有節點，優先執行 CAD-RAG 檢索。案例還必須與尺寸語意相容，
        # 避免使用軸徑配合案例推論階梯長度或其他線性尺寸。
        if node:
            query_dimension_category = FeatureCaseBase.canonical_dimension_category(
                dimension_category or ("DIAMETER" if is_diameter else "LINEAR")
            )
            raw_matches = self.case_base.search_similar_cases_detailed(
                node,
                part_type=part_type,
                top_k=8,
                product_family=product_family,
                dimension_category=query_dimension_category,
            )
            audit_matches = self.case_base.search_similar_cases_detailed(
                node,
                part_type=part_type,
                top_k=24,
                include_unverified=True,
                product_family=product_family,
                dimension_category=query_dimension_category,
            )
            review_matches = [
                match for match in audit_matches
                if not match["case"].is_retrieval_eligible()
            ][:3]
            display_matches = list(raw_matches)
            displayed_ids = {match["case"].case_id for match in display_matches}
            display_matches.extend(
                match for match in review_matches
                if match["case"].case_id not in displayed_ids
            )
            rag_matches = [
                match for match in raw_matches
                if self._case_is_dimension_compatible(
                    match["case"], category, is_diameter, query_dimension_category
                )
            ]
            for match in display_matches:
                item = self._serialize_evidence_match(match)
                item["dimension_compatible"] = self._case_is_dimension_compatible(
                    match["case"], category, is_diameter, query_dimension_category
                )
                item["decision_eligible"] = match["case"].is_retrieval_eligible()
                item["used_for_decision"] = False
                item["used_as_context"] = False
                item["evidence_role"] = "RETRIEVED_CANDIDATE"
                evidence_cases.append(item)
            retrieval_trace.update({
                "retrieved_case_count": len(display_matches),
                "compatible_case_count": len(rag_matches),
                "unverified_candidate_count": len(review_matches),
                "same_family_candidate_count": sum(
                    1 for match in display_matches if match.get("same_product_family")
                ),
                "searched_case_ids": [match["case"].case_id for match in display_matches],
            })
            if rag_matches:
                top_match = rag_matches[0]
                top_case = top_match["case"]
                sim_score = top_match["similarity"]
                consensus, consensus_match = self._summarize_case_consensus(rag_matches)
                retrieval_trace["consensus"] = consensus
                strong_single_match = sim_score >= 0.85 and not consensus["near_top_conflict"]
                adopted_match = top_match if strong_single_match else consensus_match
                # === Tier 1: 高信心度歷史案例匹配 (Similarity >= 0.85) ===
                if adopted_match is not None:
                    top_match = adopted_match
                    top_case = top_match["case"]
                    sim_score = top_match["similarity"]
                    for item in evidence_cases:
                        if item["case_id"] == top_case.case_id:
                            item["used_for_decision"] = True
                            item["evidence_role"] = "ADOPTED_HISTORICAL_CASE"
                    t_cfg = top_case.tolerance_config
                    mode = t_cfg.get("mode", "FIT")
                    fit_cls = t_cfg.get("fit_class")
                    is_hole = bool(t_cfg.get("is_hole", node.feature_type == "hole"))

                    # 防護：長度/段長尺寸絕不能套用軸孔配合代號 (如 h6)
                    if not is_diameter and mode == "FIT":
                        mode = "NONE"
                        fit_cls = None
                        u_dev, l_dev = 0.0, 0.0
                        desc = f"歷史案例段長，採用未注公差 (ISO 2768-m)。"
                    else:
                        u_dev, l_dev = self._compute_exact_devs(nominal_val, mode, fit_cls, is_hole=is_hole, custom_cfg=t_cfg)
                        desc = top_case.description
                    formatted = format_tolerance_dimension(nominal_val, is_diameter=is_diameter, tol_config={
                        "mode": mode,
                        "fit_class": fit_cls,
                        "upper_dev": u_dev,
                        "lower_dev": l_dev,
                        "dev": abs(u_dev),
                        "is_hole": is_hole,
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
                        tier_level=(
                            "TIER_1_RAG_MATCH"
                            if strong_single_match
                            else "TIER_1_RAG_CONSENSUS"
                        ),
                        evidence_sources=[top_case.case_id, top_case.evidence_source],
                        evidence_cases=evidence_cases,
                        retrieval_trace={
                            **retrieval_trace,
                            "decision_source": "HISTORICAL_CASE",
                            "adopted_case_id": top_case.case_id,
                        },
                        reasoning_description=desc,
                        is_hole=is_hole,
                        decision_status="RECOMMENDED",
                        confidence_basis="HISTORICAL_EVIDENCE_UNCALIBRATED",
                    )

                # === Tier 2: 語意啟發式推論 (Similarity 0.65 ~ 0.85) ===
                # Generic extraction labels must not be converted into a
                # guessed engineering tolerance. Tier 2 is reserved for roles
                # that have an explicit project rule.
                elif sim_score >= 0.65 and self._role_supports_rule_inference(node.inferred_role):
                    for item in evidence_cases:
                        if item["case_id"] == top_case.case_id:
                            item["used_as_context"] = True
                            item["evidence_role"] = "RULE_CONTEXT_ONLY"
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
                        "mode": mode,
                        "fit_class": fit_cls,
                        "upper_dev": u_dev,
                        "lower_dev": l_dev,
                        "dev": abs(u_dev),
                        "is_hole": node.feature_type == "hole",
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
                        confidence=round(min(0.80, sim_score), 2),
                        tier_level="TIER_2_RULE_INFERENCE",
                        evidence_sources=[f"ROLE_INFERENCE:{role}", top_case.case_id],
                        evidence_cases=evidence_cases,
                        retrieval_trace={
                            **retrieval_trace,
                            "decision_source": "RULE_WITH_CASE_CONTEXT",
                            "context_case_id": top_case.case_id,
                        },
                        reasoning_description=desc,
                        is_hole=node.feature_type == "hole",
                        decision_status="RULE_SUGGESTION",
                        confidence_basis="ENGINEERING_RULE_HEURISTIC",
                    )

        # === Tier 3: 基礎保底 (General Fallback / ISO 2768-m) ===
        if not is_diameter:
            if category in ("overall",):
                mode = "CUSTOM_SYMMETRIC"
                fit_cls = None
                u_dev, l_dev = 0.10, -0.10
                desc = "未找到可採用的同類歷史案例；暫以專案規則建議 ±0.10mm，需工程師確認。"
                conf = 0.45
            elif category in ("step",) or "len" in rule_id or "width" in rule_id:
                mode = "NONE"
                fit_cls = None
                u_dev, l_dev = 0.0, 0.0
                desc = "未找到尺寸語意相容的歷史案例；保留圖面一般公差，需工程師確認。"
                conf = 0.35
            else:
                mode = "NONE"
                fit_cls = None
                u_dev, l_dev = 0.0, 0.0
                desc = "未找到尺寸語意相容的歷史案例；保留圖面一般公差，需工程師確認。"
                conf = 0.35
        else:
            if category in ("groove",) or "groove" in rule_id:
                mode = "GROOVE"
                fit_cls = "H13"
                u_dev, l_dev = 0.040, 0.000
                desc = "標準退刀/卡簧槽直徑，推薦 JIS B2804 (+0.040/0.000mm) / H13。"
                conf = 0.70
            else:
                mode = "NONE"
                fit_cls = None
                u_dev, l_dev = 0.0, 0.0
                desc = "一般非配合過渡特徵，採用 ISO 2768-m 未注公差。"
                conf = 0.50

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
            evidence_sources=["PROJECT_RULE_FALLBACK"],
            evidence_cases=evidence_cases,
            retrieval_trace=retrieval_trace,
            reasoning_description=desc,
            decision_status="REVIEW_REQUIRED",
            confidence_basis="FALLBACK_HEURISTIC",
        )

    @staticmethod
    def _summarize_case_consensus(
        matches: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """Summarize agreement and return an adoptable historical match.

        Consensus is intentionally strict until a larger gold set can calibrate
        the thresholds: three agreeing cases, two independent part groups and
        at least 67% of similarity-weighted evidence.
        """
        if not matches:
            return {
                "eligible": False,
                "support_case_count": 0,
                "support_part_count": 0,
                "weighted_agreement": 0.0,
                "near_top_conflict": False,
            }, None

        def signature(case: ToleranceCase) -> Tuple[Any, ...]:
            config = case.tolerance_config or {}
            mode = str(config.get("mode") or "NONE").upper()
            if mode == "CUSTOM_SYMMETRIC":
                dev = abs(float(config.get("dev", config.get("upper_dev", 0.0)) or 0.0))
                upper, lower = dev, -dev
            else:
                upper = float(config.get("upper_dev", 0.0) or 0.0)
                lower = float(config.get("lower_dev", 0.0) or 0.0)
            return (
                mode,
                str(config.get("fit_class") or ""),
                round(upper, 6),
                round(lower, 6),
                bool(config.get("is_hole", False)),
            )

        def part_group(case: ToleranceCase) -> str:
            metadata = case.source_metadata or {}
            source = metadata.get("drawing_file") or case.evidence_source or case.case_id
            stem = os.path.splitext(os.path.basename(str(source)))[0].upper()
            return re.sub(r"-(?:R|A)\d+$", "", stem)

        grouped: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
        for match in matches:
            grouped.setdefault(signature(match["case"]), []).append(match)

        def group_weight(items: List[Dict[str, Any]]) -> float:
            return sum(
                float(item.get("similarity", 0.0)) * float(item["case"].confidence)
                for item in items
            )

        winning_signature, winning_items = max(
            grouped.items(), key=lambda pair: group_weight(pair[1])
        )
        total_weight = sum(group_weight(items) for items in grouped.values())
        winning_weight = group_weight(winning_items)
        agreement = winning_weight / total_weight if total_weight > 0 else 0.0
        support_parts = {part_group(item["case"]) for item in winning_items}
        top_similarity = float(matches[0].get("similarity", 0.0))
        near_top = [
            item for item in matches
            if float(item.get("similarity", 0.0)) >= max(0.80, top_similarity - 0.03)
        ]
        near_top_conflict = len({signature(item["case"]) for item in near_top}) > 1
        eligible = (
            len(winning_items) >= 3
            and len(support_parts) >= 2
            and agreement >= 0.67
            and max(float(item.get("similarity", 0.0)) for item in winning_items) >= 0.65
            and signature(matches[0]["case"]) == winning_signature
        )
        summary = {
            "eligible": eligible,
            "support_case_count": len(winning_items),
            "support_part_count": len(support_parts),
            "weighted_agreement": round(agreement, 3),
            "near_top_conflict": near_top_conflict,
            "winning_tolerance": {
                "mode": winning_signature[0],
                "fit_class": winning_signature[1] or None,
                "upper_dev": winning_signature[2],
                "lower_dev": winning_signature[3],
                "is_hole": winning_signature[4],
            },
            "support_case_ids": [item["case"].case_id for item in winning_items],
        }
        winning_items.sort(key=lambda item: item.get("similarity", 0.0), reverse=True)
        return summary, winning_items[0] if eligible else None

    @staticmethod
    def _role_supports_rule_inference(role: Optional[str]) -> bool:
        return str(role or "").upper() in {
            "BEARING_JOURNAL",
            "PRESS_FIT_HUB",
            "RETAINING_RING_GROOVE",
            "BEARING_BORE",
            "PILOT_LEAD_IN",
            "AXIAL_LOCATING_SHOULDER",
        }

    @staticmethod
    def _case_is_dimension_compatible(
        case: ToleranceCase,
        category: str,
        is_diameter: bool,
        dimension_category: Optional[str] = None,
    ) -> bool:
        """Reject cases whose tolerance semantics differ from the candidate rule."""
        config = case.tolerance_config or {}
        mode = config.get("mode", "NONE")
        case_category = FeatureCaseBase.canonical_dimension_category(
            str((case.source_metadata or {}).get("dimension_category") or "")
        )
        query_category = FeatureCaseBase.canonical_dimension_category(
            dimension_category or ("DIAMETER" if is_diameter else "LINEAR")
        )
        if case_category and query_category and case_category != query_category:
            return False
        if is_diameter:
            if category == "groove":
                return mode in {"GROOVE", "CUSTOM_LIMITS", "CUSTOM_SYMMETRIC"}
            return mode in {"FIT", "CUSTOM_LIMITS", "CUSTOM_SYMMETRIC"}
        return mode in {"CUSTOM_LIMITS", "CUSTOM_SYMMETRIC"}

    @staticmethod
    def _serialize_evidence_match(match: Dict[str, Any]) -> Dict[str, Any]:
        case = match["case"]
        metadata = case.source_metadata or {}
        source_model = (
            metadata.get("drawing_file")
            or metadata.get("model_name")
            or case.evidence_source
        )
        return {
            "case_id": case.case_id,
            "drawing": case.evidence_source,
            "source_model": os.path.splitext(os.path.basename(str(source_model)))[0],
            "part_type": case.part_type,
            "feature_type": case.feature_type,
            "inferred_role": case.inferred_role,
            "nominal_dimensions": case.nominal_dimensions,
            "tolerance_config": case.tolerance_config,
            "similarity": match["similarity"],
            "score_breakdown": match["score_breakdown"],
            "verification_status": match["verification_status"],
            "product_family": match.get("product_family", ""),
            "same_product_family": match.get("same_product_family", False),
            "description": case.description,
            "source_entity": {
                "handle": metadata.get("entity_handle"),
                "raw_text": metadata.get("raw_text"),
                "layer": metadata.get("layer"),
                "points": metadata.get("points"),
            },
        }

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
