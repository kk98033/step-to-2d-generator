"""
沖壓風扇底座 / 導流底板特化提取器。

核心策略:
  - 以中心旋轉軸作為基準 A。
  - 外圍定位邊、凸耳或缺口作為基準 B 的候選。
  - 中央孔系、葉片沖壓區與外圍定位結構集中標註。
  - 葉片曲線不逐點標註，只標數量、角度與分布範圍。
"""
import math

from extractors.base_extractor import BaseExtractor
from dimension_task import DimensionTask


class StampedFanBaseExtractor(BaseExtractor):
    """沖壓風扇底座 / 導流底板標註任務提取器"""

    def extract(self, feature_data, view_data, view_name):
        if not view_data or not view_data.get("visible"):
            return []

        edges = view_data.get("visible", [])
        circles = [e for e in edges if e.get("type") == "circle"]
        bbox = view_data["bbox"]
        w, h = view_data["size"]

        if view_name in ("front", "back") and min(w, h) / max(w, h) > 0.55:
            return self._extract_face_view(circles, edges, bbox, view_name)

        return self._extract_side_view(edges, bbox, view_name)

    def _extract_face_view(self, circles, edges, bbox, view_name):
        tasks = []
        x0, y0, x1, y1 = bbox
        w = x1 - x0
        h = y1 - y0
        span = max(w, h)
        center, central = self._center_stack(circles, bbox)
        if not center:
            center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
            central = []

        tasks.append(DimensionTask(
            dim_type="CENTERLINES",
            center=center,
            radius=span * 0.58,
            view_name=view_name,
        ))

        # 整體包絡與裝配範圍，最外層。
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

        for task in self._central_hole_tasks(central, span, view_name):
            tasks.append(task)

        pcd = self._hole_pattern(circles, center, span)
        if pcd:
            count, avg_dia, pcd_dia, start_angle, step_angle, center_pair = pcd
            tasks.append(DimensionTask(
                dim_type="LEADER",
                center=center,
                radius=pcd_dia / 2.0,
                text=f"{count}-孔 Φ{avg_dia:.2f} PCD Φ{pcd_dia:.2f}",
                angle=start_angle,
                view_name=view_name,
            ))
            if center_pair:
                axis, p1, p2 = center_pair
                if axis == "x":
                    value = abs(p2[0] - p1[0])
                    tasks.append(DimensionTask(
                        dim_type="LINEAR",
                        start_proj=(p1[0], 0),
                        end_proj=(p2[0], 0),
                        value=value,
                        text=f"孔中心距 {value:.2f}",
                        side="BOTTOM",
                        rank=1,
                        view_name=view_name,
                    ))
                else:
                    value = abs(p2[1] - p1[1])
                    tasks.append(DimensionTask(
                        dim_type="LINEAR",
                        start_proj=(0, p1[1]),
                        end_proj=(0, p2[1]),
                        value=value,
                        text=f"孔中心距 {value:.2f}",
                        side="RIGHT",
                        rank=1,
                        view_name=view_name,
                    ))
            tasks.append(DimensionTask(
                dim_type="ANGULAR",
                center=center,
                radius=pcd_dia / 2.0,
                value=step_angle,
                text=f"{step_angle:.0f}° 等分",
                angle=start_angle,
                view_name=view_name,
            ))

        blade = self._blade_pattern(edges, center, span)
        if blade:
            count, inner_r, outer_r, angle = blade
            tasks.append(DimensionTask(
                dim_type="LEADER",
                center=center,
                radius=outer_r,
                text=f"{count}-葉片/導流孔，範圍 Φ{inner_r * 2:.2f}~Φ{outer_r * 2:.2f}",
                angle=angle,
                view_name=view_name,
            ))
            if count > 0:
                tasks.append(DimensionTask(
                    dim_type="ANGULAR",
                    center=center,
                    radius=(inner_r + outer_r) / 2.0,
                    value=360.0 / count,
                    text=f"{360.0 / count:.0f}°",
                    angle=angle,
                    view_name=view_name,
                ))

        tasks.extend(self._process_notes(view_name))
        return tasks

    def _extract_side_view(self, edges, bbox, view_name):
        x0, y0, x1, y1 = bbox
        w = x1 - x0
        h = y1 - y0
        tasks = [
            DimensionTask(
                dim_type="LINEAR",
                start_proj=(x0, 0),
                end_proj=(x1, 0),
                value=w,
                side="BOTTOM",
                rank=3,
                view_name=view_name,
            ),
            DimensionTask(
                dim_type="LINEAR",
                start_proj=(0, y0),
                end_proj=(0, y1),
                value=h,
                side="RIGHT",
                rank=3,
                view_name=view_name,
            ),
        ]

        y_verts = self._find_contour_vertices(edges, axis="y", max_vertices=6)
        if len(y_verts) >= 3:
            base = y_verts[0]
            for val in y_verts[1:-1][:3]:
                tasks.append(DimensionTask(
                    dim_type="LINEAR",
                    start_proj=(0, base),
                    end_proj=(0, val),
                    value=abs(val - base),
                    side="RIGHT",
                    rank=1,
                    baseline="BOTTOM",
                    view_name=view_name,
                ))

        tasks.extend(self._process_notes(view_name, compact=True))
        return tasks

    def _central_hole_tasks(self, central, span, view_name):
        tasks = []
        if not central:
            return tasks

        central = self._dedupe_circles(central)
        central.sort(key=lambda c: c["radius"])
        small = [c for c in central if c["radius"] * 2 >= span * 0.04]
        large = [c for c in central if c["radius"] * 2 >= span * 0.20]

        if small:
            c = small[0]
            tasks.append(DimensionTask(
                dim_type="LEADER",
                center=c["center"],
                radius=c["radius"],
                text=f"基準A 中心孔 Φ{c['radius'] * 2:.2f}",
                angle=145,
                view_name=view_name,
            ))
        if large:
            c = large[-1]
            tasks.append(DimensionTask(
                dim_type="LEADER",
                center=c["center"],
                radius=c["radius"],
                text=f"沖壓/葉片範圍 Φ{c['radius'] * 2:.2f} 內",
                angle=35,
                view_name=view_name,
            ))
        return tasks

    def _hole_pattern(self, circles, center, span):
        candidates = []
        for c in circles:
            dia = c["radius"] * 2
            r = self._dist(c["center"], center)
            if span * 0.10 <= r <= span * 0.45 and dia <= span * 0.18:
                candidates.append((c, r, self._angle(c["center"], center)))
        if len(candidates) < 3:
            return None

        buckets = {}
        bucket_size = max(span * 0.04, 1.0)
        for c, r, a in candidates:
            key = round(r / bucket_size)
            buckets.setdefault(key, []).append((c, r, a))

        best = max(buckets.values(), key=len)
        if len(best) < 3:
            return None

        angles = sorted({round(a, 1) for _, _, a in best})
        count = len(angles)
        avg_r = sum(r for _, r, _ in best) / len(best)
        avg_dia = sum(c["radius"] * 2 for c, _, _ in best) / len(best)
        diffs = []
        for i, a in enumerate(angles):
            b = angles[(i + 1) % len(angles)]
            diff = b - a if b >= a else b + 360 - a
            diffs.append(diff)
        step_angle = sum(diffs) / len(diffs) if diffs else 0
        center_pair = self._pick_hole_center_pair([c for c, _, _ in best], span)
        return count, avg_dia, avg_r * 2, angles[0], step_angle, center_pair

    def _pick_hole_center_pair(self, circles, span):
        if len(circles) < 2:
            return None

        tol = max(span * 0.08, 1.0)
        best = None
        best_score = 0.0

        for i, c1 in enumerate(circles):
            p1 = c1["center"]
            for c2 in circles[i + 1:]:
                p2 = c2["center"]
                if abs(p1[1] - p2[1]) <= tol:
                    score = abs(p1[0] - p2[0])
                    if score > best_score:
                        best = ("x", p1, p2)
                        best_score = score

        if best:
            return best

        for i, c1 in enumerate(circles):
            p1 = c1["center"]
            for c2 in circles[i + 1:]:
                p2 = c2["center"]
                if abs(p1[0] - p2[0]) <= tol:
                    score = abs(p1[1] - p2[1])
                    if score > best_score:
                        best = ("y", p1, p2)
                        best_score = score

        return best

    def _blade_pattern(self, edges, center, span):
        pts = []
        for e in edges:
            if e.get("type") == "line":
                pts.extend([e["p1"], e["p2"]])
            elif e.get("points"):
                pts.extend([e["points"][0], e["points"][-1]])

        radii = []
        angles = []
        for p in pts:
            r = self._dist(p, center)
            if span * 0.18 <= r <= span * 0.52:
                radii.append(r)
                angles.append(self._angle(p, center))
        if len(angles) < 6:
            return None

        unique_angles = []
        for a in sorted(angles):
            if not unique_angles or abs(a - unique_angles[-1]) > 6:
                unique_angles.append(a)
        count = max(3, min(24, len(unique_angles) // 2 or len(unique_angles)))
        return count, min(radii), max(radii), unique_angles[0]

    def _process_notes(self, view_name, compact=False):
        if view_name not in ("front", "back", "right"):
            return []
        notes = [
            "基準A: 中央旋轉軸",
            "基準B: 外圍定位邊/凸耳/缺口",
            "沖壓方向依成形面設定",
            "內部破孔不可有毛邊",
        ]
        if not compact:
            notes.extend([
                "毛邊方向、刮料/剃邊深度依公司沖壓標準",
                "葉片曲線由CAD輪廓/模具控制",
            ])
        return [
            DimensionTask(dim_type="NOTE", text=note, view_name=view_name)
            for note in notes
        ]

    def _center_stack(self, circles, bbox):
        x0, y0, x1, y1 = bbox
        center_guess = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        span = max(x1 - x0, y1 - y0)
        near = [c for c in circles if self._dist(c["center"], center_guess) <= span * 0.16]
        if not near:
            return None, []
        largest = max(near, key=lambda c: c["radius"])
        center = largest["center"]
        central = [c for c in circles if self._dist(c["center"], center) <= span * 0.05]
        return center, central

    def _dedupe_circles(self, circles):
        result = []
        for c in circles:
            if not any(abs(c["radius"] - old["radius"]) < 0.5 for old in result):
                result.append(c)
        return result

    def _angle(self, point, center):
        a = math.degrees(math.atan2(point[1] - center[1], point[0] - center[0]))
        return a + 360 if a < 0 else a

    def _dist(self, p1, p2):
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5
