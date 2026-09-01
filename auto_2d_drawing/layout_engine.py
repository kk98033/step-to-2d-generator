"""
通用排版引擎 (Layout Engine) — 零件無關

接收 List[DimensionTask]，計算排版位置，處理碰撞偵測，最終渲染到 DXF。
這個引擎完全不需要知道零件是軸還是風扇。
"""
try:
    from config import DIM_STYLE
except ImportError:
    from auto_2d_drawing.config import DIM_STYLE


class LayoutEngine:
    """通用尺寸標註排版引擎"""

    def __init__(self, drawing_layout):
        """
        Args:
            drawing_layout: DrawingLayout 實例 (包含 scale 等資訊)
        """
        self.layout = drawing_layout
        self.layer_spacing = 8.0
        self.ext_gap = 12.0

    # =================================================================
    # 主入口
    # =================================================================

    def render(self, msp, tasks, ox, oy, sw, sh, vd):
        """
        接收標註任務列表，排版並渲染到 DXF modelspace。

        Args:
            msp: DXF modelspace
            tasks: List[DimensionTask]
            ox, oy: 視圖在圖紙上的偏移座標
            sw, sh: 視圖的縮放後寬高
            vd: 視圖投影資料
        """
        if not tasks:
            return

        bbox = vd['bbox'] if vd else (0, 0, sw, sh)
        scale = self.layout.scale

        # 按 side 或 dim_type 分組
        bottom_tasks = [t for t in tasks if t.side == "BOTTOM" and t.dim_type == "LINEAR"]
        right_tasks = [t for t in tasks if t.side == "RIGHT" and t.dim_type == "LINEAR"]
        left_tasks = [t for t in tasks if t.side == "LEFT" and t.dim_type == "DIAMETER" and not t.center]
        top_tasks = [t for t in tasks if t.side == "TOP" and t.dim_type == "LINEAR"]
        
        # 極座標與特殊任務
        centerline_tasks = [t for t in tasks if t.dim_type == "CENTERLINES"]
        polar_dia_tasks = [t for t in tasks if t.dim_type == "DIAMETER" and t.center]
        angular_tasks = [t for t in tasks if t.dim_type == "ANGULAR"]
        leader_tasks = [t for t in tasks if t.dim_type == "LEADER"]
        note_tasks = [t for t in tasks if t.dim_type == "NOTE"]

        # 渲染各方向
        if bottom_tasks:
            self._render_horizontal(msp, bottom_tasks, ox, oy, sw, sh, bbox, scale, side="BOTTOM")
        if top_tasks:
            self._render_horizontal(msp, top_tasks, ox, oy, sw, sh, bbox, scale, side="TOP")
        if right_tasks:
            self._render_vertical(msp, right_tasks, ox, oy, sw, sh, bbox, scale, side="RIGHT")
        if left_tasks:
            self._render_left_diameters(msp, left_tasks, ox, oy, sw, sh, bbox, scale)
            
        # 渲染極座標與特殊任務
        if centerline_tasks:
            self._render_centerlines(msp, centerline_tasks, ox, oy, bbox, scale)
        if polar_dia_tasks:
            self._render_diameters_polar(msp, polar_dia_tasks, ox, oy, bbox, scale)
        if angular_tasks:
            self._render_angular(msp, angular_tasks, ox, oy, bbox, scale)
        if leader_tasks:
            self._render_leaders(msp, leader_tasks, ox, oy, bbox, scale)
        if note_tasks:
            self._render_notes(msp, note_tasks, ox, oy, bbox, scale)

    # =================================================================
    # 水平標註渲染 (BOTTOM / TOP)
    # =================================================================

    def _render_horizontal(self, msp, tasks, ox, oy, sw, sh, bbox, scale, side="BOTTOM"):
        """渲染水平方向的標註 (底部或頂部)"""
        # 分離基線標註和串聯標註
        left_baseline = [t for t in tasks if t.baseline == "LEFT" and t.rank == 1]
        right_baseline = [t for t in tasks if t.baseline == "RIGHT" and t.rank == 1]
        overall = [t for t in tasks if t.rank >= 2]
        chain = [t for t in tasks if t.baseline == "NONE" and t.rank == 1]

        has_baseline = bool(left_baseline or right_baseline)

        if has_baseline:
            # === 雙向基線標註 ===
            left_baseline.sort(key=lambda t: t.value)
            right_baseline.sort(key=lambda t: t.value)

            num_layers = max(len(left_baseline), len(right_baseline))
            max_extent = self.ext_gap + num_layers * self.layer_spacing + 2

            contour_edge = oy if side == "BOTTOM" else oy + sh

            # 找出左右邊界的圖紙座標
            all_x_proj = set()
            for t in tasks:
                all_x_proj.add(t.start_proj[0])
                all_x_proj.add(t.end_proj[0])

            if not all_x_proj:
                return

            x_min_proj = min(all_x_proj)
            x_max_proj = max(all_x_proj)
            left_paper = self._proj_to_paper_x(x_min_proj, bbox[0], scale, ox)
            right_paper = self._proj_to_paper_x(x_max_proj, bbox[0], scale, ox)

            # 畫左右基準延伸線
            sign = -1 if side == "BOTTOM" else 1
            msp.add_line(
                (left_paper, contour_edge),
                (left_paper, contour_edge + sign * max_extent),
                dxfattribs={'layer': 'DIM', 'color': 2}
            )
            msp.add_line(
                (right_paper, contour_edge),
                (right_paper, contour_edge + sign * max_extent),
                dxfattribs={'layer': 'DIM', 'color': 2}
            )

            # 逐層渲染
            for i in range(num_layers):
                layer_pos = contour_edge + sign * (self.ext_gap + i * self.layer_spacing)

                # 左側基準標註
                if i < len(left_baseline):
                    t = left_baseline[i]
                    end_paper = self._proj_to_paper_x(t.end_proj[0], bbox[0], scale, ox)
                    msp.add_line(
                        (end_paper, contour_edge),
                        (end_paper, layer_pos + sign * (-2)),
                        dxfattribs={'layer': 'DIM', 'color': 2}
                    )
                    self._draw_hdim(msp, left_paper, end_paper, layer_pos, t.display_text)

                # 右側基準標註
                if i < len(right_baseline):
                    t = right_baseline[i]
                    start_paper = self._proj_to_paper_x(t.start_proj[0], bbox[0], scale, ox)
                    msp.add_line(
                        (start_paper, contour_edge),
                        (start_paper, layer_pos + sign * (-2)),
                        dxfattribs={'layer': 'DIM', 'color': 2}
                    )
                    self._draw_hdim(msp, start_paper, right_paper, layer_pos, t.display_text)

            if overall:
                overall.sort(key=lambda t: (t.rank, -t.value))
                for oi, t in enumerate(overall):
                    p1 = self._proj_to_paper_x(t.start_proj[0], bbox[0], scale, ox)
                    p2 = self._proj_to_paper_x(t.end_proj[0], bbox[0], scale, ox)
                    overall_pos = contour_edge + sign * (self.ext_gap + (num_layers + oi) * self.layer_spacing)
                    self._draw_hdim(msp, p1, p2, overall_pos, t.display_text)

        elif chain:
            # === 串聯標註 ===
            contour_edge = oy if side == "BOTTOM" else oy + sh
            sign = -1 if side == "BOTTOM" else 1

            has_overall = bool(overall)
            total_layers = 1 + len(overall) if has_overall else 1
            max_extent = self.ext_gap + total_layers * self.layer_spacing + 2

            # 建立每個端點對應的真實物理輪廓 Y 座標映射 (避免延伸線浮空)
            point_y_map = {}
            all_papers = set()
            for t in chain + overall:
                if t.start_proj:
                    px1 = self._proj_to_paper_x(t.start_proj[0], bbox[0], scale, ox)
                    py1 = self._proj_to_paper_y(t.start_proj[1], bbox[1], scale, oy)
                    all_papers.add(px1)
                    if px1 not in point_y_map or (side == "BOTTOM" and py1 > point_y_map[px1]) or (side == "TOP" and py1 < point_y_map[px1]):
                        point_y_map[px1] = py1
                if t.end_proj:
                    px2 = self._proj_to_paper_x(t.end_proj[0], bbox[0], scale, ox)
                    py2 = self._proj_to_paper_y(t.end_proj[1], bbox[1], scale, oy)
                    all_papers.add(px2)
                    if px2 not in point_y_map or (side == "BOTTOM" and py2 > point_y_map[px2]) or (side == "TOP" and py2 < point_y_map[px2]):
                        point_y_map[px2] = py2

            # 畫延伸線 (從實體邊界角點直連到尺寸線)
            for vp in all_papers:
                start_y = point_y_map.get(vp, contour_edge)
                msp.add_line(
                    (vp, start_y),
                    (vp, contour_edge + sign * max_extent),
                    dxfattribs={'layer': 'DIM', 'color': 2}
                )

            # 內層: 相鄰對
            inner_pos = contour_edge + sign * self.ext_gap
            char_width = 1.3
            for idx, t in enumerate(chain):
                p1 = self._proj_to_paper_x(t.start_proj[0], bbox[0], scale, ox)
                p2 = self._proj_to_paper_x(t.end_proj[0], bbox[0], scale, ox)
                paper_dist = abs(p2 - p1)
                stagger = 0.0
                req_space = len(t.display_text) * char_width + 1.0
                if paper_dist < req_space:
                    stagger = 2.5 if idx % 2 == 0 else -2.5
                self._draw_hdim(msp, p1, p2, inner_pos, t.display_text, text_stagger=stagger)

            if overall:
                overall.sort(key=lambda t: (t.rank, -t.value))
                for oi, t in enumerate(overall):
                    outer_pos = inner_pos + sign * self.layer_spacing * (oi + 1)
                    p1 = self._proj_to_paper_x(t.start_proj[0], bbox[0], scale, ox)
                    p2 = self._proj_to_paper_x(t.end_proj[0], bbox[0], scale, ox)
                    self._draw_hdim(msp, p1, p2, outer_pos, t.display_text)

        elif overall:
            # 只有總尺寸
            contour_edge = oy if side == "BOTTOM" else oy + sh
            sign = -1 if side == "BOTTOM" else 1
            overall.sort(key=lambda t: (t.rank, -t.value))
            for oi, t in enumerate(overall):
                p1 = self._proj_to_paper_x(t.start_proj[0], bbox[0], scale, ox)
                p2 = self._proj_to_paper_x(t.end_proj[0], bbox[0], scale, ox)
                py1 = self._proj_to_paper_y(t.start_proj[1], bbox[1], scale, oy) if t.start_proj else contour_edge
                py2 = self._proj_to_paper_y(t.end_proj[1], bbox[1], scale, oy) if t.end_proj else contour_edge
                pos = contour_edge + sign * (self.ext_gap + oi * self.layer_spacing)
                msp.add_line((p1, py1), (p1, pos + sign * (-2)), dxfattribs={'layer': 'DIM', 'color': 2})
                msp.add_line((p2, py2), (p2, pos + sign * (-2)), dxfattribs={'layer': 'DIM', 'color': 2})
                self._draw_hdim(msp, p1, p2, pos, t.display_text)

    # =================================================================
    # 垂直標註渲染 (RIGHT / LEFT for linear)
    # =================================================================

    def _render_vertical(self, msp, tasks, ox, oy, sw, sh, bbox, scale, side="RIGHT"):
        """渲染垂直方向的線性標註"""
        top_baseline = [t for t in tasks if t.baseline == "TOP" and t.rank == 1]
        bottom_baseline = [t for t in tasks if t.baseline == "BOTTOM" and t.rank == 1]
        overall = [t for t in tasks if t.rank >= 2 and t.dim_type == "LINEAR"]
        chain = [t for t in tasks if t.baseline == "NONE" and t.rank == 1 and t.dim_type == "LINEAR"]

        has_baseline = bool(top_baseline or bottom_baseline)

        if has_baseline:
            # === 基線標註 (分層標註) ===
            top_baseline.sort(key=lambda t: t.value)
            bottom_baseline.sort(key=lambda t: t.value)

            num_layers = max(len(top_baseline), len(bottom_baseline))
            max_extent = self.ext_gap + num_layers * self.layer_spacing + 2

            contour_edge = ox + sw if side == "RIGHT" else ox
            sign = 1 if side == "RIGHT" else -1

            # 找出上下邊界的圖紙座標
            all_y_proj = set()
            for t in tasks:
                all_y_proj.add(t.start_proj[1])
                all_y_proj.add(t.end_proj[1])

            if not all_y_proj:
                return

            y_min_proj = min(all_y_proj)
            y_max_proj = max(all_y_proj)
            bottom_paper = self._proj_to_paper_y(y_min_proj, bbox[1], scale, oy)
            top_paper = self._proj_to_paper_y(y_max_proj, bbox[1], scale, oy)

            # 畫上下基準延伸線
            msp.add_line(
                (contour_edge, bottom_paper),
                (contour_edge + sign * max_extent, bottom_paper),
                dxfattribs={'layer': 'DIM', 'color': 2}
            )
            msp.add_line(
                (contour_edge, top_paper),
                (contour_edge + sign * max_extent, top_paper),
                dxfattribs={'layer': 'DIM', 'color': 2}
            )

            # 逐層渲染
            for i in range(num_layers):
                layer_pos = contour_edge + sign * (self.ext_gap + i * self.layer_spacing)

                # 底部基準標註
                if i < len(bottom_baseline):
                    t = bottom_baseline[i]
                    end_paper = self._proj_to_paper_y(t.end_proj[1], bbox[1], scale, oy)
                    msp.add_line(
                        (contour_edge, end_paper),
                        (layer_pos + sign * (-2), end_paper),
                        dxfattribs={'layer': 'DIM', 'color': 2}
                    )
                    self._draw_vdim(msp, bottom_paper, end_paper, layer_pos, t.display_text)

                # 頂部基準標註
                if i < len(top_baseline):
                    t = top_baseline[i]
                    start_paper = self._proj_to_paper_y(t.start_proj[1], bbox[1], scale, oy)
                    msp.add_line(
                        (contour_edge, start_paper),
                        (layer_pos + sign * (-2), start_paper),
                        dxfattribs={'layer': 'DIM', 'color': 2}
                    )
                    self._draw_vdim(msp, start_paper, top_paper, layer_pos, t.display_text)

            if overall:
                overall.sort(key=lambda t: (t.rank, -t.value))
                for oi, t in enumerate(overall):
                    p1 = self._proj_to_paper_y(t.start_proj[1], bbox[1], scale, oy)
                    p2 = self._proj_to_paper_y(t.end_proj[1], bbox[1], scale, oy)
                    overall_pos = contour_edge + sign * (self.ext_gap + (num_layers + oi) * self.layer_spacing)
                    self._draw_vdim(msp, p1, p2, overall_pos, t.display_text)

        elif chain:
            # === 串聯標註 ===
            contour_edge = ox + sw if side == "RIGHT" else ox
            sign = 1 if side == "RIGHT" else -1

            has_overall = bool(overall)
            total_layers = 1 + len(overall) if has_overall else 1
            max_extent = self.ext_gap + total_layers * self.layer_spacing + 2

            # 收集端點
            all_papers = set()
            for t in chain + overall:
                all_papers.add(self._proj_to_paper_y(t.start_proj[1], bbox[1], scale, oy))
                all_papers.add(self._proj_to_paper_y(t.end_proj[1], bbox[1], scale, oy))

            # 延伸線
            for vp in all_papers:
                msp.add_line(
                    (contour_edge, vp),
                    (contour_edge + sign * max_extent, vp),
                    dxfattribs={'layer': 'DIM', 'color': 2}
                )

            # 內層
            inner_pos = contour_edge + sign * self.ext_gap
            char_width = 1.3
            for idx, t in enumerate(chain):
                p1 = self._proj_to_paper_y(t.start_proj[1], bbox[1], scale, oy)
                p2 = self._proj_to_paper_y(t.end_proj[1], bbox[1], scale, oy)
                paper_dist = abs(p2 - p1)
                stagger = 0.0
                req_space = len(t.display_text) * char_width + 1.0
                if paper_dist < req_space:
                    stagger = 2.5 if idx % 2 == 0 else -2.5
                self._draw_vdim(msp, p1, p2, inner_pos, t.display_text, text_stagger=stagger)

            if overall:
                overall.sort(key=lambda t: (t.rank, -t.value))
                for oi, t in enumerate(overall):
                    outer_pos = inner_pos + sign * self.layer_spacing * (oi + 1)
                    p1 = self._proj_to_paper_y(t.start_proj[1], bbox[1], scale, oy)
                    p2 = self._proj_to_paper_y(t.end_proj[1], bbox[1], scale, oy)
                    self._draw_vdim(msp, p1, p2, outer_pos, t.display_text)

        elif overall:
            contour_edge = ox + sw if side == "RIGHT" else ox
            sign = 1 if side == "RIGHT" else -1
            overall.sort(key=lambda t: (t.rank, -t.value))
            for oi, t in enumerate(overall):
                pos = contour_edge + sign * (self.ext_gap + oi * self.layer_spacing)
                p1 = self._proj_to_paper_y(t.start_proj[1], bbox[1], scale, oy)
                p2 = self._proj_to_paper_y(t.end_proj[1], bbox[1], scale, oy)
                msp.add_line((contour_edge, p1), (pos + sign * (-2), p1), dxfattribs={'layer': 'DIM', 'color': 2})
                msp.add_line((contour_edge, p2), (pos + sign * (-2), p2), dxfattribs={'layer': 'DIM', 'color': 2})
                self._draw_vdim(msp, p1, p2, pos, t.display_text)

    # =================================================================
    # 左側直徑標註渲染 (特殊: 水平延伸線 + 垂直尺寸線)
    # =================================================================

    def _render_left_diameters(self, msp, tasks, ox, oy, sw, sh, bbox, scale):
        """渲染左側的直徑標註 (水平延伸線 + 垂直尺寸線)"""
        dia_tasks = [t for t in tasks if t.dim_type == "DIAMETER"]
        if not dia_tasks:
            return

        # 依直徑值去重並按直徑大小升序排列 (小徑在內層、大徑在外層)
        seen_dias = {}
        for t in dia_tasks:
            d_val = round(t.value, 2)
            if d_val not in seen_dias:
                seen_dias[d_val] = t
            elif t.tolerance and not seen_dias[d_val].tolerance:
                seen_dias[d_val] = t

        sorted_tasks = sorted(seen_dias.values(), key=lambda t: t.value)

        for i, t in enumerate(sorted_tasks[:4]):
            label_x = ox - 12 - i * 11
            y_center = oy + sh / 2
            half_h = (t.value * scale) / 2
            y1 = y_center - half_h
            y2 = y_center + half_h

            # 特徵真實紙張 X 座標 (延伸線從實體輪廓角點向左拉出)
            feat_x = ox
            if t.start_proj:
                px = self._proj_to_paper_x(t.start_proj[0], bbox[0], scale, ox)
                if ox <= px <= ox + sw:
                    feat_x = px

            # 水平延伸線 (牢牢連到零件實體邊緣)
            msp.add_line((feat_x, y1), (label_x - 1.5, y1), dxfattribs={'layer': 'DIM', 'color': 2})
            msp.add_line((feat_x, y2), (label_x - 1.5, y2), dxfattribs={'layer': 'DIM', 'color': 2})

            # 垂直尺寸線
            self._draw_vdim(msp, y1, y2, label_x, t.display_text)

            # 公差
            if t.tolerance:
                self._add_text(msp, label_x + 1.2, y_center - 2.5, t.tolerance,
                               height=1.0, layer='TOLERANCE', color=4)

    # =================================================================
    # 極座標與圓形標註渲染 (CENTERLINES, DIAMETER, ANGULAR)
    # =================================================================

    def _render_centerlines(self, msp, tasks, ox, oy, bbox, scale):
        """渲染中心線 (支援線性軸心線與圓形中心十字線)"""
        for t in tasks:
            # 1. 線性中心軸線 (例如旋轉軸中心線)
            if t.start_proj and t.end_proj and (abs(t.start_proj[0] - t.end_proj[0]) > 0.1 or abs(t.start_proj[1] - t.end_proj[1]) > 0.1):
                x1 = self._proj_to_paper_x(t.start_proj[0], bbox[0], scale, ox)
                y1 = self._proj_to_paper_y(t.start_proj[1], bbox[1], scale, oy)
                x2 = self._proj_to_paper_x(t.end_proj[0], bbox[0], scale, ox)
                y2 = self._proj_to_paper_y(t.end_proj[1], bbox[1], scale, oy)
                dx = x2 - x1
                dy = y2 - y1
                dist = (dx * dx + dy * dy) ** 0.5
                if dist > 0.1:
                    ext = 3.0
                    ux = dx / dist
                    uy = dy / dist
                    p1 = (x1 - ux * ext, y1 - uy * ext)
                    p2 = (x2 + ux * ext, y2 + uy * ext)
                    msp.add_line(p1, p2, dxfattribs={'layer': 'CENTER', 'color': 1, 'linetype': 'CENTER2'})
            # 2. 極座標中心十字線與 PCD 圓
            elif t.center:
                cx = self._proj_to_paper_x(t.center[0], bbox[0], scale, ox)
                cy = self._proj_to_paper_y(t.center[1], bbox[1], scale, oy)
                r = (t.radius if t.radius and t.radius > 0 else max(bbox[2] - bbox[0], bbox[3] - bbox[1]) / 2.0) * scale + 2.0
                
                if getattr(t, 'text', '') == "PCD_CIRCLE":
                    msp.add_circle((cx, cy), r, dxfattribs={'layer': 'CENTER', 'color': 8, 'linetype': 'CENTER2'})
                else:
                    msp.add_line((cx - r, cy), (cx + r, cy), dxfattribs={'layer': 'CENTER', 'color': 1, 'linetype': 'CENTER2'})
                    msp.add_line((cx, cy - r), (cx, cy + r), dxfattribs={'layer': 'CENTER', 'color': 1, 'linetype': 'CENTER2'})

    def _render_diameters_polar(self, msp, tasks, ox, oy, bbox, scale):
        """渲染傾斜拉出的直徑標註"""
        import math
        for t in tasks:
            center_x = t.center[0] if t.center else 0.0
            center_y = t.center[1] if t.center else 0.0
            cx = self._proj_to_paper_x(center_x, bbox[0], scale, ox)
            cy = self._proj_to_paper_y(center_y, bbox[1], scale, oy)
            r_paper = t.radius * scale
            ang_rad = math.radians(t.angle if t.angle else 45.0)
            
            # 從中心出發
            dx = r_paper * math.cos(ang_rad)
            dy = r_paper * math.sin(ang_rad)
            p1 = (cx, cy)
            p2 = (cx + dx, cy + dy)
            
            # 拉伸線
            ext_len = 8.0
            p3 = (cx + (r_paper + ext_len) * math.cos(ang_rad), 
                  cy + (r_paper + ext_len) * math.sin(ang_rad))
            
            msp.add_line(p2, p3, dxfattribs={'layer': 'DIM', 'color': 2})
            
            # 水平尾巴
            tail_dir = 1 if math.cos(ang_rad) >= 0 else -1
            text_len = len(t.display_text) * 1.5
            landing_len = max(8.0, text_len + 2.0)
            p4 = (p3[0] + tail_dir * landing_len, p3[1])
            msp.add_line(p3, p4, dxfattribs={'layer': 'DIM', 'color': 2})
            
            # 箭頭 (指向圓周 p2)
            arr = 1.5
            arr_dx = math.cos(ang_rad) * arr
            arr_dy = math.sin(ang_rad) * arr
            sdx = -math.sin(ang_rad) * arr * 0.35
            sdy = math.cos(ang_rad) * arr * 0.35
            
            msp.add_line(p2, (p2[0] + arr_dx + sdx, p2[1] + arr_dy + sdy), dxfattribs={'layer': 'DIM', 'color': 2})
            msp.add_line(p2, (p2[0] + arr_dx - sdx, p2[1] + arr_dy - sdy), dxfattribs={'layer': 'DIM', 'color': 2})
            
            # 文字
            tx = p3[0] + (1.0 if tail_dir > 0 else (-landing_len + 1.0))
            ty = p3[1] + 0.8
            self._add_text(msp, tx, ty, t.display_text, height=1.8, layer='DIM', color=2)

    def _render_angular(self, msp, tasks, ox, oy, bbox, scale):
        """渲染角度標註"""
        import math
        for t in tasks:
            center_x = t.center[0] if t.center else 0.0
            center_y = t.center[1] if t.center else 0.0
            cx = self._proj_to_paper_x(center_x, bbox[0], scale, ox)
            cy = self._proj_to_paper_y(center_y, bbox[1], scale, oy)
            r_paper = t.radius * scale
            
            a1_rad = math.radians(t.angle)
            a2_rad = math.radians(t.angle + t.value)
            
            # 畫引導線
            h1 = (cx + r_paper * math.cos(a1_rad), cy + r_paper * math.sin(a1_rad))
            h2 = (cx + r_paper * math.cos(a2_rad), cy + r_paper * math.sin(a2_rad))
            
            msp.add_line((cx, cy), h1, dxfattribs={'layer': 'CENTER2', 'color': 8, 'linetype': 'CENTER2'})
            msp.add_line((cx, cy), h2, dxfattribs={'layer': 'CENTER2', 'color': 8, 'linetype': 'CENTER2'})
            
            try:
                dim_r = r_paper * 0.6
                base_ang = (a1_rad + a2_rad) / 2
                base = (cx + dim_r * math.cos(base_ang), cy + dim_r * math.sin(base_ang))
                
                dim = msp.add_angular_dim_3p(
                    base=base,
                    center=(cx, cy),
                    p1=h1,
                    p2=h2,
                    dimstyle=DIM_STYLE["name"],
                    override={'dimtxt': 2.0, 'dimasz': 1.5, 'dimclrt': 2}
                )
                
                if t.display_text:
                    dim.dxf.text = t.display_text
                dim.render()
            except Exception as e:
                print(f"Angular dim error: {e}")

    # =================================================================
    # 引線標註空間防碰撞排版與渲染 (Collision Avoidance Leader Engine)
    # =================================================================

    def _layout_leaders_without_collision(self, tasks, ox, oy, bbox, scale):
        """
        引線空間防碰撞最佳化排版演算法:
        1. 空間幾何聚類 (Spatial Clustering by anchor position)
        2. 扇形角度自適應分佈 (Adaptive Fan-out Angle Assignment)
        3. 多層次階梯伸長 (Multi-Tier Extension Staggering)
        4. 2D Bounding Box 碰撞檢測與迭代避讓 (AABB Collision Resolver)
        """
        import math
        sw = abs(bbox[2] - bbox[0]) * scale
        sh = abs(bbox[3] - bbox[1]) * scale
        mid_x = ox + sw / 2.0
        mid_y = oy + sh / 2.0

        # 1. 取得每個任務的紙張座標起點 p_start
        leader_items = []
        for t in tasks:
            if t.center and t.radius and t.radius > 0:
                # 極座標特徵 (由圓周出發)
                cx = self._proj_to_paper_x(t.center[0], bbox[0], scale, ox)
                cy = self._proj_to_paper_y(t.center[1], bbox[1], scale, oy)
                r_paper = t.radius * scale
                default_ang = t.angle if t.angle else 45.0
                ang_rad = math.radians(default_ang)
                p_start = (cx + r_paper * math.cos(ang_rad), cy + r_paper * math.sin(ang_rad))
            else:
                # 輪廓邊緣頂點
                start_x = t.start_proj[0] if t.start_proj else 0.0
                start_y = t.start_proj[1] if t.start_proj else 0.0
                cx = self._proj_to_paper_x(start_x, bbox[0], scale, ox)
                cy = self._proj_to_paper_y(start_y, bbox[1], scale, oy)
                p_start = (cx, cy)
                default_ang = t.angle if t.angle else 45.0

            # 依零件中線動態判定偏向 (左半側 vs 右半側)
            pref_side = "LEFT" if p_start[0] < mid_x else "RIGHT"

            leader_items.append({
                "task": t,
                "p_start": p_start,
                "default_ang": default_ang,
                "pref_side": pref_side,
                "text": t.display_text,
            })

        if not leader_items:
            return []

        # 2. 空間聚類 (依據 p_start 的 X 座標將相近特徵分組)
        leader_items.sort(key=lambda item: item["p_start"][0])

        clusters = []
        curr_cluster = []
        cluster_tol = max(4.0, sw * 0.15)  # 動態依零件尺寸自適應聚類容差

        for item in leader_items:
            if not curr_cluster:
                curr_cluster.append(item)
            else:
                last_p = curr_cluster[-1]["p_start"]
                dist_x = abs(item["p_start"][0] - last_p[0])
                if dist_x <= cluster_tol:
                    curr_cluster.append(item)
                else:
                    clusters.append(curr_cluster)
                    curr_cluster = [item]
        if curr_cluster:
            clusters.append(curr_cluster)

        # 3. 針對每個 Cluster 指派初始扇形角度與階梯伸長
        positioned_leaders = []
        occupied_bboxes = []  # 已定案標註之 [(xmin, ymin, xmax, ymax)]

        def check_bbox_overlap(box1, box2, buffer=1.8):
            return not (box1[2] + buffer < box2[0] or 
                        box1[0] - buffer > box2[2] or 
                        box1[3] + buffer < box2[1] or 
                        box1[1] - buffer > box2[3])

        for cluster in clusters:
            k = len(cluster)
            pref_side = cluster[0]["pref_side"]

            # 安全扇形角度區間:
            # 左半部特徵: 斜向右上 (58° ~ 35°)，向零件中段開闊區延伸，避開左側尺寸與正上方視圖
            # 右半部特徵: 斜向左上 (110° ~ 135°)，同樣朝零件中段開闊區延伸
            if pref_side == "LEFT":
                if k == 1:
                    angles = [50.0]
                elif k <= 3:
                    span_start = 55.0
                    span_end = 40.0
                    step_ang = (span_end - span_start) / max(1, k - 1)
                    angles = [span_start + i * step_ang for i in range(k)]
                else:
                    span_start = 58.0
                    span_end = 35.0
                    step_ang = (span_end - span_start) / max(1, k - 1)
                    angles = [span_start + i * step_ang for i in range(k)]
            else:
                if k == 1:
                    angles = [115.0]
                elif k <= 3:
                    span_start = 110.0
                    span_end = 130.0
                    step_ang = (span_end - span_start) / max(1, k - 1)
                    angles = [span_start + i * step_ang for i in range(k)]
                else:
                    span_start = 105.0
                    span_end = 135.0
                    step_ang = (span_end - span_start) / max(1, k - 1)
                    angles = [span_start + i * step_ang for i in range(k)]

            # 4. 碰撞檢測與幾何邊界動態限制
            for idx, item in enumerate(cluster):
                p_start = item["p_start"]
                ang_deg = angles[idx]
                text = item["text"]
                text_len = len(text) * 1.5
                landing_len = max(8.0, text_len + 2.0)

                base_ext = 8.0 + idx * 3.5
                max_iter = 15
                curr_ext = base_ext
                curr_ang = ang_deg

                for it in range(max_iter):
                    rad = math.radians(curr_ang)
                    p_elbow = (p_start[0] + curr_ext * math.cos(rad),
                               p_start[1] + curr_ext * math.sin(rad))

                    # 決定停機坪方向：
                    # 左半側統一向右 (tail_dir = 1)，右半側統一向左 (tail_dir = -1)
                    if pref_side == "LEFT":
                        tail_dir = 1
                    else:
                        tail_dir = -1

                    p_end = (p_elbow[0] + tail_dir * landing_len, p_elbow[1])

                    # 檢查上邊界：動態依視圖高度限制最高伸長高度
                    top_ceiling = oy + sh + 28.0
                    if p_elbow[1] > top_ceiling:
                        curr_ext = max(8.0, curr_ext - 2.0)
                        p_elbow = (p_start[0] + curr_ext * math.cos(rad),
                                   p_start[1] + curr_ext * math.sin(rad))
                        p_end = (p_elbow[0] + tail_dir * landing_len, p_elbow[1])

                    tx = p_elbow[0] + (1.0 if tail_dir > 0 else (-landing_len + 1.0))
                    ty = p_elbow[1] + 0.8
                    bx1 = min(p_elbow[0], p_end[0], tx)
                    bx2 = max(p_elbow[0], p_end[0], tx + text_len)
                    by1 = p_elbow[1] - 0.5
                    by2 = ty + 2.0
                    candidate_bbox = (bx1, by1, bx2, by2)

                    has_collision = False
                    for occ in occupied_bboxes:
                        if check_bbox_overlap(candidate_bbox, occ, buffer=1.5):
                            has_collision = True
                            break

                    if not has_collision:
                        occupied_bboxes.append(candidate_bbox)
                        positioned_leaders.append({
                            "task": item["task"],
                            "p_start": p_start,
                            "p_elbow": p_elbow,
                            "p_end": p_end,
                            "ang_deg": curr_ang,
                            "tail_dir": tail_dir,
                            "tx": tx,
                            "ty": ty,
                            "text": text,
                            "landing_len": landing_len,
                        })
                        break
                    else:
                        curr_ext += 3.2
                        # 角度微調：始終保持在安全錐區 55° ~ 125° 之間
                        if curr_ang > 90.0:
                            curr_ang = max(85.0, curr_ang - 2.5)
                        else:
                            curr_ang = min(95.0, curr_ang + 2.5)
                else:
                    occupied_bboxes.append(candidate_bbox)
                    positioned_leaders.append({
                        "task": item["task"],
                        "p_start": p_start,
                        "p_elbow": p_elbow,
                        "p_end": p_end,
                        "ang_deg": curr_ang,
                        "tail_dir": tail_dir,
                        "tx": tx,
                        "ty": ty,
                        "text": text,
                        "landing_len": landing_len,
                    })

        return positioned_leaders

    def _render_leaders(self, msp, tasks, ox, oy, bbox, scale):
        """渲染單箭頭引線與停機坪 (具備智慧空間防碰撞排版)"""
        import math
        leaders_layout = self._layout_leaders_without_collision(tasks, ox, oy, bbox, scale)

        for item in leaders_layout:
            p_start = item["p_start"]
            p_elbow = item["p_elbow"]
            p_end = item["p_end"]
            ang_rad = math.radians(item["ang_deg"])
            tx = item["tx"]
            ty = item["ty"]
            text = item["text"]

            # 1. 繪製引線主體與水平停機坪
            msp.add_line(p_start, p_elbow, dxfattribs={'layer': 'DIM', 'color': 2})
            msp.add_line(p_elbow, p_end, dxfattribs={'layer': 'DIM', 'color': 2})

            # 2. 繪製箭頭 (指向特徵起點 p_start)
            arr = 1.5
            arr_dx = math.cos(ang_rad) * arr
            arr_dy = math.sin(ang_rad) * arr
            sdx = -math.sin(ang_rad) * arr * 0.35
            sdy = math.cos(ang_rad) * arr * 0.35

            msp.add_line(p_start, (p_start[0] + arr_dx + sdx, p_start[1] + arr_dy + sdy),
                         dxfattribs={'layer': 'DIM', 'color': 2})
            msp.add_line(p_start, (p_start[0] + arr_dx - sdx, p_start[1] + arr_dy - sdy),
                         dxfattribs={'layer': 'DIM', 'color': 2})

            # 3. 繪製文字 (停機坪上方)
            self._add_text(msp, tx, ty, text, height=1.8, layer='DIM', color=2)

    def _render_notes(self, msp, tasks, ox, oy, bbox, scale):
        """渲染全域工藝註解"""
        # 放於圖紙左下角或指定位置
        start_y = oy + 20
        start_x = ox + 10
        for i, t in enumerate(tasks):
            self._add_text(msp, start_x, start_y + i * 5, t.display_text, height=2.5, layer='DIM', color=3)

    # =================================================================
    # 座標轉換
    # =================================================================

    def _proj_to_paper_x(self, proj_x, bbox_x0, scale, ox):
        return ox + (proj_x - bbox_x0) * scale

    def _proj_to_paper_y(self, proj_y, bbox_y0, scale, oy):
        return oy + (proj_y - bbox_y0) * scale

    # =================================================================
    # 底層繪圖工具
    # =================================================================

    def _draw_hdim(self, msp, x1, x2, y, text, text_stagger=0.0):
        """繪製水平尺寸標註線 (支援窄間隙反向箭頭與階梯錯位)"""
        span = abs(x2 - x1)
        if span < 0.2:
            return
        
        # 主尺寸線
        msp.add_line((x1, y), (x2, y), dxfattribs={'layer': 'DIM', 'color': 2})
        
        # 箭頭判斷: 窄間隙 (span < 4.5mm) 採用外側反向箭頭
        arr = min(1.5, max(0.8, span * 0.2))
        if span < 4.5:
            # 外側反向箭頭指向端點
            msp.add_line((x1 - arr, y + 0.6), (x1, y), dxfattribs={'layer': 'DIM', 'color': 2})
            msp.add_line((x1 - arr, y - 0.6), (x1, y), dxfattribs={'layer': 'DIM', 'color': 2})
            msp.add_line((x2 + arr, y + 0.6), (x2, y), dxfattribs={'layer': 'DIM', 'color': 2})
            msp.add_line((x2 + arr, y - 0.6), (x2, y), dxfattribs={'layer': 'DIM', 'color': 2})
        else:
            # 內側常規箭頭
            msp.add_line((x1, y), (x1 + arr, y + 0.6), dxfattribs={'layer': 'DIM', 'color': 2})
            msp.add_line((x1, y), (x1 + arr, y - 0.6), dxfattribs={'layer': 'DIM', 'color': 2})
            msp.add_line((x2, y), (x2 - arr, y + 0.6), dxfattribs={'layer': 'DIM', 'color': 2})
            msp.add_line((x2, y), (x2 - arr, y - 0.6), dxfattribs={'layer': 'DIM', 'color': 2})
        
        tx = (x1 + x2) / 2
        ty = y + 0.8 + text_stagger
        self._add_text(msp, tx - (len(text) * 0.45), ty, text, height=1.5)
        if abs(text_stagger) > 0.1:
            msp.add_line((tx, y), (tx, ty - 0.2), dxfattribs={'layer': 'DIM', 'color': 8})

    def _draw_vdim(self, msp, y1, y2, x, text, text_stagger=0.0):
        """繪製垂直尺寸標註線"""
        if abs(y2 - y1) < 1.0:
            return
        msp.add_line((x, y1), (x, y2), dxfattribs={'layer': 'DIM', 'color': 2})
        arr = min(1.5, abs(y2 - y1) * 0.08)
        msp.add_line((x, y1), (x + 0.7, y1 + arr), dxfattribs={'layer': 'DIM', 'color': 2})
        msp.add_line((x, y1), (x - 0.7, y1 + arr), dxfattribs={'layer': 'DIM', 'color': 2})
        msp.add_line((x, y2), (x + 0.7, y2 - arr), dxfattribs={'layer': 'DIM', 'color': 2})
        msp.add_line((x, y2), (x - 0.7, y2 - arr), dxfattribs={'layer': 'DIM', 'color': 2})
        tx = x + 1.5 + text_stagger
        ty = (y1 + y2) / 2
        self._add_text(msp, tx, ty - 0.75, text, height=1.5)
        if abs(text_stagger) > 0.1:
            msp.add_line((x, ty), (tx - 0.3, ty), dxfattribs={'layer': 'DIM', 'color': 8})

    def _add_text(self, msp, x, y, text, height=1.8, layer='DIM', color=None):
        attribs = {'layer': layer, 'insert': (x, y), 'style': 'CHINESE'}
        if color:
            attribs['color'] = color
        msp.add_text(text, height=height, dxfattribs=attribs)
