"""
自動尺寸標註引擎 — 混合式架構 Orchestrator

這個檔案是系統的「指揮官」，它本身不做任何標註計算或繪圖。
它的職責是:
1. 透過 PartClassifier 判斷零件類型
2. 選擇對應的特化提取器 (Extractor) 產出 DimensionTask 列表
3. 將任務列表交給通用排版引擎 (LayoutEngine) 渲染

新增零件類型時，只需要:
1. 在 extractors/ 目錄下新增一個 XxxExtractor
2. 在 part_classifier.py 加入新的判斷規則
3. 在下方 _get_extractor() 中註冊
"""
from part_classifier import PartClassifier
from layout_engine import LayoutEngine
from extractors.shaft_extractor import ShaftExtractor
from extractors.fan_extractor import FanExtractor
from extractors.generic_extractor import GenericExtractor


class DimensionEngine:
    """自動尺寸標註引擎 — 薄 Orchestrator"""

    def __init__(self, feature_data, layout):
        self.feat = feature_data
        self.layout = layout
        self.classifier = PartClassifier()
        self.layout_engine = LayoutEngine(layout)

    def annotate_all_views(self, msp, view_data=None, part_type=None):
        """
        為所有視圖自動標註尺寸。

        Args:
            msp: DXF modelspace
            view_data: 三視圖的投影資料 dict
            part_type: 指定的零件分類 (若未提供則自動重新分類)
        """
        if not view_data:
            return

        # 1. 判斷零件類型
        if not part_type:
            part_type = self.classifier.classify(self.feat)
        print(f"     DimensionEngine 零件分類: {part_type}")

        # 2. 選擇對應的提取器
        extractor = self._get_extractor(part_type)

        # 3. 為每個視圖提取標註任務並渲染
        for view_name in ['front', 'top', 'right']:
            vd = view_data.get(view_name)
            if not vd:
                continue

            self.annotate_view(msp, view_name, vd, extractor)

    def annotate_view(self, msp, view_name, vd, extractor, override_offset=None):
        """
        為單一視圖標註尺寸。
        
        Args:
            msp: DXF modelspace
            view_name: 視圖名稱
            vd: 投影資料
            extractor: 該零件類型的提取器
            override_offset: (ox, oy) 覆蓋排版座標 (用於獨立視圖輸出)
        """
        # 特化層: 決定「標什麼」
        tasks = extractor.extract(self.feat, vd, view_name)

        if not tasks:
            return

        # 通用層: 決定「標在哪」並渲染
        if override_offset:
            ox, oy = override_offset
        else:
            ox, oy = self.layout.get_view_offset(view_name)
            
        sw, sh = self.layout.get_scaled_size(view_name)
        self.layout_engine.render(msp, tasks, ox, oy, sw, sh, vd)

        # 前視圖額外: 規格字串
        if view_name == 'front':
            spec = self.feat.get_overall_spec()
            if spec:
                self.layout_engine._add_text(
                    msp, ox, oy + sh + 6,
                    f"規格: {spec}", height=2.0, layer='TEXT'
                )

    def _get_extractor(self, part_type):
        """根據零件類型回傳對應的提取器"""
        extractors = {
            "SHAFT": ShaftExtractor(),
            "FAN": FanExtractor(),
            "GENERIC": GenericExtractor(),
        }
        return extractors.get(part_type, GenericExtractor())
