"""
零件自動分類器 (Part Classifier)

根據 FeatureExtractor 提供的 3D 幾何特徵，自動判斷零件類型。
判斷結果用於選擇對應的特化提取器 (Extractor)。
"""


class PartClassifier:
    """零件類型分類器"""

    def classify(self, feature_data, part_hint=None):
        summary = feature_data.summary()

        # 先取得 Bounding Box 尺寸
        bbox = summary.get("bounding_box", {})
        w = bbox.get("W", 1)
        h = bbox.get("H", 1)
        d = bbox.get("D", 1)
        
        dims = sorted([w, h, d])

        if self._name_says_stamped_fan_base(part_hint):
            return "STAMPED_FAN_BASE"

        if self._name_says_fan_housing(part_hint) and self._is_name_shape_fan_housing_candidate(summary):
            return "FAN_HOUSING"

        if self._is_fan_housing(feature_data, summary):
            return "FAN_HOUSING"

        has_center_circle = self._has_center_circular_feature(feature_data, summary)

        # 規則 1: 圓盤/風扇類優先 (即使裡面有軸)
        # 條件: 最大和次大的尺寸相近 (長寬比接近1)，且厚度相對扁平
        if dims[2] > 0 and dims[1] / dims[2] > 0.8: 
            if dims[0] / dims[1] < 0.6 and has_center_circle: # 最短邊(厚度)小於其他兩邊
                return "FAN"

        # 規則 2: 軸類判斷
        step_count = summary.get("step_segments", 0)
        shaft_count = summary.get("shafts_count", 0)
        main_axis = summary.get("main_axis")

        if step_count >= 2 and shaft_count > 0 and main_axis is not None:
            return "SHAFT"
                
        # 規則 3: 如果 w, h 相近且不是明顯的細長軸，也當作風扇類嘗試
        if step_count < 2 and has_center_circle and max(w, h) > 0 and abs(w - h) / max(w, h) < 0.3:
            return "FAN"

        # 規則 3: 通用
        return "GENERIC"

    def _name_says_stamped_fan_base(self, part_hint):
        if not part_hint:
            return False
        text = str(part_hint).lower().replace("\\", " ").replace("/", " ")
        tokens = []
        for chunk in text.replace("-", " ").replace("_", " ").split():
            tokens.append(chunk)
        return any(t.startswith("dish") for t in tokens)

    def _name_says_fan_housing(self, part_hint):
        if not part_hint:
            return False
        text = str(part_hint).lower()
        keywords = (
            "housing",
            "house",
            "case",
            "casing",
            "shell",
            "shroud",
            "frame",
            "fan_housing",
            "fan housing",
            "風扇殼",
            "外殼",
            "殼",
            "扇框",
        )
        return any(k in text for k in keywords)

    def _is_name_shape_fan_housing_candidate(self, summary):
        bbox = summary.get("bounding_box", {})
        dims = sorted([
            bbox.get("W", 0),
            bbox.get("H", 0),
            bbox.get("D", 0),
        ])
        if dims[2] <= 0:
            return False

        is_flat = dims[0] / dims[2] < 0.65
        square_or_round_frame = dims[1] / dims[2] > 0.65
        not_tiny_fastener = dims[2] >= 15.0

        return is_flat and square_or_round_frame and not_tiny_fastener

    def _has_center_circular_feature(self, feature_data, summary):
        bbox = summary.get("bounding_box", {})
        axis_dims = {
            "X": bbox.get("W", 0),
            "Y": bbox.get("H", 0),
            "Z": bbox.get("D", 0),
        }
        ordered_axes = sorted(axis_dims, key=lambda a: axis_dims[a])
        if axis_dims[ordered_axes[-1]] <= 0:
            return False
        plane_axes = ordered_axes[1:]
        center = self._bbox_center(feature_data, plane_axes)
        span = axis_dims[ordered_axes[-1]]
        cylinders = list(getattr(feature_data, "cylinders_raw", []))
        return self._find_central_circular_features(cylinders, center, plane_axes, span)

    def _is_fan_housing(self, feature_data, summary):
        """
        風扇殼判斷:
        必須同時具備中心軸/同心圓結構與大型圓形風道，再搭配外框、
        四角安裝孔或環形重複特徵。只有方形外框加四孔不會成立。
        """
        bbox = summary.get("bounding_box", {})
        axis_dims = {
            "X": bbox.get("W", 0),
            "Y": bbox.get("H", 0),
            "Z": bbox.get("D", 0),
        }
        ordered_axes = sorted(axis_dims, key=lambda a: axis_dims[a])
        if axis_dims[ordered_axes[-1]] <= 0:
            return False

        thick_axis = ordered_axes[0]
        plane_axes = ordered_axes[1:]
        plane_sizes = [axis_dims[a] for a in plane_axes]
        short_plane, long_plane = min(plane_sizes), max(plane_sizes)
        if long_plane <= 0:
            return False

        is_flat = axis_dims[thick_axis] / long_plane < 0.55
        square_frame = short_plane / long_plane > 0.72
        if not is_flat:
            return False

        center = self._bbox_center(feature_data, plane_axes)
        span = long_plane
        cylinders = list(getattr(feature_data, "cylinders_raw", []))
        holes = list(getattr(feature_data, "holes", []))

        central = self._find_central_circular_features(cylinders, center, plane_axes, span)
        large_airway = self._find_large_airway(cylinders, center, plane_axes, span)
        mounting_holes = self._has_four_mounting_holes(holes, center, plane_axes, span)
        ring_repeat = self._has_ring_repetition(cylinders, center, plane_axes, span)

        if not (central and large_airway):
            return self._summary_has_fan_housing_signature(summary, span, is_flat, square_frame)

        return square_frame or mounting_holes or ring_repeat

    def _summary_has_fan_housing_signature(self, summary, span, is_flat, square_frame):
        """
        備援判斷: 有些 STEP 的圓柱中心或方向不穩，無法可靠判斷是否同心。
        這裡改看 summary 裡的直徑分布與特徵豐富度，避免真實風扇殼漏判。
        """
        if not (is_flat and square_frame and span > 0):
            return False

        holes = summary.get("holes", [])
        shafts = summary.get("shafts", [])
        hole_dias = [h.get("Ø", 0) for h in holes]
        shaft_dias = [s.get("Ø", 0) for s in shafts]
        all_dias = hole_dias + shaft_dias
        if not all_dias:
            return False

        has_large_airway = any(span * 0.32 <= d <= span * 1.20 for d in hole_dias)
        has_center_or_boss = any(d >= span * 0.12 for d in shaft_dias) or any(d >= span * 0.08 for d in hole_dias)
        rich_shell_detail = (
            summary.get("holes_count", 0) >= 8 and
            summary.get("shafts_count", 0) >= 4 and
            summary.get("step_segments", 0) >= 4
        )

        return has_large_airway and has_center_or_boss and rich_shell_detail

    def _bbox_center(self, feature_data, plane_axes):
        ranges = {
            "X": (getattr(feature_data, "xmin", 0), getattr(feature_data, "xmax", 0)),
            "Y": (getattr(feature_data, "ymin", 0), getattr(feature_data, "ymax", 0)),
            "Z": (getattr(feature_data, "zmin", 0), getattr(feature_data, "zmax", 0)),
        }
        return tuple((ranges[a][0] + ranges[a][1]) / 2.0 for a in plane_axes)

    def _project_center(self, feature, plane_axes):
        axis_index = {"X": 0, "Y": 1, "Z": 2}
        center = feature.get("center", (0, 0, 0))
        return tuple(center[axis_index[a]] for a in plane_axes)

    def _distance2d(self, p1, p2):
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

    def _find_central_circular_features(self, cylinders, center, plane_axes, span):
        near_tol = span * 0.12
        min_dia = span * 0.08
        for cyl in cylinders:
            dia = cyl.get("diameter", cyl.get("radius", 0) * 2)
            if dia < min_dia:
                continue
            if self._distance2d(self._project_center(cyl, plane_axes), center) <= near_tol:
                return True
        return False

    def _find_large_airway(self, cylinders, center, plane_axes, span):
        near_tol = span * 0.16
        for cyl in cylinders:
            dia = cyl.get("diameter", cyl.get("radius", 0) * 2)
            if span * 0.32 <= dia <= span * 0.95:
                if self._distance2d(self._project_center(cyl, plane_axes), center) <= near_tol:
                    return True
        return False

    def _has_four_mounting_holes(self, holes, center, plane_axes, span):
        candidates = []
        for hole in holes:
            dia = hole.get("diameter", hole.get("radius", 0) * 2)
            if dia <= 0 or dia > span * 0.22:
                continue
            p = self._project_center(hole, plane_axes)
            if self._distance2d(p, center) < span * 0.25:
                continue
            candidates.append(p)

        quadrants = set()
        for x, y in candidates:
            if abs(x - center[0]) < span * 0.08 or abs(y - center[1]) < span * 0.08:
                continue
            quadrants.add((1 if x > center[0] else -1, 1 if y > center[1] else -1))

        return len(quadrants) >= 4

    def _has_ring_repetition(self, cylinders, center, plane_axes, span):
        radii = []
        for cyl in cylinders:
            p = self._project_center(cyl, plane_axes)
            r = self._distance2d(p, center)
            dia = cyl.get("diameter", cyl.get("radius", 0) * 2)
            if span * 0.18 <= r <= span * 0.48 and dia <= span * 0.25:
                radii.append(r)

        for r in radii:
            group = [x for x in radii if abs(x - r) <= span * 0.04]
            if len(group) >= 3:
                return True
        return False
