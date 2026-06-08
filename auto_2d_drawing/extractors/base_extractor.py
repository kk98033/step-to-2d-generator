"""
抽象基底提取器 — 所有特化提取器必須繼承此類別
"""
from abc import ABC, abstractmethod


class BaseExtractor(ABC):
    """
    特化提取器的抽象基底類別。

    子類別必須實作 extract() 方法，回傳 List[DimensionTask]。
    """

    @abstractmethod
    def extract(self, feature_data, view_data, view_name):
        """
        從 3D 幾何特徵與 2D 投影資料中，提取出該零件類型在指定視圖中需要的標註任務。

        Args:
            feature_data: FeatureExtractor 實例 (包含 3D 幾何特徵)
            view_data: 單一視圖的投影資料 dict:
                       {'visible': [...], 'hidden': [...], 'bbox': (x0,y0,x1,y1), 'size': (w,h)}
            view_name: 視圖名稱 — "front", "top", "right"

        Returns:
            List[DimensionTask]: 該視圖所需的全部標註任務
        """
        raise NotImplementedError

    def _find_contour_vertices(self, edges, axis='x', tol=0.15):
        """
        從投影後的 2D 邊緣中找出所有頂點 (輪廓轉折點)。
        這是一個通用的工具方法，所有子類別都可以使用。

        Args:
            edges: 邊緣列表 (來自 ViewProjector)
            axis: 'x' 或 'y'
            tol: 聚類容差

        Returns:
            sorted list of positions (投影座標系)
        """
        positions = set()

        for e in edges:
            if e['type'] == 'line':
                p1, p2 = e['p1'], e['p2']
                if axis == 'x':
                    positions.add(p1[0])
                    positions.add(p2[0])
                else:
                    positions.add(p1[1])
                    positions.add(p2[1])
            elif 'points' in e:
                pts = e['points']
                if pts:
                    positions.add(pts[0][0] if axis == 'x' else pts[0][1])
                    positions.add(pts[-1][0] if axis == 'x' else pts[-1][1])

        if not positions:
            return []

        sorted_v = sorted(positions)

        # 計算動態容差: 基於整體尺寸的 0.5%，至少 tol
        total_range = sorted_v[-1] - sorted_v[0] if len(sorted_v) > 1 else 1.0
        base_tol = max(tol, total_range * 0.005)

        # 迭代聚類 — 如果頂點太多就加大容差
        max_vertices = 10
        current_tol = base_tol

        for attempt in range(10):
            clustered = [sorted_v[0]]
            for v in sorted_v[1:]:
                if abs(v - clustered[-1]) > current_tol:
                    clustered.append(v)
                else:
                    clustered[-1] = (clustered[-1] + v) / 2

            if len(clustered) <= max_vertices:
                return clustered

            # 頂點太多，加大容差
            current_tol *= 1.8

        return clustered[:max_vertices]
