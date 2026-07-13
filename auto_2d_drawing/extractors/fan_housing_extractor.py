"""
風扇殼特化提取器 (Fan Housing Extractor)

標註原則:
  - 前視圖以中心軸、風道、安裝孔和外框為基準。
  - 重複孔/葉片只標一組尺寸與數量，不逐一標註。
  - 側視圖只保留總厚度、主要凸台/凹槽階梯，避免標滿細碎輪廓。
"""
import math

from extractors.base_extractor import BaseExtractor
from dimension_task import DimensionTask


class FanHousingExtractor(BaseExtractor):
    """風扇殼標註任務提取器"""

    def extract(self, feature_data, view_data, view_name):
        if not view_data or not view_data.get("visible"):
            return []

        all_edges = view_data.get("visible", [])
        circles = [e for e in all_edges if e.get("type") == "circle"]
        bbox = view_data["bbox"]
        w_real, h_real = view_data["size"]

        if view_name == "front" and self._looks_like_front_face(circles, bbox, w_real, h_real):
            return self._extract_front_face(circles, all_edges, bbox, view_name)

        return self._extract_side_profile(all_edges, bbox, view_name)

    def _looks_like_front_face(self, circles, bbox, w_real, h_real):
        if not circles:
            return False
        if min(w_real, h_real) / max(w_real, h_real) < 0.65:
            return False

        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        span = max(w_real, h_real)
        for c in circles:
            dia = c["radius"] * 2
            dist = self._dist(c["center"], (cx, cy))
            if span * 0.28 <= dia <= span * 0.95 and dist <= span * 0.16:
                return True
        return False

    def _extract_front_face(self, circles, all_edges, bbox, view_name):
        tasks = []
        x0, y0, x1, y1 = bbox
        w = x1 - x0
        h = y1 - y0
        span = max(w, h)

        center, central_circles = self._find_center_stack(circles, bbox)
        if center:
            tasks.append(DimensionTask(
                dim_type="CENTERLINES",
                center=center,
                radius=span * 0.58,
                view_name=view_name,
            ))

        # 外框總寬/總高，放最外層。
        tasks.append(DimensionTask(
            dim_type="LINEAR",
            start_proj=(x0, 0),
            end_proj=(x1, 0),
            value=w,
            side="BOTTOM",
            rank=3,
            view_name=view_name,
        ))
        tasks.append(DimensionTask(
            dim_type="LINEAR",
            start_proj=(0, y0),
            end_proj=(0, y1),
            value=h,
            side="RIGHT",
            rank=3,
            view_name=view_name,
        ))

        if center and central_circles:
            airway = self._pick_airway_circle(central_circles, span)
            if airway:
                tasks.append(DimensionTask(
                    dim_type="LEADER",
                    center=airway["center"],
                    radius=airway["radius"],
                    text=f"風道內徑 Φ{airway['radius'] * 2:.2f}",
                    angle=35,
                    view_name=view_name,
                ))

            for label, circle, angle in self._pick_center_circles(central_circles, airway):
                tasks.append(DimensionTask(
                    dim_type="LEADER",
                    center=circle["center"],
                    radius=circle["radius"],
                    text=f"{label} Φ{circle['radius'] * 2:.2f}",
                    angle=angle,
                    view_name=view_name,
                ))

        if center:
            mount_holes = self._find_mounting_holes(circles, center, span)
            if len(mount_holes) >= 4:
                tasks.extend(self._build_mounting_hole_tasks(mount_holes, center, view_name))

            repeated = self._detect_ring_repetition(all_edges, center, span)
            if repeated:
                count, radius, angle = repeated
                tasks.append(DimensionTask(
                    dim_type="LEADER",
                    center=center,
                    radius=radius,
                    text=f"{count}-環形導流/葉片",
                    angle=angle,
                    view_name=view_name,
                ))

        return tasks

    def _extract_side_profile(self, all_edges, bbox, view_name):
        tasks = []
        x0, y0, x1, y1 = bbox
        w = x1 - x0
        h = y1 - y0

        tasks.append(DimensionTask(
            dim_type="LINEAR",
            start_proj=(x0, 0),
            end_proj=(x1, 0),
            value=w,
            side="BOTTOM",
            rank=3,
            view_name=view_name,
        ))
        tasks.append(DimensionTask(
            dim_type="LINEAR",
            start_proj=(0, y0),
            end_proj=(0, y1),
            value=h,
            side="RIGHT",
            rank=3,
            view_name=view_name,
        ))

        # 側視只保留少量主要階梯；太密的輪廓會被聚類合併。
        x_verts = self._significant_vertices(all_edges, axis="x", limit=5)
        y_verts = self._significant_vertices(all_edges, axis="y", limit=5)

        if len(x_verts) >= 3 and w <= h * 1.4:
            tasks.extend(self._build_selected_step_tasks(x_verts, axis="x", side="BOTTOM", view_name=view_name))
        if len(y_verts) >= 3:
            tasks.extend(self._build_selected_step_tasks(y_verts, axis="y", side="RIGHT", view_name=view_name))

        return tasks

    def _find_center_stack(self, circles, bbox):
        x0, y0, x1, y1 = bbox
        center_guess = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        span = max(x1 - x0, y1 - y0)
        near = [c for c in circles if self._dist(c["center"], center_guess) <= span * 0.18]
        if not near:
            return None, []

        largest = max(near, key=lambda c: c["radius"])
        center = largest["center"]
        central = [c for c in circles if self._dist(c["center"], center) <= span * 0.04]
        central.sort(key=lambda c: c["radius"], reverse=True)
        return center, self._dedupe_circles(central)

    def _pick_airway_circle(self, central_circles, span):
        candidates = [
            c for c in central_circles
            if span * 0.28 <= c["radius"] * 2 <= span * 0.95
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c["radius"])

    def _pick_center_circles(self, central_circles, airway):
        circles = [c for c in central_circles if c is not airway]
        circles.sort(key=lambda c: c["radius"])
        picked = []
        if circles:
            picked.append(("中心孔", circles[0], 145))
        if len(circles) >= 2:
            picked.append(("軸套/馬達座", circles[-1], 55))
        return picked

    def _find_mounting_holes(self, circles, center, span):
        candidates = []
        for c in circles:
            dia = c["radius"] * 2
            dist = self._dist(c["center"], center)
            if dia <= span * 0.22 and span * 0.28 <= dist <= span * 0.72:
                candidates.append(c)

        by_quadrant = {}
        for c in candidates:
            dx = c["center"][0] - center[0]
            dy = c["center"][1] - center[1]
            if abs(dx) < span * 0.08 or abs(dy) < span * 0.08:
                continue
            quadrant = (1 if dx > 0 else -1, 1 if dy > 0 else -1)
            old = by_quadrant.get(quadrant)
            if not old or self._dist(c["center"], center) > self._dist(old["center"], center):
                by_quadrant[quadrant] = c

        return list(by_quadrant.values())

    def _build_mounting_hole_tasks(self, holes, center, view_name):
        tasks = []
        holes = sorted(holes, key=lambda c: (c["center"][0], c["center"][1]))
        avg_dia = sum(c["radius"] * 2 for c in holes) / len(holes)
        first = max(holes, key=lambda c: self._dist(c["center"], center))
        angle = math.degrees(math.atan2(first["center"][1] - center[1], first["center"][0] - center[0]))

        tasks.append(DimensionTask(
            dim_type="LEADER",
            center=first["center"],
            radius=first["radius"],
            text=f"{len(holes)}-安裝孔 Φ{avg_dia:.2f}",
            angle=angle,
            view_name=view_name,
        ))

        xs = [c["center"][0] for c in holes]
        ys = [c["center"][1] for c in holes]
        left_x = sum(x for x in xs if x < center[0]) / max(1, sum(1 for x in xs if x < center[0]))
        right_x = sum(x for x in xs if x > center[0]) / max(1, sum(1 for x in xs if x > center[0]))
        low_y = sum(y for y in ys if y < center[1]) / max(1, sum(1 for y in ys if y < center[1]))
        high_y = sum(y for y in ys if y > center[1]) / max(1, sum(1 for y in ys if y > center[1]))

        if right_x > left_x:
            tasks.append(DimensionTask(
                dim_type="LINEAR",
                start_proj=(left_x, 0),
                end_proj=(right_x, 0),
                value=right_x - left_x,
                prefix="孔距 ",
                side="BOTTOM",
                rank=2,
                view_name=view_name,
            ))
        if high_y > low_y:
            tasks.append(DimensionTask(
                dim_type="LINEAR",
                start_proj=(0, low_y),
                end_proj=(0, high_y),
                value=high_y - low_y,
                prefix="孔距 ",
                side="RIGHT",
                rank=2,
                view_name=view_name,
            ))

        return tasks

    def _detect_ring_repetition(self, all_edges, center, span):
        points = []
        for e in all_edges:
            if e.get("type") == "line":
                points.extend([e["p1"], e["p2"]])
            elif e.get("points"):
                points.extend([e["points"][0], e["points"][-1]])

        buckets = {}
        for p in points:
            r = self._dist(p, center)
            if not (span * 0.18 <= r <= span * 0.50):
                continue
            angle = math.degrees(math.atan2(p[1] - center[1], p[0] - center[0]))
            if angle < 0:
                angle += 360
            key = round(r / max(span * 0.04, 1.0))
            buckets.setdefault(key, []).append((r, angle))

        for items in buckets.values():
            angles = []
            for _, angle in sorted(items, key=lambda x: x[1]):
                if not angles or abs(angle - angles[-1]) > 4:
                    angles.append(angle)
            if 3 <= len(angles) <= 20:
                avg_r = sum(r for r, _ in items) / len(items)
                return len(angles), avg_r, angles[0]
        return None

    def _significant_vertices(self, all_edges, axis="x", limit=5):
        verts = self._find_contour_vertices(all_edges, axis=axis, max_vertices=12)
        if len(verts) <= limit:
            return verts

        selected = [verts[0], verts[-1]]
        gaps = []
        for i in range(len(verts) - 1):
            gaps.append((abs(verts[i + 1] - verts[i]), verts[i], verts[i + 1]))
        gaps.sort(reverse=True)
        for _, a, b in gaps:
            selected.extend([a, b])
            selected = sorted(set(round(v, 4) for v in selected))
            if len(selected) >= limit:
                break
        if len(selected) > limit:
            interior = selected[1:-1][:max(0, limit - 2)]
            selected = [selected[0], *interior, selected[-1]]
        return sorted(selected)

    def _build_selected_step_tasks(self, verts, axis="x", side="BOTTOM", view_name="front"):
        tasks = []
        base = verts[0]
        for val in verts[1:-1]:
            dist = abs(val - base)
            if dist < 1.0:
                continue
            tasks.append(DimensionTask(
                dim_type="LINEAR",
                start_proj=(base, 0) if axis == "x" else (0, base),
                end_proj=(val, 0) if axis == "x" else (0, val),
                value=dist,
                side=side,
                rank=1,
                baseline="LEFT" if axis == "x" else "BOTTOM",
                view_name=view_name,
            ))
        return tasks

    def _dedupe_circles(self, circles):
        result = []
        for c in circles:
            if not any(abs(c["radius"] - old["radius"]) < 0.5 for old in result):
                result.append(c)
        return result

    def _dist(self, p1, p2):
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5
