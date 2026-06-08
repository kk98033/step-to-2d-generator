"""
DXF 繪圖核心模組 — 將 2D 投影邊緣繪製到 DXF 模型空間
"""
from config import PAPER_SIZES, MARGIN, VIEW_CONFIG


class DrawingLayout:
    """三視圖佈局管理器 — 第三角投影法"""

    def __init__(self, view_sizes, paper="A3"):
        """
        Args:
            view_sizes: {'front': (w,h), 'top': (w,h), 'right': (w,h)}
            paper: 'A3' or 'A4'
        """
        self.paper_w, self.paper_h = PAPER_SIZES[paper]
        self.margin = MARGIN
        self.view_sizes = view_sizes
        self.title_h = 60    # 標題欄區域高度
        self.revision_h = 30  # 版次欄區域高度
        self._calculate_scale()

    def _calculate_scale(self):
        """自動計算最佳縮放比例與動態排版間距"""
        fw, fh = self.view_sizes.get('front', (100, 100))
        rw, rh = self.view_sizes.get('right', (0, 0))
        tw, th = self.view_sizes.get('top', (0, 0))

        has_right = rw > 1.0
        has_top = th > 1.0

        # 圖框內可用區域
        draw_w = self.paper_w - 2 * self.margin
        draw_h = self.paper_h - 2 * self.margin - self.title_h - self.revision_h

        # 預留邊界空間給最外圍的尺寸標註
        margin_left = 35.0
        margin_bottom = 35.0
        margin_right = 35.0 if not has_right else 15.0
        margin_top = 35.0 if not has_top else 15.0

        avail_w = draw_w - margin_left - margin_right
        avail_h = draw_h - margin_bottom - margin_top

        # 動態間距：依據可用空間比例分配，但也設定安全上下限
        # 保障前視圖與其他視圖之間有足夠的引線空間
        gap_x = max(50.0, min(90.0, avail_w * 0.25)) if has_right else 0.0
        gap_y = max(50.0, min(90.0, avail_h * 0.25)) if has_top else 0.0

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
