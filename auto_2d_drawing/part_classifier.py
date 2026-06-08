"""
零件自動分類器 (Part Classifier)

根據 FeatureExtractor 提供的 3D 幾何特徵，自動判斷零件類型。
判斷結果用於選擇對應的特化提取器 (Extractor)。
"""


class PartClassifier:
    """零件類型分類器"""

    def classify(self, feature_data):
        summary = feature_data.summary()

        # 先取得 Bounding Box 尺寸
        bbox = summary.get("bounding_box", {})
        w = bbox.get("W", 1)
        h = bbox.get("H", 1)
        d = bbox.get("D", 1)
        
        dims = sorted([w, h, d])

        # 規則 1: 圓盤/風扇類優先 (即使裡面有軸)
        # 條件: 最大和次大的尺寸相近 (長寬比接近1)，且厚度相對扁平
        if dims[2] > 0 and dims[1] / dims[2] > 0.8: 
            if dims[0] / dims[1] < 0.6: # 最短邊(厚度)小於其他兩邊
                return "FAN"

        # 規則 2: 軸類判斷
        step_count = summary.get("step_segments", 0)
        shaft_count = summary.get("shafts_count", 0)
        main_axis = summary.get("main_axis")

        if step_count >= 2 and shaft_count > 0 and main_axis is not None:
            return "SHAFT"
                
        # 規則 3: 如果 w, h 相近且不是明顯的細長軸，也當作風扇類嘗試
        if step_count < 2 and max(w, h) > 0 and abs(w - h) / max(w, h) < 0.3:
            return "FAN"

        # 規則 3: 通用
        return "GENERIC"
