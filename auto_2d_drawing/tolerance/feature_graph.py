"""
特徵關係圖引擎 (Feature Relation Graph - FRG)
=============================================================================
功能:
1. 接收 OpenCASCADE 3D 實體模型 (TopoDS_Shape) 或 FeatureExtractor 幾何事實。
2. 提取所有段落特徵 (軸段、階梯、卡簧槽、軸肩、倒角、圓角、孔洞)。
3. 依照主軸向空間座標進行拓撲排序 (Topological / Spatial Sorting)。
4. 建立節點間的鄰接拓撲關聯 (Adjacency Relation: adjacent_left, adjacent_right, neighbor_types)。
5. 為 CAD-RAG 提供具備「機能上下文 (Functional Context)」的特徵檢索單元。
=============================================================================
"""

import math
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict

from auto_2d_drawing.feature_extractor import FeatureExtractor
from auto_2d_drawing.part_classifier import PartClassifier


# Shared tolerance-feature taxonomy. Historical ingestion must use the same
# feature names produced by FeatureGraphExtractor instead of inventing a
# parallel 2D-only classification.
CANONICAL_FEATURE_TYPES = frozenset({
    "shaft_segment",
    "hole",
    "retaining_ring_groove",
    "locating_shoulder",
    "pilot_chamfer",
    "transition_fillet",
})

DIMENSION_CATEGORY_FEATURE_CANDIDATES = {
    "DIAMETER": ("shaft_segment", "hole", "retaining_ring_groove"),
    "LINEAR": ("shaft_segment", "hole", "retaining_ring_groove", "locating_shoulder", "pilot_chamfer"),
    "RADIUS": ("transition_fillet",),
    "CHAMFER": ("pilot_chamfer",),
    "ANGULAR": ("pilot_chamfer",),
}


def candidate_feature_types_for_dimension(dimension_category: str) -> List[str]:
    """Return FeatureGraphExtractor-compatible candidates for a 2D dimension."""
    return list(DIMENSION_CATEGORY_FEATURE_CANDIDATES.get(
        str(dimension_category or "").upper(),
        (),
    ))


@dataclass
class FeatureNode:
    """特徵關係圖中的單一特徵節點"""
    id: str
    feature_type: str                  # shaft_segment, groove, step, chamfer, fillet, hole, plane
    nominal: Dict[str, float]          # {"diameter": 3.0, "length": 8.0, ...}
    axial_span: List[float]            # [start_axial, end_axial]
    center_axial: float                # 軸向中心位置
    adjacent_left_type: Optional[str] = None
    adjacent_right_type: Optional[str] = None
    neighbor_types: List[str] = field(default_factory=list)
    boundary_position: str = "INTERIOR"  # LEFT_END, RIGHT_END, INTERIOR
    source_info: Dict[str, Any] = field(default_factory=dict)
    inferred_role: Optional[str] = None # BEARING_SEAT, PRESS_FIT_HUB, SLIDING_JOURNAL, etc.

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FeatureRelationGraph:
    """特徵關係圖 (FRG) 結構"""
    def __init__(self, part_type: str, main_axis: str = 'x', total_length: float = 0.0):
        self.part_type = part_type
        self.main_axis = main_axis
        self.total_length = total_length
        self.nodes: List[FeatureNode] = []

    def add_node(self, node: FeatureNode):
        self.nodes.append(node)

    def get_node(self, node_id: str) -> Optional[FeatureNode]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "part_type": self.part_type,
            "main_axis": self.main_axis,
            "total_length": round(self.total_length, 3),
            "node_count": len(self.nodes),
            "nodes": [n.to_dict() for n in self.nodes]
        }


class FeatureGraphExtractor:
    """
    從 3D 拓撲實體提取特徵關聯圖 (FRG) 的核心引擎
    """
    def __init__(self):
        self.classifier = PartClassifier()

    def build_graph(self, shape, part_type: Optional[str] = None) -> FeatureRelationGraph:
        """
        掃描 3D 幾何，建構空間特徵關係圖
        """
        feat = FeatureExtractor(shape)
        if not part_type:
            part_type = self.classifier.classify(feat, None)

        # 決定主軸向 (長度最長的方向)
        dims = [('x', feat.W), ('y', feat.H), ('z', feat.D)]
        dims.sort(key=lambda x: x[1], reverse=True)
        main_axis = dims[0][0]
        total_len = dims[0][1]

        graph = FeatureRelationGraph(part_type=part_type, main_axis=main_axis, total_length=total_len)

        # 依零件類型調用特化建圖邏輯
        if part_type == "SHAFT" or "SHAFT" in part_type.upper() or len(feat.shafts) >= 1:
            self._build_shaft_graph(feat, graph)
        else:
            self._build_general_graph(feat, graph)

        # 進行全域拓撲排序與鄰接關係修補
        self._link_adjacency_relations(graph)

        return graph

    # =========================================================================
    # 軸類零件 (SHAFT) 特徵圖構建
    # =========================================================================
    def _build_shaft_graph(self, feat: FeatureExtractor, graph: FeatureRelationGraph):
        axis_idx = 0 if graph.main_axis == 'x' else (1 if graph.main_axis == 'y' else 2)

        # 1. 軸段 (Shafts & Cylinders)
        target_cyls = feat.shafts if feat.shafts else feat.cylinders_raw
        for idx, cyl in enumerate(target_cyls):
            c_dia = round(cyl.get("diameter", 0.0), 3)
            c_len = round(cyl.get("length", 0.0), 3)
            center = cyl.get("center", [0.0, 0.0, 0.0])
            c_pos = float(center[axis_idx])
            start_a = c_pos - c_len / 2.0
            end_a = c_pos + c_len / 2.0

            node = FeatureNode(
                id=f"cyl_{idx + 1:02d}",
                feature_type="shaft_segment",
                nominal={"diameter": c_dia, "length": c_len},
                axial_span=[round(start_a, 3), round(end_a, 3)],
                center_axial=round(c_pos, 3),
                source_info={"type": "cylinder", "area": cyl.get("area", 0.0)}
            )
            graph.add_node(node)

        # 2. 卡簧槽 / 溝槽 (Toruses)
        for idx, tor in enumerate(feat.toruses):
            w = round(tor.get("width", 0.9), 3)
            dia = round(tor.get("major_diameter", tor.get("bottom_diameter", 2.5)), 3)
            center = tor.get("center", [0.0, 0.0, 0.0])
            c_pos = float(center[axis_idx])
            start_a = c_pos - w / 2.0
            end_a = c_pos + w / 2.0

            node = FeatureNode(
                id=f"groove_{idx + 1:02d}",
                feature_type="retaining_ring_groove",
                nominal={"groove_diameter": dia, "groove_width": w},
                axial_span=[round(start_a, 3), round(end_a, 3)],
                center_axial=round(c_pos, 3),
                source_info={"type": "torus", "minor_radius": tor.get("minor_radius", 0.0)}
            )
            graph.add_node(node)

        # 3. 階梯 / 軸肩 (Step Segments)
        for idx, stp in enumerate(feat.step_segments):
            d1 = round(stp.get("dia1", 0.0), 3)
            d2 = round(stp.get("dia2", 0.0), 3)
            h = round(stp.get("step_height", abs(d2 - d1) / 2.0), 3)
            pos = float(stp.get("axial_pos", stp.get("z_pos", 0.0)))

            node = FeatureNode(
                id=f"step_{idx + 1:02d}",
                feature_type="locating_shoulder",
                nominal={"step_height": h, "dia_from": min(d1, d2), "dia_to": max(d1, d2)},
                axial_span=[round(pos - 0.1, 3), round(pos + 0.1, 3)],
                center_axial=round(pos, 3),
                source_info={"type": "step"}
            )
            graph.add_node(node)

        # 4. 倒角 (Cones / Chamfers)
        for idx, cone in enumerate(feat.cones):
            semi_ang = round(cone.get("semi_angle_deg", 45.0), 1)
            h = round(cone.get("height", 0.5), 3)
            center = cone.get("center", [0.0, 0.0, 0.0])
            c_pos = float(center[axis_idx])

            node = FeatureNode(
                id=f"chamfer_{idx + 1:02d}",
                feature_type="pilot_chamfer",
                nominal={"chamfer_height": h, "angle": semi_ang},
                axial_span=[round(c_pos - h / 2.0, 3), round(c_pos + h / 2.0, 3)],
                center_axial=round(c_pos, 3),
                source_info={"type": "cone"}
            )
            graph.add_node(node)

        # 5. 圓角 (Fillets)
        for idx, fil in enumerate(feat.fillets):
            r = round(fil.get("radius", 0.2), 3)
            center = fil.get("center", [0.0, 0.0, 0.0])
            c_pos = float(center[axis_idx])

            node = FeatureNode(
                id=f"fillet_{idx + 1:02d}",
                feature_type="transition_fillet",
                nominal={"radius": r},
                axial_span=[round(c_pos - r, 3), round(c_pos + r, 3)],
                center_axial=round(c_pos, 3),
                source_info={"type": "fillet"}
            )
            graph.add_node(node)

    # =========================================================================
    # 通用幾何特徵圖構建
    # =========================================================================
    def _build_general_graph(self, feat: FeatureExtractor, graph: FeatureRelationGraph):
        for idx, cyl in enumerate(feat.cylinders_raw):
            c_dia = round(cyl.get("diameter", 0.0), 3)
            c_len = round(cyl.get("length", 0.0), 3)
            node = FeatureNode(
                id=f"feat_cyl_{idx + 1:02d}",
                feature_type="shaft_segment" if not cyl.get("is_hole") else "hole",
                nominal={"diameter": c_dia, "length": c_len},
                axial_span=[0.0, c_len],
                center_axial=c_len / 2.0
            )
            graph.add_node(node)

    # =========================================================================
    # 拓撲排序與鄰接關係鏈接 (Adjacency Resolver)
    # =========================================================================
    def _link_adjacency_relations(self, graph: FeatureRelationGraph):
        if not graph.nodes:
            return

        # 依軸向中心座標排序
        graph.nodes.sort(key=lambda n: n.center_axial)

        n_count = len(graph.nodes)
        for i, curr_node in enumerate(graph.nodes):
            # 判定邊界狀態
            if i == 0:
                curr_node.boundary_position = "LEFT_END"
            elif i == n_count - 1:
                curr_node.boundary_position = "RIGHT_END"
            else:
                curr_node.boundary_position = "INTERIOR"

            # 鄰接左側與右側節點
            left_node = graph.nodes[i - 1] if i > 0 else None
            right_node = graph.nodes[i + 1] if i < n_count - 1 else None

            curr_node.adjacent_left_type = left_node.feature_type if left_node else "NONE"
            curr_node.adjacent_right_type = right_node.feature_type if right_node else "NONE"

            # 收集鄰近 15mm 範圍內的特徵類型清單
            neighbors = set()
            for other in graph.nodes:
                if other.id != curr_node.id:
                    dist = abs(other.center_axial - curr_node.center_axial)
                    if dist <= 12.0:
                        neighbors.add(other.feature_type)
            if curr_node.boundary_position in ("LEFT_END", "RIGHT_END"):
                neighbors.add("shaft_end")
            curr_node.neighbor_types = sorted(list(neighbors))

            # 初步機能意圖推測 (Rule-Based Semantic Inference)
            curr_node.inferred_role = self._infer_semantic_role(curr_node, graph)

    # =========================================================================
    # 幾何語意與機能角色啟發式推論 (Semantic Role Inference)
    # =========================================================================
    def _infer_semantic_role(self, node: FeatureNode, graph: FeatureRelationGraph) -> str:
        f_type = node.feature_type
        neighbors = node.neighbor_types
        nom = node.nominal

        if f_type == "retaining_ring_groove":
            return "RETAINING_RING_GROOVE"
        elif f_type == "pilot_chamfer":
            return "PILOT_LEAD_IN"
        elif f_type == "locating_shoulder":
            return "AXIAL_LOCATING_SHOULDER"
        elif f_type == "transition_fillet":
            return "STRESS_RELIEF_FILLET"
        elif f_type == "shaft_segment":
            dia = nom.get("diameter", 0.0)
            length = nom.get("length", 0.0)

            # 軸承配合位判定特徵：鄰接卡簧槽或定位軸肩，且直徑在 2~10mm 標準軸承規格
            if ("retaining_ring_groove" in neighbors or "locating_shoulder" in neighbors) and (2.0 <= dia <= 12.0):
                return "BEARING_JOURNAL"
            # 葉輪過盈配合段：位於軸端邊界且長度適中
            elif node.boundary_position in ("LEFT_END", "RIGHT_END") and length < 10.0:
                return "PRESS_FIT_HUB"
            # 長主軸主體
            elif length > 12.0:
                return "MAIN_SHAFT_BODY"
            else:
                return "GENERAL_FIT_JOURNAL"

        return "GENERAL_FEATURE"
