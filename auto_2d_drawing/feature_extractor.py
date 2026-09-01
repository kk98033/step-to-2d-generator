"""
3D 幾何特徵提取模組 v3 — 全特徵深度解析版
包含: 圓柱孔、軸與凸台、圓錐/倒角/沉頭、圓弧/圓角、平面與壁厚、階梯段落、環形槽與孔群陣列 (PCD)
"""
import math
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_REVERSED
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.GeomAbs import (
    GeomAbs_Cylinder, GeomAbs_Circle, GeomAbs_Plane,
    GeomAbs_Cone, GeomAbs_Torus, GeomAbs_Sphere, GeomAbs_Line
)
from OCC.Core.gp import gp_Vec
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop


class FeatureExtractor:
    """從 TopoDS_Shape 中提取完整幾何特徵，用於自動標註與特徵圖層解析"""

    def __init__(self, shape):
        self.shape = shape
        # Bounding Box
        bbox = Bnd_Box()
        brepbndlib.Add(shape, bbox)
        self.xmin, self.ymin, self.zmin, self.xmax, self.ymax, self.zmax = bbox.Get()
        self.W = self.xmax - self.xmin   # X 方向
        self.H = self.ymax - self.ymin   # Y 方向
        self.D = self.zmax - self.zmin   # Z 方向

        # 特徵容器 (無任何數量上限)
        self.holes = []
        self.shafts = []
        self.cones = []             # 圓錐面/倒角/沉頭
        self.planes = []            # 平面/基準面
        self.thicknesses = []       # 壁厚/板厚
        self.toruses = []           # 環形槽/O-ring槽
        self.spheres = []           # 球面
        self.fillets = []           # 圓弧邊/圓角
        self.circle_edges = []      # 3D 圓形邊界
        self.cylinders_raw = []     # 所有圓柱面 (未分類)
        self.step_segments = []     # 沿主軸的段差結構
        self.hole_patterns = []     # 孔群陣列 (PCD)

        # 完整提取流程
        self._extract_faces()
        self._extract_edges()
        self._detect_step_segments()
        self._detect_hole_patterns()
        self._detect_thicknesses()

    def _extract_faces(self):
        """遍歷所有 FACE，提取圓柱面、圓錐面、平面、圓環面、球面"""
        seen_cyl = set()
        seen_cone = set()
        seen_plane = set()
        seen_torus = set()
        seen_sphere = set()

        exp = TopExp_Explorer(self.shape, TopAbs_FACE)
        while exp.More():
            face = exp.Current()
            surf = BRepAdaptor_Surface(face)
            stype = surf.GetType()
            is_reversed = (face.Orientation() == TopAbs_REVERSED)

            # 1. 圓柱面 (Cylinder: 孔 / 軸 / 凸台)
            if stype == GeomAbs_Cylinder:
                cyl = surf.Cylinder()
                r = cyl.Radius()
                loc = cyl.Location()
                d = cyl.Axis().Direction()
                v0, v1 = surf.FirstVParameter(), surf.LastVParameter()

                vec = gp_Vec(d).Multiplied((v0 + v1) / 2)
                cp = loc.Translated(vec)
                length = abs(v1 - v0)
                start_pos = min(v0, v1)
                end_pos = max(v0, v1)

                key = (round(cp.X(), 2), round(cp.Y(), 2), round(cp.Z(), 2), round(r, 3), round(length, 2))
                if key not in seen_cyl:
                    seen_cyl.add(key)
                    info = {
                        "radius": round(r, 4),
                        "diameter": round(r * 2, 4),
                        "center": (round(cp.X(), 3), round(cp.Y(), 3), round(cp.Z(), 3)),
                        "axis_dir": (round(d.X(), 3), round(d.Y(), 3), round(d.Z(), 3)),
                        "length": round(length, 3),
                        "start_pos": round(start_pos, 3),
                        "end_pos": round(end_pos, 3),
                        "is_hole": is_reversed,
                    }
                    self.cylinders_raw.append(info)
                    if is_reversed:
                        self.holes.append(info)
                    else:
                        self.shafts.append(info)

            # 2. 圓錐面 (Cone: 倒角 / 沉頭孔 Countersink / 錐度)
            elif stype == GeomAbs_Cone:
                cone = surf.Cone()
                r_ref = cone.RefRadius()
                semi_angle = cone.SemiAngle()
                semi_deg = abs(math.degrees(semi_angle))
                loc = cone.Location()
                d = cone.Axis().Direction()
                apex = cone.Apex()
                v0, v1 = surf.FirstVParameter(), surf.LastVParameter()
                r0 = abs(r_ref + v0 * math.sin(semi_angle))
                r1 = abs(r_ref + v1 * math.sin(semi_angle))
                min_r, max_r = min(r0, r1), max(r0, r1)
                height = abs(v1 - v0) * math.cos(semi_angle)

                key = (round(loc.X(), 2), round(loc.Y(), 2), round(loc.Z(), 2), round(min_r, 2), round(max_r, 2))
                if key not in seen_cone:
                    seen_cone.add(key)
                    self.cones.append({
                        "min_radius": round(min_r, 4),
                        "max_radius": round(max_r, 4),
                        "min_diameter": round(min_r * 2, 4),
                        "max_diameter": round(max_r * 2, 4),
                        "semi_angle_deg": round(semi_deg, 2),
                        "included_angle_deg": round(semi_deg * 2, 2),
                        "height": round(height, 3),
                        "center": (round(loc.X(), 3), round(loc.Y(), 3), round(loc.Z(), 3)),
                        "apex": (round(apex.X(), 3), round(apex.Y(), 3), round(apex.Z(), 3)),
                        "axis_dir": (round(d.X(), 3), round(d.Y(), 3), round(d.Z(), 3)),
                        "is_hole": is_reversed,
                    })

            # 3. 平面 (Plane: 安裝面 / 基準面 / 壁厚)
            elif stype == GeomAbs_Plane:
                pln = surf.Plane()
                loc = pln.Location()
                n = pln.Axis().Direction()
                norm = (round(n.X(), 3), round(n.Y(), 3), round(n.Z(), 3))

                # 計算面積與質心
                try:
                    props = GProp_GProps()
                    brepgprop.SurfaceProperties(face, props)
                    area = props.Mass()
                    cog = props.CentreOfMass()
                    cog_tuple = (round(cog.X(), 3), round(cog.Y(), 3), round(cog.Z(), 3))
                except Exception:
                    area = 0.0
                    cog_tuple = (round(loc.X(), 3), round(loc.Y(), 3), round(loc.Z(), 3))

                if area > 0.01:
                    key = (round(cog_tuple[0], 1), round(cog_tuple[1], 1), round(cog_tuple[2], 1), norm)
                    if key not in seen_plane:
                        seen_plane.add(key)
                        self.planes.append({
                            "normal": norm,
                            "location": (round(loc.X(), 3), round(loc.Y(), 3), round(loc.Z(), 3)),
                            "center_of_mass": cog_tuple,
                            "area": round(area, 2),
                            "is_reversed": is_reversed,
                        })

            # 4. 圓環面 (Torus: O-ring 槽 / 環形凹槽)
            elif stype == GeomAbs_Torus:
                torus = surf.Torus()
                maj_r = torus.MajorRadius()
                min_r = torus.MinorRadius()
                loc = torus.Location()
                d = torus.Axis().Direction()
                key = (round(loc.X(), 2), round(loc.Y(), 2), round(loc.Z(), 2), round(maj_r, 2), round(min_r, 2))
                if key not in seen_torus:
                    seen_torus.add(key)
                    self.toruses.append({
                        "major_radius": round(maj_r, 4),
                        "minor_radius": round(min_r, 4),
                        "major_diameter": round(maj_r * 2, 4),
                        "center": (round(loc.X(), 3), round(loc.Y(), 3), round(loc.Z(), 3)),
                        "axis_dir": (round(d.X(), 3), round(d.Y(), 3), round(d.Z(), 3)),
                        "is_hole": is_reversed,
                    })

            # 5. 球面 (Sphere)
            elif stype == GeomAbs_Sphere:
                sph = surf.Sphere()
                r = sph.Radius()
                loc = sph.Location()
                key = (round(loc.X(), 2), round(loc.Y(), 2), round(loc.Z(), 2), round(r, 2))
                if key not in seen_sphere:
                    seen_sphere.add(key)
                    self.spheres.append({
                        "radius": round(r, 4),
                        "diameter": round(r * 2, 4),
                        "center": (round(loc.X(), 3), round(loc.Y(), 3), round(loc.Z(), 3)),
                    })

            exp.Next()

        # 排序
        self.holes.sort(key=lambda x: -x["radius"])
        self.shafts.sort(key=lambda x: -x["radius"])
        self.planes.sort(key=lambda x: -x["area"])

    def _extract_edges(self):
        """遍歷所有 EDGE，提取圓弧邊 (圓角 Fillet/Round) 與全圓邊"""
        raw_circ_edges = []

        exp = TopExp_Explorer(self.shape, TopAbs_EDGE)
        while exp.More():
            edge = exp.Current()
            try:
                curve = BRepAdaptor_Curve(edge)
                if curve.GetType() == GeomAbs_Circle:
                    circ = curve.Circle()
                    r = circ.Radius()
                    if r > 0.05:
                        loc = circ.Location()
                        u_min = curve.FirstParameter()
                        u_max = curve.LastParameter()
                        sweep = abs(u_max - u_min)
                        mid_pnt = curve.Value((u_min + u_max) / 2)
                        p_start = curve.Value(u_min)
                        p_end = curve.Value(u_max)
                        d = circ.Axis().Direction()

                        raw_circ_edges.append({
                            "radius": r,
                            "center": (loc.X(), loc.Y(), loc.Z()),
                            "axis_dir": (d.X(), d.Y(), d.Z()),
                            "sweep": sweep,
                            "mid_pnt": (mid_pnt.X(), mid_pnt.Y(), mid_pnt.Z()),
                            "p_start": (p_start.X(), p_start.Y(), p_start.Z()),
                            "p_end": (p_end.X(), p_end.Y(), p_end.Z()),
                        })
            except Exception:
                pass
            exp.Next()

        # 依 (center, axis_dir, radius) 聚合圓邊
        circle_groups = {}
        for edge_info in raw_circ_edges:
            cx, cy, cz = edge_info["center"]
            dx, dy, dz = edge_info["axis_dir"]
            r = edge_info["radius"]
            # 軸向方向統一 (同一直線正負號同向化)
            sign = 1 if (dx + dy + dz) >= 0 else -1
            ckey = (
                round(cx, 2), round(cy, 2), round(cz, 2),
                round(dx * sign, 1), round(dy * sign, 1), round(dz * sign, 1),
                round(r, 2)
            )
            if ckey not in circle_groups:
                circle_groups[ckey] = []
            circle_groups[ckey].append(edge_info)

        for ckey, group in circle_groups.items():
            total_sweep = sum(e["sweep"] for e in group)
            first_e = group[0]
            r = first_e["radius"]
            c = first_e["center"]
            ax_d = first_e["axis_dir"]

            if total_sweep >= 2 * math.pi - 0.15:
                # 總弧度達到 360° -> 封閉整圓邊 (Step / Cylinder rim)
                self.circle_edges.append({
                    "radius": round(r, 4),
                    "diameter": round(r * 2, 4),
                    "center": (round(c[0], 3), round(c[1], 3), round(c[2], 3)),
                    "axis_dir": (round(ax_d[0], 3), round(ax_d[1], 3), round(ax_d[2], 3)),
                })
            else:
                # 真正的不完全圓弧 -> 圓角 Fillet
                seen_mids = set()
                for e in group:
                    m = e["mid_pnt"]
                    mkey = (round(m[0], 2), round(m[1], 2), round(m[2], 2))
                    if mkey not in seen_mids:
                        seen_mids.add(mkey)
                        arc_len = r * e["sweep"]
                        self.fillets.append({
                            "radius": round(r, 4),
                            "diameter": round(r * 2, 4),
                            "center": (round(c[0], 3), round(c[1], 3), round(c[2], 3)),
                            "mid_point": (round(m[0], 3), round(m[1], 3), round(m[2], 3)),
                            "start_point": (round(e["p_start"][0], 3), round(e["p_start"][1], 3), round(e["p_start"][2], 3)),
                            "end_point": (round(e["p_end"][0], 3), round(e["p_end"][1], 3), round(e["p_end"][2], 3)),
                            "sweep_angle_deg": round(math.degrees(e["sweep"]), 2),
                            "arc_length": round(arc_len, 3),
                        })

        self.fillets.sort(key=lambda x: -x["radius"])
        self.circle_edges.sort(key=lambda x: -x["radius"])

    def _detect_step_segments(self):
        """偵測沿主軸方向的所有段差結構 (階梯軸/階梯孔)"""
        if not self.cylinders_raw:
            return

        axis_groups = {}
        for cyl in self.cylinders_raw:
            dx, dy, dz = cyl["axis_dir"]
            adx, ady, adz = abs(dx), abs(dy), abs(dz)
            max_comp = max(adx, ady, adz)
            if max_comp < 0.01:
                continue
            if adz > 0.7:
                axis_key = "Z"
            elif ady > 0.7:
                axis_key = "Y"
            elif adx > 0.7:
                axis_key = "X"
            else:
                axis_key = f"({round(dx,1)},{round(dy,1)},{round(dz,1)})"
            
            if axis_key not in axis_groups:
                axis_groups[axis_key] = []
            axis_groups[axis_key].append(cyl)

        if not axis_groups:
            return

        main_axis = max(axis_groups, key=lambda k: len(axis_groups[k]))
        main_cyls = axis_groups[main_axis]

        if main_axis == "Z":
            proj_idx = 2
        elif main_axis == "Y":
            proj_idx = 1
        else:
            proj_idx = 0

        segments = []
        for cyl in main_cyls:
            cx, cy, cz = cyl["center"]
            pos = [cx, cy, cz][proj_idx]
            segments.append({
                "position": round(pos, 3),
                "diameter": round(cyl["diameter"], 3),
                "radius": round(cyl["radius"], 3),
                "length": round(cyl["length"], 3),
                "start": round(cyl["start_pos"], 3),
                "end": round(cyl["end_pos"], 3),
                "is_hole": cyl["is_hole"],
            })

        segments.sort(key=lambda s: s["position"])

        # 合併與去重
        merged = []
        for seg in segments:
            if merged and abs(merged[-1]["diameter"] - seg["diameter"]) < 0.02 and abs(merged[-1]["position"] - seg["position"]) < 0.5:
                merged[-1]["length"] = max(merged[-1]["length"], seg["length"])
                merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
            else:
                merged.append(dict(seg))

        self.step_segments = sorted(merged, key=lambda s: s["position"])
        self.main_axis = main_axis

    def _detect_hole_patterns(self):
        """偵測圓周孔群陣列 (PCD)"""
        if len(self.holes) < 2:
            return

        # 依直徑與軸向分組
        groups = {}
        for h in self.holes:
            d_key = round(h["diameter"], 2)
            ax_key = (round(h["axis_dir"][0], 1), round(h["axis_dir"][1], 1), round(h["axis_dir"][2], 1))
            k = (d_key, ax_key)
            if k not in groups:
                groups[k] = []
            groups[k].append(h)

        patterns = []
        for (dia, ax_dir), hole_list in groups.items():
            if len(hole_list) >= 2:
                # 計算中心平均位置
                cx = sum(h["center"][0] for h in hole_list) / len(hole_list)
                cy = sum(h["center"][1] for h in hole_list) / len(hole_list)
                cz = sum(h["center"][2] for h in hole_list) / len(hole_list)

                # 計算每個孔到平均中心的距離
                radii = []
                for h in hole_list:
                    dx = h["center"][0] - cx
                    dy = h["center"][1] - cy
                    dz = h["center"][2] - cz
                    r_pcd = math.sqrt(dx * dx + dy * dy + dz * dz)
                    radii.append(r_pcd)

                avg_r = sum(radii) / len(radii)
                # 若半徑分散很小且大於 2mm，視為同一個 PCD 孔群
                if avg_r > 2.0 and max(abs(r - avg_r) for r in radii) < 1.0:
                    patterns.append({
                        "count": len(hole_list),
                        "hole_diameter": round(dia, 2),
                        "pcd": round(avg_r * 2, 2),
                        "pattern_center": (round(cx, 2), round(cy, 2), round(cz, 2)),
                        "axis_dir": ax_dir,
                        "holes": hole_list,
                    })

        self.hole_patterns = sorted(patterns, key=lambda p: -p["count"])

    def _detect_thicknesses(self):
        """偵測主要平面間的壁厚與台階厚度"""
        if len(self.planes) < 2:
            return

        # 依主軸法向量 (X, Y, Z) 分組
        norm_groups = {"X": [], "Y": [], "Z": []}
        for p in self.planes:
            nx, ny, nz = p["normal"]
            if abs(nx) > 0.8:
                norm_groups["X"].append((p["center_of_mass"][0], p))
            elif abs(ny) > 0.8:
                norm_groups["Y"].append((p["center_of_mass"][1], p))
            elif abs(nz) > 0.8:
                norm_groups["Z"].append((p["center_of_mass"][2], p))

        thicknesses = []
        for axis_name, plane_pairs in norm_groups.items():
            if len(plane_pairs) >= 2:
                # 按座標排序
                plane_pairs.sort(key=lambda item: item[0])
                for i in range(len(plane_pairs) - 1):
                    pos1, p1 = plane_pairs[i]
                    pos2, p2 = plane_pairs[i + 1]
                    dist = abs(pos2 - pos1)
                    if 0.2 < dist < max(self.W, self.H, self.D) * 0.9:
                        thicknesses.append({
                            "axis": axis_name,
                            "thickness": round(dist, 3),
                            "pos1": round(pos1, 3),
                            "pos2": round(pos2, 3),
                            "area1": p1["area"],
                            "area2": p2["area"],
                        })

        # 去重與排序
        unique_th = []
        seen = set()
        for t in thicknesses:
            k = (t["axis"], round(t["thickness"], 2))
            if k not in seen:
                seen.add(k)
                unique_th.append(t)

        self.thicknesses = sorted(unique_th, key=lambda t: t["thickness"])

    def get_step_dims_for_view(self, view_name):
        if not self.step_segments:
            return {"is_profile": False, "segments": [], "overall_length": 0, "main_axis": None}

        axis = getattr(self, 'main_axis', None)
        if axis == "Z":
            is_profile = view_name in ('front', 'right')
        elif axis == "Y":
            is_profile = view_name in ('front', 'right')
        elif axis == "X":
            is_profile = view_name in ('top', 'right')
        else:
            is_profile = False

        overall = max(s["end"] - s["start"] for s in self.step_segments) if self.step_segments else 0

        return {
            "is_profile": is_profile,
            "segments": self.step_segments,
            "overall_length": round(overall, 3),
            "main_axis": axis,
        }

    def get_bbox_dimensions(self):
        return {"W": round(self.W, 3), "H": round(self.H, 3), "D": round(self.D, 3)}

    def get_overall_spec(self):
        parts = []
        if self.shafts:
            max_od = max(s["diameter"] for s in self.shafts)
            parts.append(f"OD{max_od:.2f}")
        if self.holes:
            max_id = max(h["diameter"] for h in self.holes)
            parts.append(f"ID{max_id:.2f}")
        h = max(self.H, self.D)
        if h > 0.1:
            parts.append(f"{h:.2f}L")
        return "x".join(parts) if parts else f"{self.W:.2f}x{self.H:.2f}x{self.D:.2f}"

    def summary(self):
        """完整回傳所有提取到的特徵資料，不做任何截斷"""
        main_ax = getattr(self, 'main_axis', None)
        if not main_ax:
            if self.H >= self.W and self.H >= self.D:
                main_ax = "Y"
            elif self.D >= self.W and self.D >= self.H:
                main_ax = "Z"
            else:
                main_ax = "X"

        return {
            "bounding_box": {"W": round(self.W, 3), "H": round(self.H, 3), "D": round(self.D, 3)},
            "center": (round((self.xmin + self.xmax) / 2.0, 3), round((self.ymin + self.ymax) / 2.0, 3), round((self.zmin + self.zmax) / 2.0, 3)),
            "bounds": (round(self.xmin, 3), round(self.ymin, 3), round(self.zmin, 3), round(self.xmax, 3), round(self.ymax, 3), round(self.zmax, 3)),
            "spec": self.get_overall_spec(),
            "main_axis": main_ax,
            "holes_count": len(self.holes),
            "shafts_count": len(self.shafts),
            "cones_count": len(self.cones),
            "fillets_count": len(self.fillets),
            "circle_edges_count": len(self.circle_edges),
            "planes_count": len(self.planes),
            "thicknesses_count": len(self.thicknesses),
            "step_segments_count": len(self.step_segments),
            "step_segments": len(self.step_segments),
            "hole_patterns_count": len(self.hole_patterns),
            "toruses_count": len(self.toruses),
            "spheres_count": len(self.spheres),
            "counts": {
                "holes_count": len(self.holes),
                "shafts_count": len(self.shafts),
                "cones_count": len(self.cones),
                "fillets_count": len(self.fillets),
                "circle_edges_count": len(self.circle_edges),
                "planes_count": len(self.planes),
                "thicknesses_count": len(self.thicknesses),
                "step_segments_count": len(self.step_segments),
                "hole_patterns_count": len(self.hole_patterns),
                "toruses_count": len(self.toruses),
                "spheres_count": len(self.spheres),
            },
            # 完整特徵清單
            "holes": self.holes,
            "shafts": self.shafts,
            "cones": self.cones,
            "fillets": self.fillets,
            "circle_edges": self.circle_edges,
            "planes": self.planes,
            "thicknesses": self.thicknesses,
            "steps": self.step_segments,
            "hole_patterns": self.hole_patterns,
            "toruses": self.toruses,
            "spheres": self.spheres,
        }
