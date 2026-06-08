"""
FORCECON 標準圖框與標題欄模組
繪製符合力致科技標準的 A3 圖框、標題欄、版次欄
"""
import datetime
import ezdxf
from config import (
    PAPER_SIZES, DEFAULT_PAPER, MARGIN, COMPANY_NAME, COMPANY_FULL,
    TOLERANCE_TABLE, LAYERS, DIM_STYLE, DXF_FONT_NAME,
    COLOR_WHITE, COLOR_RED, COLOR_YELLOW, COLOR_GREEN, COLOR_CYAN
)


def setup_document():
    """建立並設定 DXF 文件 (圖層、線型、字型、標註樣式)"""
    doc = ezdxf.new('R2010')

    # 線型
    doc.linetypes.add('DASHED', pattern='A,3,-1.5')
    doc.linetypes.add('CENTER2', pattern='A,8,-1.5,1.5,-1.5')

    # 字型
    doc.styles.add('CHINESE', font=DXF_FONT_NAME)
    doc.styles.add('TITLE', font=DXF_FONT_NAME)

    # 圖層
    for name, props in LAYERS.items():
        doc.layers.add(name, color=props["color"], linetype=props["linetype"])

    # 標註樣式
    ds_cfg = DIM_STYLE
    if ds_cfg["name"] not in doc.dimstyles:
        ds = doc.dimstyles.new(ds_cfg["name"])
        ds.dxf.dimtxt = ds_cfg["dimtxt"]
        ds.dxf.dimasz = ds_cfg["dimasz"]
        ds.dxf.dimexe = ds_cfg["dimexe"]
        ds.dxf.dimexo = ds_cfg["dimexo"]
        ds.dxf.dimtmove = ds_cfg["dimtmove"]
        ds.dxf.dimdec = ds_cfg["dimdec"]
        ds.dxf.dimclrd = ds_cfg["dimclrd"]
        ds.dxf.dimclre = ds_cfg["dimclre"]
        ds.dxf.dimclrt = ds_cfg["dimclrt"]
        ds.dxf.dimgap = ds_cfg["dimgap"]

    return doc


class TitleBlock:
    """FORCECON 標準圖框 + 標題欄"""

    def __init__(self, paper="A3"):
        self.paper_w, self.paper_h = PAPER_SIZES[paper]
        self.margin = MARGIN

    def _text(self, msp, x, y, text, height=2.2, layer='TITLE_VALUE', color=None):
        attribs = {'layer': layer, 'insert': (x, y), 'style': 'CHINESE'}
        if color is not None:
            attribs['color'] = color
        msp.add_text(text, height=height, dxfattribs=attribs)

    def _line(self, msp, x1, y1, x2, y2, layer='BORDER', lineweight=None):
        attribs = {'layer': layer}
        if lineweight:
            attribs['lineweight'] = lineweight
        msp.add_line((x1, y1), (x2, y2), dxfattribs=attribs)

    def draw(self, msp, part_name="---", drawing_no="---", revision="R00",
             scale_text="1:1", material="---", model_code="---",
             designer="", checker="", approver=""):
        """繪製完整圖框"""
        self._draw_outer_border(msp)
        self._draw_zone_marks(msp)
        self._draw_title_block(msp, part_name, drawing_no, revision,
                               scale_text, material, model_code,
                               designer, checker, approver)
        self._draw_revision_block(msp, revision)

    def _draw_outer_border(self, msp):
        """繪製外框線"""
        m = self.margin
        pw, ph = self.paper_w, self.paper_h
        # 外框 (粗線)
        msp.add_lwpolyline(
            [(m, m), (pw - m, m), (pw - m, ph - m), (m, ph - m)],
            close=True,
            dxfattribs={'layer': 'BORDER', 'lineweight': 50}
        )

    def _draw_zone_marks(self, msp):
        """繪製圖框邊緣區域標記 (1-8, A-D)"""
        m = self.margin
        pw, ph = self.paper_w, self.paper_h
        zone_h = 3  # 標記區域寬度

        # 頂部和底部數字標記 (8→1)
        cols = 8
        col_w = (pw - 2 * m) / cols
        for i in range(cols):
            x = m + col_w * i
            # 頂部
            self._line(msp, x, ph - m, x, ph - m - zone_h)
            self._text(msp, x + col_w / 2 - 1, ph - m - zone_h + 0.5, str(cols - i), 1.5, 'BORDER')
            # 底部
            self._line(msp, x, m, x, m + zone_h)
            self._text(msp, x + col_w / 2 - 1, m + 0.5, str(cols - i), 1.5, 'BORDER')

        # 左側和右側字母標記 (D→A)
        rows = 4
        row_h = (ph - 2 * m) / rows
        labels = ['A', 'B', 'C', 'D']
        for i in range(rows):
            y = m + row_h * i
            # 左側
            self._line(msp, m, y, m + zone_h, y)
            self._text(msp, m + 0.5, y + row_h / 2 - 1, labels[i], 1.5, 'BORDER')
            # 右側
            self._line(msp, pw - m, y, pw - m - zone_h, y)
            self._text(msp, pw - m - 2, y + row_h / 2 - 1, labels[i], 1.5, 'BORDER')

    def _draw_title_block(self, msp, part_name, drawing_no, revision,
                          scale_text, material, model_code,
                          designer, checker, approver):
        """繪製標題欄 (右下角)"""
        m = self.margin
        pw = self.paper_w

        tb_w = 170
        tb_h = 55
        tb_x = pw - m - tb_w
        tb_y = m
        rh = tb_h / 6  # 6 列
        p = 2  # 內邊距
        today = datetime.date.today().strftime('%Y/%m/%d')

        # 外框
        msp.add_lwpolyline(
            [(tb_x, tb_y), (tb_x + tb_w, tb_y), (tb_x + tb_w, tb_y + tb_h), (tb_x, tb_y + tb_h)],
            close=True,
            dxfattribs={'layer': 'BORDER', 'lineweight': 35}
        )

        # 水平分隔線
        for i in range(1, 6):
            self._line(msp, tb_x, tb_y + rh * i, tb_x + tb_w, tb_y + rh * i)

        # 垂直分隔 — 3 大列
        cx1 = tb_x + tb_w * 0.30   # RANGE/TOLERANCE 與 MODEL 分隔
        cx2 = tb_x + tb_w * 0.55   # MODEL 與 COMPANY 分隔
        self._line(msp, cx1, tb_y, cx1, tb_y + tb_h)
        self._line(msp, cx2, tb_y, cx2, tb_y + tb_h)

        # === 左欄: RANGE / TOLERANCE (一般公差表) ===
        # Row 5 (最上方): 表頭
        r5 = tb_y + 5 * rh
        self._text(msp, tb_x + p, r5 + p, 'RANGE', 1.5, 'TITLE_LABEL')
        self._text(msp, tb_x + (cx1 - tb_x) * 0.55, r5 + p, 'TOLERANCE', 1.5, 'TITLE_LABEL')

        # Row 4~0: 公差數據
        tol_data = TOLERANCE_TABLE
        for idx, (rng, tol) in enumerate(tol_data):
            row_y = tb_y + (4 - idx) * rh
            self._text(msp, tb_x + p, row_y + p, rng, 1.8)
            self._text(msp, tb_x + (cx1 - tb_x) * 0.55, row_y + p, tol, 1.8)

        # === 中欄: MODEL / PART NO / DESIGN ===
        # Row 5: MODEL 標籤
        self._text(msp, cx1 + p, r5 + rh * 0.6, 'MODEL:', 1.3, 'TITLE_LABEL')
        # 加入第三角投影法符號位置
        self._text(msp, cx1 + p + 30, r5 + p, model_code, 2.2)

        # Row 4: 公司名 (跨右欄)
        r4 = tb_y + 4 * rh
        self._text(msp, cx2 + p, r5 + rh * 0.5, COMPANY_NAME, 3.5, 'TITLE_VALUE')
        self._text(msp, cx2 + p, r4 + p + 1, COMPANY_FULL, 2.2, 'TITLE_VALUE')

        # Row 3: PART NO.
        r3 = tb_y + 3 * rh
        self._text(msp, cx1 + p, r3 + rh * 0.65, 'PART NO.', 1.3, 'TITLE_LABEL')
        self._text(msp, cx1 + p, r3 + p, drawing_no, 2.2)
        # TITLE
        self._text(msp, cx2 + p, r3 + rh * 0.65, 'TITLE:', 1.3, 'TITLE_LABEL')
        self._text(msp, cx2 + p, r3 + p, part_name, 2.5)

        # Row 2: DESIGN
        r2 = tb_y + 2 * rh
        self._text(msp, cx1 + p, r2 + rh * 0.65, 'DESIGN', 1.3, 'TITLE_LABEL')
        self._text(msp, cx1 + p, r2 + p, designer or today, 1.8)
        # UNIT | REV
        cx3 = cx2 + (tb_x + tb_w - cx2) * 0.5
        self._line(msp, cx3, tb_y, cx3, tb_y + 3 * rh)
        self._text(msp, cx2 + p, r2 + rh * 0.65, 'UNIT:', 1.3, 'TITLE_LABEL')
        self._text(msp, cx2 + p, r2 + p, 'MM', 2.2)
        self._text(msp, cx3 + p, r2 + rh * 0.65, 'REV:', 1.3, 'TITLE_LABEL')
        self._text(msp, cx3 + p, r2 + p, revision, 2.2)

        # Row 1: CHECKED
        r1 = tb_y + rh
        self._text(msp, cx1 + p, r1 + rh * 0.65, 'CHECKED', 1.3, 'TITLE_LABEL')
        self._text(msp, cx1 + p, r1 + p, checker, 1.8)
        self._text(msp, cx2 + p, r1 + rh * 0.65, 'SCALE:', 1.3, 'TITLE_LABEL')
        self._text(msp, cx2 + p, r1 + p, scale_text, 2.2)
        self._text(msp, cx3 + p, r1 + rh * 0.65, 'SHEET', 1.3, 'TITLE_LABEL')
        self._text(msp, cx3 + p, r1 + p, '1 OF 1', 1.8)

        # Row 0: APPROVAL
        r0 = tb_y
        self._text(msp, cx1 + p, r0 + rh * 0.65, 'APPROVAL', 1.3, 'TITLE_LABEL')
        self._text(msp, cx1 + p, r0 + p, approver, 1.8)
        self._text(msp, cx2 + p, r0 + rh * 0.65, 'MATERIAL:', 1.3, 'TITLE_LABEL')
        self._text(msp, cx2 + p, r0 + p, material, 1.8)

    def _draw_revision_block(self, msp, revision):
        """繪製版次欄 (右上角)"""
        m = self.margin
        pw, ph = self.paper_w, self.paper_h
        p = 2

        rv_w = 170
        rv_h = 22
        rv_x = pw - m - rv_w
        rv_y = ph - m - rv_h - 3  # 留出上方區域標記空間

        # 外框
        msp.add_lwpolyline(
            [(rv_x, rv_y), (rv_x + rv_w, rv_y), (rv_x + rv_w, rv_y + rv_h), (rv_x, rv_y + rv_h)],
            close=True,
            dxfattribs={'layer': 'BORDER'}
        )

        # 水平分隔 (表頭)
        mid_y = rv_y + rv_h * 0.5
        self._line(msp, rv_x, mid_y, rv_x + rv_w, mid_y)

        # 垂直分隔
        cols = [0, 0.10, 0.50, 0.68, 0.80, 1.0]
        for c in cols[1:]:
            self._line(msp, rv_x + rv_w * c, rv_y, rv_x + rv_w * c, rv_y + rv_h)

        # 表頭標籤
        headers = ['REV', 'DESCRIPTION', 'DATE', 'DESIGN', 'APPROVAL']
        for i, h in enumerate(headers):
            self._text(msp, rv_x + rv_w * cols[i] + p, mid_y + p, h, 1.5, 'TITLE_LABEL')

        # 資料列
        today = datetime.date.today().strftime('%m/%d/%Y')
        self._text(msp, rv_x + rv_w * cols[0] + p, rv_y + p, revision, 1.8)
        self._text(msp, rv_x + rv_w * cols[1] + p, rv_y + p, 'Initial Release Date Approved', 1.5)
        self._text(msp, rv_x + rv_w * cols[2] + p, rv_y + p, today, 1.5)
