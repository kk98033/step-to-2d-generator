"""
軸類零件特化提取器 (Shaft Extractor)

負責產出軸類零件 (旋轉對稱、階梯軸、套筒等) 的標註任務。
邏輯:
  - 前視圖: 水平方向使用雙向基線標註法 (Dual-Baseline)，
            垂直方向由 3D 段差特徵提取直徑標註。
  - 俯視圖/右視圖: 使用整體外框尺寸標註。
"""
from extractors.base_extractor import BaseExtractor
from dimension_task import DimensionTask


class ShaftExtractor(BaseExtractor):
    """軸類零件標註任務提取器"""

    def extract(self, feature_data, view_data, view_name):
        """
        Args:
            feature_data: FeatureExtractor 實例
            view_data: 單一視圖的投影資料
            view_name: "front", "top", "right"

        Returns:
            List[DimensionTask]
        """
        vd = view_data
        if not vd or not vd.get('visible'):
            return []

        vis_edges = vd['visible']
        bbox = vd['bbox']
        w_real, h_real = vd['size']

        tasks = []

        if view_name == 'front':
            tasks.extend(self._extract_front_lengths(vis_edges, bbox, w_real, h_real))
            tasks.extend(self._extract_front_diameters(feature_data, vd))
        elif view_name in ('top', 'right'):
            tasks.extend(self._extract_secondary_view(vis_edges, bbox, view_name))

        return tasks

    # =================================================================
    # 前視圖: 水平長度標註 (雙向基線)
    # =================================================================

    def _extract_front_lengths(self, vis_edges, bbox, w_real, h_real):
        """提取前視圖的水平長度標註任務 (雙向基線標註法)"""
        tasks = []

        # 判斷主軸方向
        if w_real > h_real * 1.2:
            # 主軸水平 → X 軸頂點為長度標註
            h_verts = self._find_contour_vertices(vis_edges, axis='x')
            if len(h_verts) >= 2:
                tasks.extend(self._build_dual_baseline_tasks(h_verts, axis='x', side="BOTTOM"))
        elif h_real > w_real * 1.2:
            # 主軸垂直 → Y 軸頂點為長度標註
            v_verts = self._find_contour_vertices(vis_edges, axis='y')
            if len(v_verts) >= 2:
                tasks.extend(self._build_dual_baseline_tasks(v_verts, axis='y', side="RIGHT"))
            # 水平方向用簡單串聯
            h_verts = self._find_contour_vertices(vis_edges, axis='x')
            if len(h_verts) >= 2:
                tasks.extend(self._build_chain_tasks(h_verts, axis='x', side="BOTTOM"))
        else:
            # 近似正方形 → 兩個方向都標
            h_verts = self._find_contour_vertices(vis_edges, axis='x')
            if len(h_verts) >= 2:
                tasks.extend(self._build_dual_baseline_tasks(h_verts, axis='x', side="BOTTOM"))
            v_verts = self._find_contour_vertices(vis_edges, axis='y')
            if len(v_verts) >= 2:
                tasks.extend(self._build_chain_tasks(v_verts, axis='y', side="RIGHT"))

        return tasks

    def _build_dual_baseline_tasks(self, verts, axis='x', side="BOTTOM"):
        """
        雙向基線標註法: 以中線分為左右群組。
        左側特徵從最左端量測，右側特徵從最右端量測。
        最外層放總長度。
        """
        tasks = []
        n = len(verts)
        if n < 2:
            return tasks

        base_left = verts[0]
        base_right = verts[-1]
        midpoint = (base_left + base_right) / 2.0

        # 分群
        left_features = []   # (position, distance_from_left)
        right_features = []  # (position, distance_from_right)

        for i in range(1, n - 1):
            v = verts[i]
            if v <= midpoint:
                left_features.append((v, v - base_left))
            else:
                right_features.append((v, base_right - v))

        # 按距離排序
        left_features.sort(key=lambda x: x[1])
        right_features.sort(key=lambda x: x[1])

        # 左側基準標註
        for pos, dist in left_features:
            start = (base_left, 0) if axis == 'x' else (0, base_left)
            end = (pos, 0) if axis == 'x' else (0, pos)
            tasks.append(DimensionTask(
                dim_type="LINEAR",
                start_proj=start,
                end_proj=end,
                value=dist,
                side=side,
                rank=1,
                baseline="LEFT",
                view_name="front",
            ))

        # 右側基準標註
        for pos, dist in right_features:
            start = (pos, 0) if axis == 'x' else (0, pos)
            end = (base_right, 0) if axis == 'x' else (0, base_right)
            tasks.append(DimensionTask(
                dim_type="LINEAR",
                start_proj=start,
                end_proj=end,
                value=dist,
                side=side,
                rank=1,
                baseline="RIGHT",
                view_name="front",
            ))

        # 總長度
        overall = base_right - base_left
        start = (base_left, 0) if axis == 'x' else (0, base_left)
        end = (base_right, 0) if axis == 'x' else (0, base_right)
        tasks.append(DimensionTask(
            dim_type="LINEAR",
            start_proj=start,
            end_proj=end,
            value=overall,
            side=side,
            rank=2,
            baseline="NONE",
            view_name="front",
        ))

        return tasks

    def _build_chain_tasks(self, verts, axis='x', side="BOTTOM"):
        """串聯標註 (Chain Dimensioning) + 總長度"""
        tasks = []
        n = len(verts)
        if n < 2:
            return tasks

        # 內層: 相鄰對
        for i in range(n - 1):
            dist = abs(verts[i + 1] - verts[i])
            start = (verts[i], 0) if axis == 'x' else (0, verts[i])
            end = (verts[i + 1], 0) if axis == 'x' else (0, verts[i + 1])
            tasks.append(DimensionTask(
                dim_type="LINEAR",
                start_proj=start,
                end_proj=end,
                value=dist,
                side=side,
                rank=1,
                baseline="NONE",
                view_name="front",
            ))

        # 外層: 總長度
        if n > 2:
            overall = abs(verts[-1] - verts[0])
            start = (verts[0], 0) if axis == 'x' else (0, verts[0])
            end = (verts[-1], 0) if axis == 'x' else (0, verts[-1])
            tasks.append(DimensionTask(
                dim_type="LINEAR",
                start_proj=start,
                end_proj=end,
                value=overall,
                side=side,
                rank=2,
                baseline="NONE",
                view_name="front",
            ))

        return tasks

    # =================================================================
    # 前視圖: 直徑標註 (從 3D 段差特徵)
    # =================================================================

    def _extract_front_diameters(self, feature_data, vd):
        """從 FeatureExtractor 的段差特徵中提取直徑標註任務"""
        tasks = []
        step_info = feature_data.get_step_dims_for_view('front')
        segments = step_info.get("segments", [])
        if not segments:
            return tasks

        # 去重直徑
        unique_dias = []
        seen = set()
        for seg in segments:
            if seg.get("is_hole", False):
                continue
            d_key = round(seg["diameter"], 1)
            if d_key not in seen:
                seen.add(d_key)
                unique_dias.append(seg)

        if not unique_dias:
            return tasks

        w_real, h_real = vd['size']
        is_horizontal = w_real > h_real * 1.2

        ctrl_idx = 0
        for seg in unique_dias[:5]:
            dia = seg["diameter"]
            ctrl_letter = chr(65 + ctrl_idx)
            ctrl_idx += 1
            tol = "±0.05" if dia < 10 else "±0.10"

            side = "LEFT" if is_horizontal else "BOTTOM"

            tasks.append(DimensionTask(
                dim_type="DIAMETER",
                start_proj=(0, 0),
                end_proj=(0, 0),
                value=dia,
                text=f"{dia:.2f}",
                prefix=f"({ctrl_letter})Φ",
                tolerance=tol,
                side=side,
                rank=1,
                baseline="NONE",
                view_name="front",
            ))

        return tasks

    # =================================================================
    # 俯視圖 / 右視圖
    # =================================================================

    def _extract_secondary_view(self, vis_edges, bbox, view_name):
        """俯視圖/右視圖: 使用串聯標註"""
        tasks = []

        # 水平方向
        h_verts = self._find_contour_vertices(vis_edges, axis='x')
        if len(h_verts) >= 2:
            for t in self._build_chain_tasks(h_verts, axis='x', side="BOTTOM"):
                t.view_name = view_name
                tasks.append(t)

        # 垂直方向
        v_verts = self._find_contour_vertices(vis_edges, axis='y')
        if len(v_verts) >= 2:
            for t in self._build_chain_tasks(v_verts, axis='y', side="RIGHT"):
                t.view_name = view_name
                tasks.append(t)

        return tasks
