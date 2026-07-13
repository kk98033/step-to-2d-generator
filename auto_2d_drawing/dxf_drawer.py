"""
DXF 繪圖核心模組 — 將 2D 投影邊緣繪製到 DXF 模型空間
"""
from config import PAPER_SIZES, MARGIN, VIEW_CONFIG


class DrawingLayout:
    """三視圖佈局管理器 — 第三角投影法"""

    def __init__(self, view_sizes, tasks_dict=None, paper="A3"):
        """
        Args:
            view_sizes: {'front': (w,h), 'top': (w,h), 'right': (w,h)}
            tasks_dict: {'front': [tasks...], ...} 提前提取出的標註任務
            paper: 'A3' or 'A4'
        """
        self.paper_w, self.paper_h = PAPER_SIZES[paper]
        self.margin = MARGIN
        self.view_sizes = view_sizes
        self.tasks_dict = tasks_dict or {}
        self.title_h = 60    # 標題欄區域高度
        self.revision_h = 30  # 版次欄區域高度
        
        # 來自 LayoutEngine 的排版參數 (需同步)
        self.ext_gap = 12.0
        self.layer_spacing = 8.0
        
        self._calculate_scale()

    def _get_view_padding(self, view_name):
        """
        估算某視圖四周標註所需的空間 (單位: mm 圖紙空間)
        回傳 (pad_top, pad_bottom, pad_left, pad_right)
        """
        tasks = self.tasks_dict.get(view_name, [])
        if not tasks:
            return (0, 0, 0, 0)
            
        def get_layers(side, dim_type="LINEAR"):
            baseline_tasks = [t for t in tasks if t.baseline == side and t.rank == 1 and t.dim_type == dim_type]
            return len(set(t.value for t in baseline_tasks)) if baseline_tasks else 0
            
        def get_overall(side):
            return len(set(
                (getattr(t, 'rank', 0), round(getattr(t, 'value', 0), 2))
                for t in tasks
                if getattr(t, 'rank', 0) >= 2 and getattr(t, 'side', None) == side
            ))

        # 計算各方向的層數 = baseline 分層數 + overall 總標註
        layers_bottom = get_layers("BOTTOM") + get_overall("BOTTOM")
        layers_top = get_layers("TOP") + get_overall("TOP")
        layers_left = get_layers("LEFT") + get_overall("LEFT")
        layers_right = get_layers("RIGHT") + get_overall("RIGHT")
        
        # 基礎邊界 (即使沒有標註也保留些微空間給標籤與引線)
        pad_base = 15.0
        
        pad_bottom = pad_base + (self.ext_gap + layers_bottom * self.layer_spacing if layers_bottom > 0 else 0)
        pad_top    = pad_base + (self.ext_gap + layers_top * self.layer_spacing if layers_top > 0 else 0)
        pad_left   = pad_base + (self.ext_gap + layers_left * self.layer_spacing if layers_left > 0 else 0)
        pad_right  = pad_base + (self.ext_gap + layers_right * self.layer_spacing if layers_right > 0 else 0)
        
        # 標籤空間
        pad_bottom += 15.0 # 視圖文字標籤
        
        return (pad_top, pad_bottom, pad_left, pad_right)

    def _calculate_scale(self):
        """自動計算最佳縮放比例與動態排版間距，並考慮標註空間"""
        fw, fh = self.view_sizes.get('front', (0, 0))
        rw, rh = self.view_sizes.get('right', (0, 0))
        tw, th = self.view_sizes.get('top', (0, 0))

        has_right = rw > 0.1
        has_top = tw > 0.1

        # 取得各視圖的所需 Padding (top, bottom, left, right)
        f_pt, f_pb, f_pl, f_pr = self._get_view_padding('front')
        r_pt, r_pb, r_pl, r_pr = self._get_view_padding('right')
        t_pt, t_pb, t_pl, t_pr = self._get_view_padding('top')

        # 組合總 Padding
        margin_left = f_pl
        margin_bottom = f_pb
        
        # 決定右側邊界
        margin_right = r_pr if has_right else f_pr
        # 決定頂部邊界
        margin_top = t_pt if has_top else f_pt

        # 圖框內可用區域
        draw_w = self.paper_w - 2 * self.margin
        draw_h = self.paper_h - 2 * self.margin - self.title_h - self.revision_h

        avail_w = draw_w - margin_left - margin_right
        avail_h = draw_h - margin_bottom - margin_top

        # 動態間距：包含兩個視圖之間的 Padding (前視圖的右 Pad + 右視圖的左 Pad)
        # 再加上一些基礎視覺間距 (30.0)
        gap_x = (f_pr + r_pl + 30.0) if has_right else 0.0
        gap_y = (f_pt + t_pb + 30.0) if has_top else 0.0

        fw_total = fw + rw if has_right else fw
        fh_total = fh + th if has_top else fh

        scale_x = (avail_w - gap_x) / fw_total if fw_total > 0 else 1.0
        scale_y = (avail_h - gap_y) / fh_total if fh_total > 0 else 1.0

        # 取得較嚴格的縮放比例
        self.scale = min(scale_x, scale_y)

        # 限制縮放範圍
        self.scale = min(self.scale, 5.0)
        self.scale = max(self.scale, 0.05)

        self.gap_x = gap_x
        self.gap_y = gap_y
        self.start_x = self.margin + margin_left
        self.start_y = self.margin + self.title_h + margin_bottom

    def get_scale_text(self):
        """回傳比例文字 (如 '1:2', '2:1')"""
        if self.scale >= 1.0:
            return f"{self.scale:.0f} : 1" if self.scale == int(self.scale) else f"{self.scale:.1f} : 1"
        else:
            inv = 1.0 / self.scale
            return f"1 : {inv:.0f}" if inv == int(inv) else f"1 : {inv:.1f}"

    def get_view_offset(self, view_name):
        """
        回傳指定視圖在圖面上的起始座標 (ox, oy)
        
        第三角投影法佈局:
          [top]     在前視圖正上方
          [front]   在左下
          [right]   在前視圖正右方
        """
        s = self.scale
        fw, fh = self.view_sizes.get('front', (100, 100))

        if view_name == 'front':
            return self.start_x, self.start_y
        elif view_name == 'top':
            return self.start_x, self.start_y + fh * s + self.gap_y
        elif view_name == 'right':
            return self.start_x + fw * s + self.gap_x, self.start_y
        return 0, 0

    def get_scaled_size(self, view_name):
        """回傳視圖的縮放後尺寸"""
        w, h = self.view_sizes.get(view_name, (0, 0))
        return w * self.scale, h * self.scale


class DxfDrawer:
    """DXF 繪圖工具 — 將邊緣資料繪製到模型空間"""

    def draw_edges(self, msp, edges, ox, oy, scale, bbox_x0, bbox_y0, layer='VISIBLE'):
        """
        繪製一組邊緣到 DXF 模型空間。
        
        Args:
            msp: DXF modelspace
            edges: list of edge dicts from ViewProjector
            ox, oy: 圖面偏移
            scale: 縮放比例
            bbox_x0, bbox_y0: 邊緣原始 bbox 原點
            layer: DXF 圖層名稱
        """
        for e in edges:
            if e['type'] == 'line':
                x1, y1 = e['p1']
                x2, y2 = e['p2']
                msp.add_line(
                    (ox + (x1 - bbox_x0) * scale, oy + (y1 - bbox_y0) * scale),
                    (ox + (x2 - bbox_x0) * scale, oy + (y2 - bbox_y0) * scale),
                    dxfattribs={'layer': layer}
                )
            elif e['type'] in ('circle', 'spline'):
                pts = e.get('points', [])
                if len(pts) >= 2:
                    scaled_pts = [
                        (ox + (p[0] - bbox_x0) * scale, oy + (p[1] - bbox_y0) * scale)
                        for p in pts
                    ]
                    msp.add_lwpolyline(scaled_pts, dxfattribs={'layer': layer})

    def draw_bbox_outline(self, msp, ox, oy, scale, bbox, layer='VISIBLE'):
        """補畫零件投影外輪廓，避免單面可見線缺少外框。"""
        x0, y0, x1, y1 = bbox
        pts = [
            (ox, oy),
            (ox + (x1 - x0) * scale, oy),
            (ox + (x1 - x0) * scale, oy + (y1 - y0) * scale),
            (ox, oy + (y1 - y0) * scale),
        ]
        msp.add_lwpolyline(pts, close=True, dxfattribs={'layer': layer})

    def draw_view_border(self, msp, ox, oy, sw, sh):
        """繪製視圖邊界框 (淡色虛線)"""
        msp.add_lwpolyline(
            [(ox - 1, oy - 1), (ox + sw + 1, oy - 1),
             (ox + sw + 1, oy + sh + 1), (ox - 1, oy + sh + 1)],
            close=True,
            dxfattribs={'layer': 'BORDER', 'color': 9}
        )

    def draw_view_label(self, msp, ox, oy, label_text):
        """繪製視圖標籤"""
        msp.add_text(
            label_text, height=2.2,
            dxfattribs={'layer': 'TEXT', 'insert': (ox, oy - 5), 'style': 'CHINESE'}
        )
