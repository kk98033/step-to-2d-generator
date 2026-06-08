"""
自動尺寸標註引擎 v7 — 頂點偵測 + 最長邊 + 相鄰標註

核心邏輯:
1. 從投影後的 2D 邊緣找出所有頂點 (輪廓轉折點)
2. 依照最長邊 + 相鄰標註法: 
   假設找到頂點 1,2,3,4:
   最外層: 1------4  (最長邊 = 總長)
   內層:   1-2       (相鄰)
   內層:      2-3    (相鄰)
   內層:         3-4 (相鄰)
3. 直徑標註: 在垂直方向找出不同直徑段
4. 俯視圖/右視圖也使用同樣的頂點標註法
"""
from config import DIM_STYLE


class DimensionEngine:
    """自動尺寸標註引擎 — 頂點全組合累進式"""

    def __init__(self, feature_data, layout):
        self.feat = feature_data
        self.layout = layout
        self.ctrl_idx = 0

    def _next_ctrl_letter(self):
        letter = chr(65 + self.ctrl_idx)
        self.ctrl_idx += 1
        return letter

    def annotate_all_views(self, msp, view_data=None):
        self.ctrl_idx = 0
        self.view_data = view_data
        for vn in ['front', 'top', 'right']:
            ox, oy = self.layout.get_view_offset(vn)
            sw, sh = self.layout.get_scaled_size(vn)
            vd = view_data[vn] if view_data else None
            self._annotate_view(msp, vn, ox, oy, sw, sh, vd)

    # =========================================================
    # 頂點偵測
    # =========================================================

    def _find_contour_vertices(self, edges, bbox, axis='x', tol=0.15):
        """
        從投影後的 2D 邊緣中找出所有頂點 (輪廓轉折點)
        
        策略: 收集所有邊的端點座標，然後聚類
        動態容差: 如果頂點太多，自動加大容差重新聚類
        
        Returns: sorted list of positions (投影座標系)
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

    def _find_diameter_changes(self, edges, bbox, axis='x', tol=0.15):
        """
        找出不同直徑段 (垂直方向的高度變化)
        
        Returns: list of (x_position, half_height) — 在各個 X 位置的半高度
        """
        # 在每個 X 位置測量 Y 跨度 → 近似直徑
        all_coords = []
        for e in edges:
            if e['type'] == 'line':
                all_coords.extend([e['p1'], e['p2']])
            elif 'points' in e:
                all_coords.extend(e['points'])

        if not all_coords:
            return []

        if axis == 'x':
            x_min = min(p[0] for p in all_coords)
            x_max = max(p[0] for p in all_coords)
            x_range = x_max - x_min
            if x_range < 0.01:
                return []

            # 在 20 個等距位置取樣 Y 跨度
            samples = []
            n_samples = 20
            for i in range(n_samples + 1):
                sample_x = x_min + (x_range * i / n_samples)
                # 找出在此 X ± tol 範圍內的所有 Y 值
                y_vals = []
                for p in all_coords:
                    if abs(p[0] - sample_x) < x_range / n_samples:
                        y_vals.append(p[1])
                if y_vals:
                    y_span = max(y_vals) - min(y_vals)
                    y_center = (max(y_vals) + min(y_vals)) / 2
                    samples.append((sample_x, y_span / 2, y_center))
            return samples
        else:
            # Similar for Y axis
            y_min = min(p[1] for p in all_coords)
            y_max = max(p[1] for p in all_coords)
            y_range = y_max - y_min
            if y_range < 0.01:
                return []
            samples = []
            n_samples = 20
            for i in range(n_samples + 1):
                sample_y = y_min + (y_range * i / n_samples)
                x_vals = []
                for p in all_coords:
                    if abs(p[1] - sample_y) < y_range / n_samples:
                        x_vals.append(p[0])
                if x_vals:
                    x_span = max(x_vals) - min(x_vals)
                    x_center = (max(x_vals) + min(x_vals)) / 2
                    samples.append((sample_y, x_span / 2, x_center))
            return samples

    # =========================================================
    # 座標轉換
    # =========================================================

    def _proj_to_paper_x(self, proj_x, bbox_x0, scale, ox):
        """投影座標 → 圖紙座標 (X)"""
        return ox + (proj_x - bbox_x0) * scale

    def _proj_to_paper_y(self, proj_y, bbox_y0, scale, oy):
        """投影座標 → 圖紙座標 (Y)"""
        return oy + (proj_y - bbox_y0) * scale

    # =========================================================
    # 標註繪製
    # =========================================================

    def _draw_hdim(self, msp, x1, x2, y, text, layer_offset=0):
        """繪製水平尺寸標註線"""
        if abs(x2 - x1) < 1.0:
            return
        # 尺寸線
        msp.add_line((x1, y), (x2, y), dxfattribs={'layer': 'DIM', 'color': 2})
        # 箭頭
        arr = min(1.5, abs(x2 - x1) * 0.08)
        msp.add_line((x1, y), (x1 + arr, y + 0.7), dxfattribs={'layer': 'DIM', 'color': 2})
        msp.add_line((x1, y), (x1 + arr, y - 0.7), dxfattribs={'layer': 'DIM', 'color': 2})
        msp.add_line((x2, y), (x2 - arr, y + 0.7), dxfattribs={'layer': 'DIM', 'color': 2})
        msp.add_line((x2, y), (x2 - arr, y - 0.7), dxfattribs={'layer': 'DIM', 'color': 2})
        # 文字
        tx = (x1 + x2) / 2
        self._add_text(msp, tx - 3, y + 1.0, text, height=1.5)

    def _draw_vdim(self, msp, y1, y2, x, text, layer_offset=0):
        """繪製垂直尺寸標註線"""
        if abs(y2 - y1) < 1.0:
            return
        msp.add_line((x, y1), (x, y2), dxfattribs={'layer': 'DIM', 'color': 2})
        arr = min(1.5, abs(y2 - y1) * 0.08)
        msp.add_line((x, y1), (x + 0.7, y1 + arr), dxfattribs={'layer': 'DIM', 'color': 2})
        msp.add_line((x, y1), (x - 0.7, y1 + arr), dxfattribs={'layer': 'DIM', 'color': 2})
        msp.add_line((x, y2), (x + 0.7, y2 - arr), dxfattribs={'layer': 'DIM', 'color': 2})
        msp.add_line((x, y2), (x - 0.7, y2 - arr), dxfattribs={'layer': 'DIM', 'color': 2})
        tx = x + 1.5
        ty = (y1 + y2) / 2
        self._add_text(msp, tx, ty, text, height=1.5)

    # =========================================================
    # 全組合累進式標註
    # =========================================================

    def _dim_progressive(self, msp, vertices_proj, bbox_start, scale, origin,
                         contour_edge, direction='horizontal', paper_contour_pos=None):
        """
        最長邊 + 相鄰標註法
        
        vertices_proj: 投影座標系中的頂點列表 (sorted)
        direction: 'horizontal' = 下方標 / 'vertical' = 右側標
        contour_edge: 輪廓邊緣位置 (圖紙座標)
        
        標註佈局 (假設頂點 1,2,3,4):
        Layer 1 (最外層): 1------4   (最長邊 = 總長)
        Layer 2 (靠近):   1-2         (相鄰)
        Layer 3 (靠近):      2-3      (相鄰)
        Layer 4 (靠近):         3-4   (相鄰)
        """
        n = len(vertices_proj)
        if n < 2:
            return

        # 轉換為圖紙座標
        if direction == 'horizontal':
            verts_paper = [self._proj_to_paper_x(v, bbox_start, scale, origin) for v in vertices_proj]
        else:
            verts_paper = [self._proj_to_paper_y(v, bbox_start, scale, origin) for v in vertices_proj]

        # 產生標註對: 最長邊 + 相鄰對
        dim_pairs = []

        # 1) 最長邊 (第一個到最後一個) — 放在最外層
        total_dist = abs(vertices_proj[-1] - vertices_proj[0])
        total_paper = abs(verts_paper[-1] - verts_paper[0])
        if total_paper >= 3.0:
            dim_pairs.append((0, n - 1, total_dist))

        # 2) 相鄰對 (i 到 i+1) — 放在靠近輪廓的層
        for i in range(n - 1):
            real_dist = abs(vertices_proj[i + 1] - vertices_proj[i])
            paper_dist = abs(verts_paper[i + 1] - verts_paper[i])
            if paper_dist < 3.0:
                continue
            dim_pairs.append((i, i + 1, real_dist))

        if not dim_pairs:
            return

        layer_spacing = 5.0
        ext_gap = 2.5
        total_layers = len(dim_pairs)
        max_extent = ext_gap + total_layers * layer_spacing + 3

        # 繪製延伸線 (從輪廓到標註區域)
        for vi, vp in enumerate(verts_paper):
            if direction == 'horizontal':
                msp.add_line((vp, contour_edge), (vp, contour_edge - max_extent),
                             dxfattribs={'layer': 'DIM', 'color': 2})
            else:
                msp.add_line((contour_edge, vp), (contour_edge + max_extent, vp),
                             dxfattribs={'layer': 'DIM', 'color': 2})

        # 繪製各層標註
        # 最長邊在最外層 (layer 0 = 最遠), 相鄰對在內層 (靠近輪廓)
        for layer_idx, (si, ei, real_dist) in enumerate(dim_pairs):
            dim_text = f"{real_dist:.2f}"
            # 最長邊 (index 0) 放最外層, 相鄰對從內層開始
            actual_layer = total_layers - 1 - layer_idx if layer_idx == 0 else layer_idx - 1

            if direction == 'horizontal':
                dim_y = contour_edge - ext_gap - actual_layer * layer_spacing
                self._draw_hdim(msp, verts_paper[si], verts_paper[ei], dim_y, dim_text)
            else:
                dim_x = contour_edge + ext_gap + actual_layer * layer_spacing
                self._draw_vdim(msp, verts_paper[si], verts_paper[ei], dim_x, dim_text)

    # =========================================================
    # 視圖標註主邏輯
    # =========================================================

    def _annotate_view(self, msp, view_name, ox, oy, sw, sh, vd):
        dimstyle = DIM_STYLE["name"]

        if vd:
            w_real = vd['size'][0]
            h_real = vd['size'][1]
            bbox = vd['bbox']
            all_edges = vd['visible'] + vd['hidden']
            vis_edges = vd['visible']
        else:
            w_real = sw
            h_real = sh
            bbox = (0, 0, sw, sh)
            all_edges = []
            vis_edges = []

        scale = self.layout.scale

        # === 前視圖: 完整標註 ===
        if view_name == 'front' and vis_edges:
            # 判斷主軸方向 (投影後的形狀)
            if w_real > h_real * 1.2:
                # 主軸沿水平方向
                # 1. 水平方向頂點 → 底部累進標註
                h_verts = self._find_contour_vertices(vis_edges, bbox, axis='x')
                if len(h_verts) >= 2:
                    self._dim_progressive(msp, h_verts, bbox[0], scale, ox,
                                         contour_edge=oy, direction='horizontal')
                # 2. 垂直方向頂點 → 右側累進標註 (直徑方向)
                v_verts = self._find_contour_vertices(vis_edges, bbox, axis='y')
                if len(v_verts) >= 2:
                    self._dim_progressive(msp, v_verts, bbox[1], scale, oy,
                                         contour_edge=ox + sw, direction='vertical')
            elif h_real > w_real * 1.2:
                # 主軸沿垂直方向
                v_verts = self._find_contour_vertices(vis_edges, bbox, axis='y')
                if len(v_verts) >= 2:
                    self._dim_progressive(msp, v_verts, bbox[1], scale, oy,
                                         contour_edge=ox + sw, direction='vertical')
                h_verts = self._find_contour_vertices(vis_edges, bbox, axis='x')
                if len(h_verts) >= 2:
                    self._dim_progressive(msp, h_verts, bbox[0], scale, ox,
                                         contour_edge=oy, direction='horizontal')
            else:
                # 近似正方形 — 兩個方向都標
                h_verts = self._find_contour_vertices(vis_edges, bbox, axis='x')
                if len(h_verts) >= 2:
                    self._dim_progressive(msp, h_verts, bbox[0], scale, ox,
                                         contour_edge=oy, direction='horizontal')
                v_verts = self._find_contour_vertices(vis_edges, bbox, axis='y')
                if len(v_verts) >= 2:
                    self._dim_progressive(msp, v_verts, bbox[1], scale, oy,
                                         contour_edge=ox + sw, direction='vertical')

            # 3. 規格字串
            spec = self.feat.get_overall_spec()
            if spec:
                self._add_text(msp, ox, oy + sh + 6, f"規格: {spec}", height=2.0, layer='TEXT')

            # 4. 直徑標註 (段差直徑)
            self._dim_diameters(msp, ox, oy, sw, sh, vd)

        # === 俯視圖 / 右視圖: 同樣使用頂點標註 ===
        elif view_name in ('top', 'right') and vis_edges:
            # 水平方向頂點
            h_verts = self._find_contour_vertices(vis_edges, bbox, axis='x')
            if len(h_verts) >= 2:
                self._dim_progressive(msp, h_verts, bbox[0], scale, ox,
                                     contour_edge=oy, direction='horizontal')
            # 垂直方向頂點
            v_verts = self._find_contour_vertices(vis_edges, bbox, axis='y')
            if len(v_verts) >= 2:
                self._dim_progressive(msp, v_verts, bbox[1], scale, oy,
                                     contour_edge=ox + sw, direction='vertical')
        elif view_name in ('top', 'right'):
            # Fallback: 如果沒有邊緣資料，用簡單的整體尺寸
            self._dim_overall_simple(msp, ox, oy, sw, sh, w_real, h_real, dimstyle)

    def _dim_overall_simple(self, msp, ox, oy, sw, sh, w_real, h_real, dimstyle):
        """簡單的整體尺寸標註 (俯視圖/右視圖)"""
        gap = 8
        if sw > 1.0:
            try:
                dim = msp.add_linear_dim(
                    base=(ox + sw / 2, oy - gap),
                    p1=(ox, oy), p2=(ox + sw, oy),
                    dimstyle=dimstyle,
                    override={'dimtxt': 2.5, 'dimasz': 1.5}
                )
                dim.render()
            except Exception:
                pass
        if sh > 1.0:
            try:
                dim = msp.add_linear_dim(
                    base=(ox + sw + gap, oy + sh / 2),
                    p1=(ox + sw, oy), p2=(ox + sw, oy + sh),
                    angle=90,
                    dimstyle=dimstyle,
                    override={'dimtxt': 2.5, 'dimasz': 1.5}
                )
                dim.render()
            except Exception:
                pass

    def _dim_diameters(self, msp, ox, oy, sw, sh, vd):
        """直徑標註 — 從段差特徵提取"""
        step_info = self.feat.get_step_dims_for_view('front')
        segments = step_info.get("segments", [])
        if not segments:
            return

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
            return

        max_dia = max(s["diameter"] for s in unique_dias)

        # 在前視圖左上方標註各段直徑
        for i, seg in enumerate(unique_dias[:5]):
            dia = seg["diameter"]
            ctrl = self._next_ctrl_letter()
            tol = "±0.05" if dia < 10 else "±0.10"

            label_x = ox - 5 - i * 10
            label_y = oy + sh + 3 + i * 5

            # 引線從輪廓到標註
            dia_ratio = dia / max_dia if max_dia > 0 else 1.0
            half_w = sw / 2 * dia_ratio
            cx = ox + sw / 2

            msp.add_line((cx - half_w, oy + sh / 2), (label_x + 8, label_y),
                         dxfattribs={'layer': 'LEADER', 'color': 2})
            self._add_text(msp, label_x - 5, label_y + 1, f"({ctrl})Ø{dia:.2f}", height=1.5, layer='DIM')
            self._add_text(msp, label_x - 5, label_y - 1.5, tol, height=1.0, layer='TOLERANCE')

    def _add_text(self, msp, x, y, text, height=1.8, layer='DIM', color=None):
        attribs = {'layer': layer, 'insert': (x, y), 'style': 'CHINESE'}
        if color:
            attribs['color'] = color
        msp.add_text(text, height=height, dxfattribs=attribs)
