"""
通用 Fallback 提取器 (Generic Extractor)

當零件類型無法辨識時使用。
邏輯極簡: 掃描 X/Y 軸頂點 → 串聯標註 + 總尺寸。
"""
from extractors.base_extractor import BaseExtractor
from dimension_task import DimensionTask


class GenericExtractor(BaseExtractor):
    """通用零件標註任務提取器"""

    def extract(self, feature_data, view_data, view_name):
        vd = view_data
        if not vd or not vd.get('visible'):
            return []

        vis_edges = vd['visible']
        tasks = []

        # 水平方向
        h_verts = self._find_contour_vertices(vis_edges, axis='x')
        if len(h_verts) >= 2:
            # 相鄰對
            for i in range(len(h_verts) - 1):
                dist = abs(h_verts[i + 1] - h_verts[i])
                tasks.append(DimensionTask(
                    dim_type="LINEAR",
                    start_proj=(h_verts[i], 0),
                    end_proj=(h_verts[i + 1], 0),
                    value=dist,
                    side="BOTTOM",
                    rank=1,
                    view_name=view_name,
                ))
            # 總長度
            if len(h_verts) > 2:
                tasks.append(DimensionTask(
                    dim_type="LINEAR",
                    start_proj=(h_verts[0], 0),
                    end_proj=(h_verts[-1], 0),
                    value=abs(h_verts[-1] - h_verts[0]),
                    side="BOTTOM",
                    rank=2,
                    view_name=view_name,
                ))

        # 垂直方向
        v_verts = self._find_contour_vertices(vis_edges, axis='y')
        if len(v_verts) >= 2:
            for i in range(len(v_verts) - 1):
                dist = abs(v_verts[i + 1] - v_verts[i])
                tasks.append(DimensionTask(
                    dim_type="LINEAR",
                    start_proj=(0, v_verts[i]),
                    end_proj=(0, v_verts[i + 1]),
                    value=dist,
                    side="RIGHT",
                    rank=1,
                    view_name=view_name,
                ))
            if len(v_verts) > 2:
                tasks.append(DimensionTask(
                    dim_type="LINEAR",
                    start_proj=(0, v_verts[0]),
                    end_proj=(0, v_verts[-1]),
                    value=abs(v_verts[-1] - v_verts[0]),
                    side="RIGHT",
                    rank=2,
                    view_name=view_name,
                ))

        return tasks
