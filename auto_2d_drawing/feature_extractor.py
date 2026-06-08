"""
3D 幾何特徵提取模組 v2 — 增強版
包含: 階梯段落偵測、沿軸各段直徑/長度提取
"""
import math
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_REVERSED
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.GeomAbs import GeomAbs_Cylinder, GeomAbs_Circle, GeomAbs_Plane
from OCC.Core.gp import gp_Vec


class FeatureExtractor:
    """從 TopoDS_Shape 中提取幾何特徵，用於自動標註"""

    def __init__(self, shape):
        self.shape = shape
        # Bounding Box
        bbox = Bnd_Box()
        brepbndlib.Add(shape, bbox)
        self.xmin, self.ymin, self.zmin, self.xmax, self.ymax, self.zmax = bbox.Get()
        self.W = self.xmax - self.xmin   # X 方向
        self.H = self.ymax - self.ymin   # Y 方向
        self.D = self.zmax - self.zmin   # Z 方向

        # 特徵容器
        self.holes = []
        self.shafts = []
        self.fillets = []
        self.cylinders_raw = []   # 所有圓柱面 (未分類)
        self.step_segments = []   # 沿主軸的段差結構

        # 提取
        self._extract_cylindrical_features()
        self._extract_fillet_features()
        self._detect_step_segments()

    def _extract_cylindrical_features(self):
        """遍歷所有 FACE，找出圓柱面"""
        seen = set()
        exp = TopExp_Explorer(self.shape, TopAbs_FACE)
        while exp.More():
            face = exp.Current()
            surf = BRepAdaptor_Surface(face)
            if surf.GetType() == GeomAbs_Cylinder:
                cyl = surf.Cylinder()
                r = cyl.Radius()
                loc = cyl.Location()
                d = cyl.Axis().Direction()
                v0, v1 = surf.FirstVParameter(), surf.LastVParameter()

                # 計算中心點 (沿軸向的中間位置)
                vec = gp_Vec(d).Multiplied((v0 + v1) / 2)
                cp = loc.Translated(vec)
                length = abs(v1 - v0)

                # 計算沿軸的起止位置
                start_pos = min(v0, v1)
                end_pos = max(v0, v1)

                key = (round(cp.X(), 1), round(cp.Y(), 1), round(cp.Z(), 1), round(r, 2))
                if key not in seen:
                    seen.add(key)
                    info = {
                        "radius": r,
                        "diameter": r * 2,
                        "center": (cp.X(), cp.Y(), cp.Z()),
                        "axis_dir": (d.X(), d.Y(), d.Z()),
                        "length": length,
                        "start_pos": start_pos,
                        "end_pos": end_pos,
                        "is_hole": face.Orientation() == TopAbs_REVERSED,
                    }
                    self.cylinders_raw.append(info)
                    if info["is_hole"]:
                        self.holes.append(info)
                    else:
                        self.shafts.append(info)
            exp.Next()

        self.holes.sort(key=lambda x: -x["radius"])
        self.shafts.sort(key=lambda x: -x["radius"])

    def _extract_fillet_features(self):
        """遍歷所有 EDGE，找出圓弧邊"""
        seen = set()
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
                        key = (round(loc.X(), 1), round(loc.Y(), 1), round(loc.Z(), 1), round(r, 2))
                        if key not in seen:
                            seen.add(key)
                            u_min = curve.FirstParameter()
                            u_max = curve.LastParameter()
                            mid_pnt = curve.Value((u_min + u_max) / 2)
                            self.fillets.append({
                                "radius": r,
                                "center": (loc.X(), loc.Y(), loc.Z()),
                                "mid_point": (mid_pnt.X(), mid_pnt.Y(), mid_pnt.Z()),
                            })
            except Exception:
                pass
            exp.Next()

    def _detect_step_segments(self):
        """
        偵測沿主軸方向的段差結構 (階梯軸/階梯孔)
        
        找出所有圓柱面中最常見的軸方向，然後沿該軸排序
        各段的 {position, diameter, length}，用於標註
        """
        if not self.cylinders_raw:
            return

        # 找出主軸方向 (最多圓柱面共用的軸方向)
        axis_groups = {}
        for cyl in self.cylinders_raw:
            dx, dy, dz = cyl["axis_dir"]
            # 正規化方向 (讓 abs 最大的分量為正)
            adx, ady, adz = abs(dx), abs(dy), abs(dz)
            max_comp = max(adx, ady, adz)
            if max_comp < 0.01:
                continue
            # 把方向歸入主要軸向
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

        # 選擇最多圓柱面的方向為主軸
        main_axis = max(axis_groups, key=lambda k: len(axis_groups[k]))
        main_cyls = axis_groups[main_axis]

        # 計算沿主軸的投影位置
        if main_axis == "Z":
            proj_idx = 2  # Z axis
        elif main_axis == "Y":
            proj_idx = 1  # Y axis
        else:
            proj_idx = 0  # X axis

        # 建立段落: 按沿軸位置排序的直徑段
        segments = []
        for cyl in main_cyls:
            cx, cy, cz = cyl["center"]
            pos = [cx, cy, cz][proj_idx]
            segments.append({
                "position": pos,
                "diameter": cyl["diameter"],
                "radius": cyl["radius"],
                "length": cyl["length"],
                "start": cyl["start_pos"],
                "end": cyl["end_pos"],
                "is_hole": cyl["is_hole"],
            })

        # 按位置排序
        segments.sort(key=lambda s: s["position"])

        # 合併相同直徑的相鄰段落
        merged = []
        for seg in segments:
            if merged and abs(merged[-1]["diameter"] - seg["diameter"]) < 0.01:
                # 延伸現有段落
                merged[-1]["length"] = max(merged[-1]["length"], seg["length"])
                merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
            else:
                merged.append(dict(seg))

        # 去重: 移除重複的直徑段落 (相同直徑但不同位置)
        unique_diameters = {}
        for seg in merged:
            d_key = round(seg["diameter"], 1)
            if d_key not in unique_diameters or seg["length"] > unique_diameters[d_key]["length"]:
                unique_diameters[d_key] = seg
        
        self.step_segments = sorted(unique_diameters.values(), key=lambda s: s["position"])
        self.main_axis = main_axis

    def get_step_dims_for_view(self, view_name):
        """
        回傳指定視圖需要的段差標註資料
        
        對於旋轉對稱件 (軸/套筒):
        - profile view (看到長軸方向的截面): 標註各段直徑+長度
        - end view (看到圓形端面): 標註直徑
        
        Returns:
            {
                "is_profile": True/False,
                "segments": [{position, diameter, length}, ...],
                "overall_length": float,
                "main_axis": str,
            }
        """
        if not self.step_segments:
            return {"is_profile": False, "segments": [], "overall_length": 0, "main_axis": None}

        # 根據主軸和視圖方向決定是 profile view 還是 end view
        axis = getattr(self, 'main_axis', None)
        
        if axis == "Z":
            is_profile = view_name in ('front', 'right')  # 前/右看到側面
        elif axis == "Y":
            is_profile = view_name in ('front', 'right')  # 前/右看到側面
        elif axis == "X":
            is_profile = view_name in ('top', 'right')
        else:
            is_profile = False

        # 計算整體長度
        if self.step_segments:
            positions = [s["position"] for s in self.step_segments]
            overall = max(s["end"] - s["start"] for s in self.step_segments) if self.step_segments else 0
        else:
            overall = 0

        return {
            "is_profile": is_profile,
            "segments": self.step_segments,
            "overall_length": overall,
            "main_axis": axis,
        }

    def get_bbox_dimensions(self):
        return {"W": self.W, "H": self.H, "D": self.D}

    def get_overall_spec(self):
        parts = []
        if self.shafts:
            max_od = max(s["diameter"] for s in self.shafts)
            parts.append(f"ψ{max_od:.2f}")
        if self.holes:
            max_id = max(h["diameter"] for h in self.holes)
            parts.append(f"ψ{max_id:.2f}")
        h = max(self.H, self.D)
        if h > 0.1:
            parts.append(f"{h:.2f}L")
        return "×".join(parts) if parts else f"{self.W:.2f}×{self.H:.2f}×{self.D:.2f}"

    def summary(self):
        return {
            "bounding_box": {"W": round(self.W, 2), "H": round(self.H, 2), "D": round(self.D, 2)},
            "spec": self.get_overall_spec(),
            "holes_count": len(self.holes),
            "shafts_count": len(self.shafts),
            "fillets_count": len(self.fillets),
            "step_segments": len(self.step_segments),
            "main_axis": getattr(self, 'main_axis', None),
            "holes": [{"Ø": round(h["diameter"], 2), "len": round(h["length"], 2)} for h in self.holes[:10]],
            "shafts": [{"Ø": round(s["diameter"], 2), "len": round(s["length"], 2)} for s in self.shafts[:10]],
            "steps": [{"Ø": round(s["diameter"], 2), "len": round(s["length"], 2), "pos": round(s["position"], 2)} for s in self.step_segments],
        }
