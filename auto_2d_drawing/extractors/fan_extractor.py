"""
風扇類零件特化提取器 (Fan Extractor) — 骨架版

負責產出風扇類零件 (葉片、扇框、輪轂等) 的標註任務。
目前為初始骨架，後續逐步完善。

邏輯:
  - 前視圖: 整體寬度 + 整體高度
  - 俯視圖 (圓形端面): 外徑、中心孔徑
"""
from extractors.base_extractor import BaseExtractor
from dimension_task import DimensionTask


class FanExtractor(BaseExtractor):
    """風扇類零件標註任務提取器 (初版骨架)"""

    def extract(self, feature_data, view_data, view_name):
        vd = view_data
        if not vd or not vd.get('visible'):
            return []

        vis_edges = vd['visible']
        bbox = vd['bbox']
        w_real, h_real = vd['size']

        # 動態判斷：如果這個視圖有投影出大圓形，代表這是「正面/端面」，套用極座標邏輯
        # 否則套用側面的線性邊界邏輯
        circles = [e for e in vis_edges if e['type'] == 'circle']
        
        is_polar_face = False
        if circles:
            # 只要投影面的長寬比接近 1 (接近正方形/圓形包絡)，就認定是端面
            if max(w_real, h_real) > 0 and min(w_real, h_real) / max(w_real, h_real) > 0.8:
                is_polar_face = True
                
        if is_polar_face:
            return self._extract_polar_face(vis_edges, circles, bbox, view_name)
        else:
            hid_edges = vd.get('hidden', [])
            return self._extract_side_profile(vis_edges, hid_edges, view_name)

    def _extract_polar_face(self, vis_edges, circles, bbox, view_name):
        """圓形端面視圖: 中心十字線、同心圓直徑、環形陣列偵測"""
        tasks = []
        
        # 找最大的圓作為主體參考
        largest_circle = max(circles, key=lambda c: c['radius'])
        cx, cy = largest_circle['center']
        max_r = largest_circle['radius']
        
        # 取得零件整體的特徵對角半徑 (用來畫貫穿整個視圖的中心線)
        overall_radius = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) / 2.0
        centerline_r = overall_radius * 1.15 # 超出邊界 15%

        # 產生貫穿全局的十字中心線
        tasks.append(DimensionTask(
            dim_type="CENTERLINES",
            center=(cx, cy),
            radius=centerline_r,
            view_name=view_name
        ))

        # 全域工藝註解已移除，避免寫死在代碼中

        # 收集同心圓與非同心圓
        concentric = []
        off_center_circles = []
        tol = 0.5
        for c in circles:
            dc = ((c['center'][0] - cx)**2 + (c['center'][1] - cy)**2)**0.5
            if dc < tol:
                concentric.append(c)
            else:
                off_center_circles.append(c)

        # 排序並過濾太接近的同心圓
        concentric.sort(key=lambda c: c['radius'], reverse=True)
        filtered_concentric = []
        for c in concentric:
            if not filtered_concentric or abs(c['radius'] - filtered_concentric[-1]['radius']) > 1.0:
                filtered_concentric.append(c)

        # 標註所有關鍵同心圓 -> 全部使用 LEADER 引線標註
        angles = [30, 150, 45, 135, 60, 120, 15, 165] # 拉伸角度配置，盡量錯開
        for i, c in enumerate(filtered_concentric):
            r = c['radius']
            ang = angles[i % len(angles)]
            
            prefix = "內圈 Φ"
            if i == 0:
                prefix = "最大外徑 Φ"
            elif i == len(filtered_concentric) - 1:
                prefix = "中心孔 Φ"
                
            tasks.append(DimensionTask(
                dim_type="LEADER",
                center=(cx, cy),
                radius=r,
                value=r * 2,
                prefix=prefix,
                angle=ang,
                view_name=view_name
            ))

        # 3. 幾何頂點的極座標陣列偵測 (不僅限於圓心，包含葉片、溝槽的邊角)
        import math
        vertices = []
        for e in vis_edges:
            # 取線段、曲線的端點
            pts = []
            if e['type'] == 'line':
                pts.append(e['p1'])
                pts.append(e['p2'])
            elif 'points' in e and len(e['points']) >= 2:
                pts.append(e['points'][0])
                pts.append(e['points'][-1])
                
            for p in pts:
                dx = p[0] - cx
                dy = p[1] - cy
                r_dist = (dx**2 + dy**2)**0.5
                if r_dist > 2.0: # 忽略太靠近中心的雜訊
                    ang_deg = math.degrees(math.atan2(dy, dx))
                    if ang_deg < 0: ang_deg += 360
                    vertices.append((r_dist, ang_deg, dx, dy))
                    
        # 依照距離圓心的半徑分群
        features_by_r = {}
        for r_dist, ang_deg, dx, dy in vertices:
            matched = False
            for key_r in features_by_r.keys():
                if abs(key_r - r_dist) < 1.0: # 半徑容差 1mm
                    # 避免同一個特徵點重複加入 (角度容差 1 度)
                    if not any(abs(existing_ang - ang_deg) < 1.0 for _, existing_ang, _, _ in features_by_r[key_r]):
                        features_by_r[key_r].append((r_dist, ang_deg, dx, dy))
                    matched = True
                    break
            if not matched:
                features_by_r[r_dist] = [(r_dist, ang_deg, dx, dy)]
                
        # 尋找數量大於等於 3 的陣列特徵
        for group_r, items in features_by_r.items():
            if len(items) >= 3:
                # 按照角度排序
                items.sort(key=lambda x: x[1])
                diffs = []
                for i in range(len(items)):
                    a1 = items[i][1]
                    a2 = items[(i+1)%len(items)][1]
                    diff = a2 - a1
                    if diff < 0: diff += 360
                    diffs.append(diff)
                    
                avg_diff = sum(diffs) / len(diffs)
                # 判斷是否為均勻陣列
                if all(abs(d - avg_diff) < 2.0 for d in diffs):
                    N = len(items)
                    
                    # 標註其中兩個相鄰特徵的夾角
                    tasks.append(DimensionTask(
                        dim_type="ANGULAR",
                        center=(cx, cy),
                        radius=group_r,
                        value=avg_diff,
                        text=f"{N}-{avg_diff:.1f}°",
                        angle=items[0][1], # 起始角度
                        view_name=view_name
                    ))
                    
                    # 觸發工藝規則引擎 (Heuristics)
                    if N == 12: # 假設 12 個陣列特徵代表 12 個補±槽
                        tasks.append(DimensionTask(
                            dim_type="LEADER",
                            center=(cx, cy),
                            radius=group_r,
                            text=f"12*補±槽",
                            angle=items[1][1], # 拉在第二個特徵上
                            view_name=view_name
                        ))
                    elif N == 5: # 假設 5 個特徵是葉片
                        tasks.append(DimensionTask(
                            dim_type="LEADER",
                            center=(cx, cy),
                            radius=group_r,
                            text=f"5-葉片",
                            angle=items[1][1],
                            view_name=view_name
                        ))

        return tasks

    def _extract_side_profile(self, vis_edges, hid_edges, view_name):
        """側面視圖: 結合可見邊與隱藏邊，使用基線分層標註以標示所有階梯深度的頂點，避免擁擠重疊"""
        tasks = []
        all_edges = vis_edges + hid_edges

        # 風扇右視圖通常包含許多細微的階梯深度，允許提取更多頂點 (max_vertices=20)
        h_verts = self._find_contour_vertices(all_edges, axis='x', max_vertices=20)
        if len(h_verts) >= 2:
            tasks.extend(self._build_baseline_tasks(h_verts, axis='x', side="BOTTOM", view_name=view_name))

        v_verts = self._find_contour_vertices(all_edges, axis='y', max_vertices=20)
        if len(v_verts) >= 2:
            tasks.extend(self._build_baseline_tasks(v_verts, axis='y', side="RIGHT", view_name=view_name))

        return tasks

    def _build_baseline_tasks(self, verts, axis='x', side="BOTTOM", view_name="front"):
        """單向基線標註法: 全部以最小頂點為基準層層堆疊"""
        tasks = []
        n = len(verts)
        if n < 2:
            return tasks

        base_val = verts[0]
        # x軸基準在左邊，y軸基準在底部
        baseline_name = "LEFT" if axis == 'x' else "BOTTOM"

        # 逐層拉出 (rank=1, baseline=baseline_name)
        for i in range(1, n):
            val = verts[i]
            dist = abs(val - base_val)
            start = (base_val, 0) if axis == 'x' else (0, base_val)
            end = (val, 0) if axis == 'x' else (0, val)

            # 若為最後一個特徵，可視為 overall (rank=2)
            rank = 2 if i == n - 1 else 1
            b_name = "NONE" if rank == 2 else baseline_name

            tasks.append(DimensionTask(
                dim_type="LINEAR",
                start_proj=start,
                end_proj=end,
                value=dist,
                side=side,
                rank=rank,
                baseline=b_name,
                view_name=view_name,
            ))

        return tasks
