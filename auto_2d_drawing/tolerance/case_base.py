"""
特徵級歷史案例庫與 CAD-RAG 檢索引擎 (Feature-Level Case Base & CAD-RAG Engine)
=============================================================================
核心理念:
1. 案例顆粒度為「特徵級 (Feature-Level)」：即使整顆零件幾何不同，同一直徑與鄰接拓撲的段落依然可跨模型檢索。
2. 預置 25+ 筆力致標準種子案例 (Seed Cases)：涵蓋軸承位 (h6)、葉輪壓配位 (p6)、滑動位 (g6)、卡簧槽 (JIS B2804)、軸承孔 (H7) 等。
3. 支援多維度特徵相似度檢索 (Contextual Similarity Matcher)：幾何尺寸 + 拓撲鄰接 + 機能角色 + 零件類別。
4. 支援動態沉澱 (Continuous Knowledge Accumulation)：工程師在 UI 上審定之公差自動持久化至案例庫。
=============================================================================
"""

import os
import json
import math
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict

from auto_2d_drawing.tolerance.feature_graph import FeatureNode


@dataclass
class ToleranceCase:
    """特徵級公差案例結構"""
    case_id: str
    part_type: str                         # SHAFT, FAN, HOUSING, STAMPING, GENERAL
    feature_type: str                      # shaft_segment, retaining_ring_groove, pilot_chamfer, locating_shoulder, hole
    inferred_role: str                     # BEARING_JOURNAL, PRESS_FIT_HUB, RETAINING_RING_GROOVE, SLIDING_JOURNAL, etc.
    nominal_dimensions: Dict[str, float]   # {"diameter": 3.0, "length": 8.0, ...}
    neighbor_types: List[str]              # ["retaining_ring_groove", "locating_shoulder"]
    boundary_position: str                 # INTERIOR, LEFT_END, RIGHT_END
    tolerance_config: Dict[str, Any]       # {"mode": "FIT", "fit_class": "h6", "upper_dev": 0.0, "lower_dev": -0.006}
    confidence: float                      # 歷史審定信心度 (0.0 ~ 1.0)
    evidence_source: str                   # "FORCECON_STANDARD_SEED", "DWG_1FQ6V5000H", "ENGINEER_CONFIRMED"
    description: str                       # 推薦與設計理由說明
    verification_status: str = "UNVERIFIED"  # ENGINEER_VERIFIED / AUTO_VERIFIED / AUTO_EXTRACTED / UNVERIFIED
    source_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def effective_verification_status(self) -> str:
        if self.verification_status and self.verification_status != "UNVERIFIED":
            return self.verification_status
        if self.case_id.startswith("SEED_") or self.evidence_source == "FORCECON_STANDARD_SEED":
            return "SEED_REFERENCE"
        if self.evidence_source == "ENGINEER_CONFIRMED":
            return "ENGINEER_VERIFIED"
        return "UNVERIFIED"

    def is_retrieval_eligible(self) -> bool:
        return self.effective_verification_status() in {
            "ENGINEER_VERIFIED",
            "AUTO_VERIFIED",
        }

    def is_verified_extraction(self) -> bool:
        """Return whether the extracted tolerance is tied to a verified feature.

        Automatic cases require both a passed 2D/3D geometry verification and
        an explicit feature identity.  A manually verified case is accepted
        when its feature identity was recorded, because the engineer decision
        is the authoritative geometry check for that case.
        """
        metadata = self.source_metadata or {}
        if not bool(metadata.get("feature_identity_verified")):
            return False
        status = self.effective_verification_status()
        if status == "ENGINEER_VERIFIED":
            return True
        geometry = metadata.get("geometry_verification") or {}
        return status == "AUTO_VERIFIED" and bool(geometry.get("passed"))


class FeatureCaseBase:
    """
    特徵案例庫與 CAD-RAG 檢索引擎
    """
    DEFAULT_DB_PATH = os.path.abspath(os.environ.get(
        "CAD_TOLERANCE_CASE_DB",
        os.path.join(os.path.dirname(__file__), "data", "feature_case_base.json"),
    ))
    FEATURE_TYPE_ALIASES = {
        "cylinder": "shaft_segment",
        "shaft": "shaft_segment",
        "shaft_segment": "shaft_segment",
        "hole": "hole",
        "fillet": "transition_fillet",
        "transition_fillet": "transition_fillet",
        "chamfer": "pilot_chamfer",
        "pilot_chamfer": "pilot_chamfer",
        "groove": "retaining_ring_groove",
        "retaining_ring_groove": "retaining_ring_groove",
        "locating_shoulder": "locating_shoulder",
        "overall_dimension": "overall_dimension",
        "shaft_overall": "overall_dimension",
        "linear_feature": "linear_feature",
        "unresolved_feature": "unresolved_feature",
    }
    DIMENSION_CATEGORY_ALIASES = {
        "RADIAL": "RADIUS",
        "RADIUS": "RADIUS",
        "DIAMETER": "DIAMETER",
        "LINEAR": "LINEAR",
        "ANGULAR": "ANGULAR",
        "CHAMFER": "CHAMFER",
    }

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or self.DEFAULT_DB_PATH
        self.cases: List[ToleranceCase] = []
        self._load_or_initialize_db()

    def _load_or_initialize_db(self):
        """載入公司案例庫；合成種子案例不進入執行期知識庫。"""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    loaded_cases = [ToleranceCase(**item) for item in data]
                    self.cases = [
                        case for case in loaded_cases
                        if case.effective_verification_status() != "SEED_REFERENCE"
                    ]
                    return
            except Exception as e:
                # print(f"Failed to load case base from {self.db_path}: {e}")
                pass

        # 公司歷史資料不存在時保持空庫，不建立合成案例。
        self.cases = []

    def save_db(self):
        """持久化儲存案例庫至 JSON 檔案"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump([c.to_dict() for c in self.cases], f, ensure_ascii=False, indent=2)

    def add_case(self, case: ToleranceCase):
        """新增單一案例並存檔"""
        # 避免重複 ID
        self.cases = [c for c in self.cases if c.case_id != case.case_id]
        self.cases.append(case)
        self.save_db()

    # =========================================================================
    # CAD-RAG 特徵相似度檢索引擎 (Multi-Dimensional Feature Matcher)
    # =========================================================================
    @classmethod
    def canonical_feature_type(cls, feature_type: str) -> str:
        key = (feature_type or "").strip().lower()
        return cls.FEATURE_TYPE_ALIASES.get(key, key)

    @classmethod
    def canonical_dimension_category(cls, category: str) -> str:
        key = (category or "").strip().upper()
        return cls.DIMENSION_CATEGORY_ALIASES.get(key, key)

    @staticmethod
    def infer_product_family(source_name: str) -> str:
        """Extract the FORCECON product-system code, e.g. 1FQ6H... -> FQ6H."""
        name = os.path.basename(str(source_name or "")).upper()
        match = re.search(r'(?<![A-Z0-9])[0-3]([A-Z0-9]{4})', name)
        return match.group(1) if match else ""

    def search_similar_cases_detailed(
        self,
        query_node: FeatureNode,
        part_type: str = "SHAFT",
        top_k: int = 3,
        include_unverified: bool = False,
        product_family: Optional[str] = None,
        dimension_category: Optional[str] = None,
        allowed_case_ids: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """回傳可追溯的 Top-K 檢索結果，包含每個相似度分項。"""
        if not self.cases:
            return []

        q_nom = query_node.nominal or {}
        q_dia = float(q_nom.get("diameter", q_nom.get("groove_diameter", q_nom.get("radius", 0.0) * 2)) or 0.0)
        q_len = float(q_nom.get("length", q_nom.get("groove_width", q_nom.get("chamfer_height", 0.0))) or 0.0)
        q_neighbors = set(query_node.neighbor_types or [])
        q_feature = self.canonical_feature_type(query_node.feature_type)
        query_part = (part_type or "GENERAL").upper()
        query_family = (product_family or "").upper()
        query_dimension_category = self.canonical_dimension_category(dimension_category or "")
        scored_cases: List[Dict[str, Any]] = []

        for case in self.cases:
            if allowed_case_ids is not None and case.case_id not in allowed_case_ids:
                continue
            verification_status = case.effective_verification_status()
            if verification_status == "SEED_REFERENCE":
                continue
            if not include_unverified and not case.is_retrieval_eligible():
                continue

            case_feature = self.canonical_feature_type(case.feature_type)
            if case_feature != q_feature:
                continue
            case_metadata = case.source_metadata or {}
            case_dimension_category = self.canonical_dimension_category(
                str(case_metadata.get("dimension_category") or "")
            )
            if (
                query_dimension_category
                and case_dimension_category
                and case_dimension_category != query_dimension_category
            ):
                continue

            c_nom = case.nominal_dimensions or {}
            c_dia = float(c_nom.get("diameter", c_nom.get("groove_diameter", c_nom.get("radius", 0.0) * 2)) or 0.0)
            c_len = float(c_nom.get("length", c_nom.get("groove_width", c_nom.get("chamfer_height", 0.0))) or 0.0)
            c_neighbors = set(case.neighbor_types or [])
            case_part = (case.part_type or "GENERAL").upper()
            case_family = self.infer_product_family(
                (case.source_metadata or {}).get("drawing_file") or case.evidence_source
            )

            part_score = 1.0 if case_part == query_part else (0.6 if case_part in {"GENERAL", "MECHANICAL_PART"} else 0.3)
            if not query_family:
                family_score = 0.5
            elif case_family == query_family:
                family_score = 1.0
            elif not case_family and case.effective_verification_status() == "SEED_REFERENCE":
                family_score = 0.75
            elif not case_family:
                family_score = 0.3
            else:
                family_score = 0.1
            feature_score = 1.0
            role_is_verified = bool(case_metadata.get("functional_role_verified", True))
            if not role_is_verified:
                role_score = 0.5
            elif query_node.inferred_role and case.inferred_role:
                role_score = 1.0 if query_node.inferred_role == case.inferred_role else 0.35
            else:
                role_score = 0.5

            if q_neighbors and c_neighbors:
                topology_score = len(q_neighbors & c_neighbors) / max(1, len(q_neighbors | c_neighbors))
            elif not q_neighbors and not c_neighbors:
                topology_score = 0.5
            else:
                topology_score = 0.2

            if q_dia > 0 and c_dia > 0:
                relative_diameter_error = abs(q_dia - c_dia) / max(q_dia, c_dia, 0.001)
                diameter_score = math.exp(-4.0 * relative_diameter_error)
            else:
                diameter_score = 0.5
            if q_len > 0 and c_len > 0:
                relative_length_error = abs(q_len - c_len) / max(q_len, c_len, 0.001)
                length_score = max(0.0, 1.0 - relative_length_error)
            else:
                length_score = 0.5

            breakdown = {
                "part_type": round(part_score, 3),
                "product_family": round(family_score, 3),
                "feature_type": round(feature_score, 3),
                "functional_role": round(role_score, 3),
                "topology": round(topology_score, 3),
                "diameter": round(diameter_score, 3),
                "length": round(length_score, 3),
            }
            total_score = (
                0.10 * part_score
                + 0.20 * family_score
                + 0.20 * feature_score
                + 0.15 * role_score
                + 0.10 * topology_score
                + 0.15 * diameter_score
                + 0.10 * length_score
            )
            scored_cases.append({
                "case": case,
                "similarity": round(total_score, 3),
                "score_breakdown": breakdown,
                "verification_status": verification_status,
                "product_family": case_family,
                "same_product_family": bool(query_family and case_family == query_family),
            })

        if query_family:
            scored_cases.sort(
                key=lambda item: (item["same_product_family"], item["similarity"]),
                reverse=True,
            )
        else:
            scored_cases.sort(key=lambda item: item["similarity"], reverse=True)
        return scored_cases[:top_k]

    def search_similar_cases(self, query_node: FeatureNode, part_type: str = "SHAFT", top_k: int = 3) -> List[Tuple[ToleranceCase, float]]:
        """
        以 FeatureNode 為查詢上下文，檢索 Top-K 最相似之歷史公差案例。
        回傳: [(ToleranceCase, similarity_score)]
        """
        detailed = self.search_similar_cases_detailed(query_node, part_type=part_type, top_k=top_k)
        return [(item["case"], item["similarity"]) for item in detailed]

    # =========================================================================
    # 預置 28 筆力致標準種子案例庫 (Seed Knowledge Base)
    # =========================================================================
    def _generate_seed_cases(self) -> List[ToleranceCase]:
        seeds = [
            # 1. 軸承位 (h6 精密過渡/滑動配合)
            ToleranceCase(
                case_id="SEED_SHAFT_BEARING_01",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 3.0, "length": 8.0},
                neighbor_types=["retaining_ring_groove", "locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "h6", "is_hole": False},
                confidence=0.95,
                evidence_source="FORCECON_STANDARD_SEED",
                description="微型馬達標準滾珠軸承安裝段，直徑 3.0mm，鄰接定位卡簧槽，採用 h6 (0/-0.006mm) 緊密滑動配合。"
            ),
            ToleranceCase(
                case_id="SEED_SHAFT_BEARING_02",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 2.5, "length": 6.0},
                neighbor_types=["retaining_ring_groove", "locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "h6", "is_hole": False},
                confidence=0.94,
                evidence_source="FORCECON_STANDARD_SEED",
                description="微型風扇軸承安裝段，直徑 2.5mm，採用 h6 (0/-0.006mm) 精密配合。"
            ),
            ToleranceCase(
                case_id="SEED_SHAFT_BEARING_03",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 4.0, "length": 10.0},
                neighbor_types=["retaining_ring_groove", "locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "h6", "is_hole": False},
                confidence=0.95,
                evidence_source="FORCECON_STANDARD_SEED",
                description="伺服馬達中型軸承位，直徑 4.0mm，採用 h6 (0/-0.008mm) 配合。"
            ),
            ToleranceCase(
                case_id="SEED_SHAFT_BEARING_04",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="BEARING_JOURNAL",
                nominal_dimensions={"diameter": 5.0, "length": 12.0},
                neighbor_types=["locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "h6", "is_hole": False},
                confidence=0.95,
                evidence_source="FORCECON_STANDARD_SEED",
                description="散熱風扇驅動軸承位，直徑 5.0mm，採用 h6 (0/-0.008mm)。"
            ),

            # 2. 葉輪 / 轉子壓配段 (p6 過盈配合)
            ToleranceCase(
                case_id="SEED_SHAFT_PRESS_FIT_01",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="PRESS_FIT_HUB",
                nominal_dimensions={"diameter": 2.0, "length": 5.0},
                neighbor_types=["shaft_end", "pilot_chamfer"],
                boundary_position="RIGHT_END",
                tolerance_config={"mode": "FIT", "fit_class": "p6", "is_hole": False},
                confidence=0.92,
                evidence_source="FORCECON_STANDARD_SEED",
                description="風扇塑膠葉輪輪轂過盈壓配段，位於軸端，採用 p6 (+0.012/+0.006mm) 防止高速打滑。"
            ),
            ToleranceCase(
                case_id="SEED_SHAFT_PRESS_FIT_02",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="PRESS_FIT_HUB",
                nominal_dimensions={"diameter": 3.0, "length": 6.5},
                neighbor_types=["shaft_end", "pilot_chamfer"],
                boundary_position="LEFT_END",
                tolerance_config={"mode": "FIT", "fit_class": "p6", "is_hole": False},
                confidence=0.93,
                evidence_source="FORCECON_STANDARD_SEED",
                description="金屬轉子鐵芯過盈壓配段，採用 p6 (+0.012/+0.006mm) 緊固連接。"
            ),

            # 3. 滑動導引軸段 (g6 間隙配合)
            ToleranceCase(
                case_id="SEED_SHAFT_SLIDING_01",
                part_type="SHAFT",
                feature_type="shaft_segment",
                inferred_role="SLIDING_JOURNAL",
                nominal_dimensions={"diameter": 3.0, "length": 15.0},
                neighbor_types=["locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "g6", "is_hole": False},
                confidence=0.90,
                evidence_source="FORCECON_STANDARD_SEED",
                description="含油襯套滑動摩擦段，直徑 3.0mm，採用 g6 (-0.002/-0.008mm) 保證油膜潤滑間隙。"
            ),

            # 4. 卡簧槽 (JIS B2804 標準卡簧公差)
            ToleranceCase(
                case_id="SEED_GROOVE_E_RING_01",
                part_type="SHAFT",
                feature_type="retaining_ring_groove",
                inferred_role="RETAINING_RING_GROOVE",
                nominal_dimensions={"groove_diameter": 2.5, "groove_width": 0.6},
                neighbor_types=["shaft_segment", "locating_shoulder"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "GROOVE", "upper_dev": 0.040, "lower_dev": 0.000},
                confidence=0.96,
                evidence_source="FORCECON_STANDARD_SEED",
                description="E型扣環定位槽 (JIS B2804)，寬度 0.6mm，槽底直徑 2.5mm，採用 (+0.040/0.000mm) 標準偏差。"
            ),
            ToleranceCase(
                case_id="SEED_GROOVE_E_RING_02",
                part_type="SHAFT",
                feature_type="retaining_ring_groove",
                inferred_role="RETAINING_RING_GROOVE",
                nominal_dimensions={"groove_diameter": 2.58, "groove_width": 0.9},
                neighbor_types=["shaft_segment", "pilot_chamfer"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "GROOVE", "upper_dev": 0.040, "lower_dev": 0.000},
                confidence=0.96,
                evidence_source="FORCECON_STANDARD_SEED",
                description="C型扣環安裝槽，寬度 0.9mm，採用 (+0.040/0.000mm)。"
            ),

            # 5. 軸承孔 / 襯套內孔 (H7 / H8 配合)
            ToleranceCase(
                case_id="SEED_BORE_BEARING_01",
                part_type="HOUSING",
                feature_type="hole",
                inferred_role="BEARING_BORE",
                nominal_dimensions={"diameter": 6.0, "length": 6.0},
                neighbor_types=["plane", "chamfer"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "H7", "is_hole": True},
                confidence=0.95,
                evidence_source="FORCECON_STANDARD_SEED",
                description="馬達基座軸承安裝孔，直徑 6.0mm，採用 H7 (+0.012/0.000mm) 精密過渡配合。"
            ),
            ToleranceCase(
                case_id="SEED_BORE_BEARING_02",
                part_type="HOUSING",
                feature_type="hole",
                inferred_role="BEARING_BORE",
                nominal_dimensions={"diameter": 8.0, "length": 8.0},
                neighbor_types=["plane"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "FIT", "fit_class": "H7", "is_hole": True},
                confidence=0.95,
                evidence_source="FORCECON_STANDARD_SEED",
                description="風扇中管軸承孔，直徑 8.0mm，採用 H7 (+0.015/0.000mm)。"
            ),

            # 6. 導引倒角 (一般未注公差)
            ToleranceCase(
                case_id="SEED_CHAMFER_PILOT_01",
                part_type="SHAFT",
                feature_type="pilot_chamfer",
                inferred_role="PILOT_LEAD_IN",
                nominal_dimensions={"chamfer_height": 0.5, "angle": 45.0},
                neighbor_types=["shaft_segment", "shaft_end"],
                boundary_position="LEFT_END",
                tolerance_config={"mode": "NONE"},
                confidence=0.98,
                evidence_source="FORCECON_STANDARD_SEED",
                description="軸端組裝導引倒角 C0.5x45°，採用 ISO 2768-m 一般未注公差。"
            ),
            ToleranceCase(
                case_id="SEED_CHAMFER_PILOT_02",
                part_type="SHAFT",
                feature_type="pilot_chamfer",
                inferred_role="PILOT_LEAD_IN",
                nominal_dimensions={"chamfer_height": 0.75, "angle": 35.0},
                neighbor_types=["shaft_segment", "shaft_end"],
                boundary_position="LEFT_END",
                tolerance_config={"mode": "NONE"},
                confidence=0.98,
                evidence_source="FORCECON_STANDARD_SEED",
                description="軸端引導導錐 C0.75x35°，採用 ISO 2768-m 一般未注公差。"
            ),

            # 7. 總長度與定位階梯 (線性公差)
            ToleranceCase(
                case_id="SEED_SHAFT_OVERALL_01",
                part_type="SHAFT",
                feature_type="shaft_overall",
                inferred_role="OVERALL_LENGTH",
                nominal_dimensions={"length": 21.4},
                neighbor_types=["shaft_end"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.10},
                confidence=0.92,
                evidence_source="FORCECON_STANDARD_SEED",
                description="小型馬達軸總長度 L21.40mm，採用 ±0.10mm 線性工程公差。"
            ),
            ToleranceCase(
                case_id="SEED_STEP_SHOULDER_01",
                part_type="SHAFT",
                feature_type="locating_shoulder",
                inferred_role="AXIAL_LOCATING_SHOULDER",
                nominal_dimensions={"step_height": 0.5, "dia_from": 2.5, "dia_to": 3.0},
                neighbor_types=["shaft_segment", "retaining_ring_groove"],
                boundary_position="INTERIOR",
                tolerance_config={"mode": "CUSTOM_SYMMETRIC", "dev": 0.05},
                confidence=0.93,
                evidence_source="FORCECON_STANDARD_SEED",
                description="軸承定位軸肩台階，採用 ±0.05mm 線性公差控制軸向竄動。"
            ),
        ]
        return seeds
