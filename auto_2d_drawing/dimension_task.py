"""
標註任務物件 (DimensionTask) — 特化層與通用層之間的唯一溝通介面

不論輸入的是軸、風扇還是齒輪，特化層最終只能輸出一個由 DimensionTask 組成的列表。
通用排版引擎只消費 DimensionTask 列表，完全不需要知道零件類型。
"""


class DimensionTask:
    """
    一個標註任務，描述「要標什麼」而不管「標在哪」。

    Attributes:
        dim_type:   標註類型 — "LINEAR", "DIAMETER", "RADIAL", "ANGLE"
        start_proj: 投影座標系的起始點 (x, y)
        end_proj:   投影座標系的終止點 (x, y)
        value:      實際尺寸數值 (mm)
        text:       顯示文字 (如 "2.59")，若為空字串則由 layout engine 自動產生
        prefix:     前綴 (如 "Φ", "(A)Φ", "7x")
        tolerance:  公差 (如 "±0.05")
        side:       期望擺放方向 — "BOTTOM", "TOP", "LEFT", "RIGHT"
        rank:       層級 — 1=內層局部, 2=外層總長, 3=參考尺寸(加括號)
        baseline:   基準端 — "LEFT", "RIGHT", "NONE"
                    用於基線標註法: "LEFT" 表示從最左端量測, "RIGHT" 表示從最右端量測
        view_name:  所屬視圖 — "front", "top", "right"
    """

    def __init__(self, dim_type="LINEAR", start_proj=(0, 0), end_proj=(0, 0),
                 value=0.0, text="", prefix="", tolerance="",
                 side="BOTTOM", rank=1, baseline="NONE", view_name="front",
                 center=None, angle=0.0, radius=0.0, p1=None, p2=None):
        self.dim_type = dim_type
        self.start_proj = start_proj
        self.end_proj = end_proj
        self.value = value
        self.text = text if text else f"{value:.2f}"
        self.prefix = prefix
        self.tolerance = tolerance
        self.side = side
        self.rank = rank
        self.baseline = baseline
        self.view_name = view_name
        
        # 極座標與圓形標註擴充
        self.center = center
        self.angle = angle
        self.radius = radius
        self.p1 = p1
        self.p2 = p2

    @property
    def display_text(self):
        """組合完整的顯示文字: prefix + text + tolerance"""
        parts = []
        if self.prefix:
            parts.append(self.prefix)
        parts.append(self.text)
        return "".join(parts)

    @property
    def is_horizontal(self):
        """判斷此標註是否為水平方向"""
        return self.side in ("BOTTOM", "TOP")

    def __repr__(self):
        return (f"DimensionTask({self.dim_type}, {self.display_text}, "
                f"side={self.side}, rank={self.rank}, baseline={self.baseline})")
