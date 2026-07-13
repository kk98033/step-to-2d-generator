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
from extractors.fan_housing_extractor import FanHousingExtractor
from extractors.stamped_fan_base_extractor import StampedFanBaseExtractor
from extractors.generic_extractor import GenericExtractor


class DimensionEngine:
    """自動尺寸標註引擎 — 薄 Orchestrator"""

    def __init__(self, feature_data, layout, part_hint=None):
        self.feat = feature_data
        self.layout = layout
        self.part_hint = part_hint
        self.classifier = PartClassifier()
        self.layout_engine = LayoutEngine(layout)

    def extract_all_tasks(self, view_data, part_type=None):
        """
        預先提取所有視圖的標註任務，但不渲染。
        回傳結構: {'front': [tasks...], 'top': [tasks...], 'right': [tasks...]}
        """
        if not view_data:
            return {}

        if not part_type:
            part_type = self.classifier.classify(self.feat, self.part_hint)
        print(f"     DimensionEngine 零件分類: {part_type}")

        extractor = self._get_extractor(part_type)
        all_tasks = {}

        for view_name in ['front', 'back', 'top', 'right', 'left']:
            vd = view_data.get(view_name)
            if not vd:
                continue
            tasks = extractor.extract(self.feat, vd, view_name)
            all_tasks[view_name] = tasks

        return all_tasks

    def render_pre_extracted_tasks(self, msp, all_tasks, view_data):
        """
        渲染預先提取的任務到 DXF 模型空間
        """
        for view_name, tasks in all_tasks.items():
            vd = view_data.get(view_name)
            if not vd or not tasks:
                continue

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

    def annotate_all_views(self, msp, view_data=None, part_type=None):
        """
        為所有視圖自動標註尺寸 (舊版整合式，用於相容或不需預先計算排版的場景)
        """
        if not view_data:
            return

        all_tasks = self.extract_all_tasks(view_data, part_type)
        self.render_pre_extracted_tasks(msp, all_tasks, view_data)

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
            "FAN_HOUSING": FanHousingExtractor(),
            "STAMPED_FAN_BASE": StampedFanBaseExtractor(),
            "GENERIC": GenericExtractor(),
        }
        return extractors.get(part_type, GenericExtractor())
