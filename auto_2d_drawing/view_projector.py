"""
HLR 三視圖投影模組 — 將 3D 模型投影為 2D 輪廓邊緣
使用 python-occ Hidden Line Removal (HLR)
"""
from OCC.Core.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCC.Core.HLRAlgo import HLRAlgo_Projector
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Ax2
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.GeomAbs import GeomAbs_Line, GeomAbs_Circle
from OCC.Core.GCPnts import GCPnts_UniformDeflection

from config import VIEW_CONFIG, HLR_DEFLECTION


class ViewProjector:
    """三視圖投影器: 使用 HLR 演算法將 3D 模型投影為 2D 輪廓"""

    def project(self, shape, view_name):
        """
        投影指定視圖方向，回傳可見邊與隱藏邊。
        
        Args:
            shape: TopoDS_Shape
            view_name: 'front', 'top', 'right'
        
        Returns:
            (visible_compound, hidden_compound)
        """
        cfg = VIEW_CONFIG[view_name]
        d = cfg["direction"]
        u = cfg["up"]
        ax2 = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*d), gp_Dir(*u))

        hlr = HLRBRep_Algo()
        hlr.Add(shape)
        hlr.Projector(HLRAlgo_Projector(ax2))
        hlr.Update()
        hlr.Hide()

        hs = HLRBRep_HLRToShape(hlr)
        
        # 取得可見邊 (包含輪廓線和一般邊)
        vis = hs.VCompound()
        hid = hs.HCompound()
        
        # 也取得輪廓邊 (OutLine) — 對圓柱面等曲面的投影輪廓很重要
        try:
            outline_v = hs.OutLineVCompound()
            outline_h = hs.OutLineHCompound()
        except Exception:
            outline_v = None
            outline_h = None
        
        return vis, hid, outline_v, outline_h

    def extract_edges(self, compound):
        """
        從 HLR 結果中提取 2D 邊緣資料。
        
        Returns:
            list of edge dicts: [
                {'type': 'line', 'p1': (x,y), 'p2': (x,y)},
                {'type': 'circle', 'center': (x,y), 'radius': r, 'points': [(x,y),...]},
                {'type': 'spline', 'points': [(x,y),...]}
            ]
        """
        edges = []
        if compound is None or compound.IsNull():
            return edges

        exp = TopExp_Explorer(compound, TopAbs_EDGE)
        while exp.More():
            edge = exp.Current()
            try:
                curve = BRepAdaptor_Curve(edge)
                ct = curve.GetType()
                t0, t1 = curve.FirstParameter(), curve.LastParameter()

                if ct == GeomAbs_Line:
                    p1 = curve.Value(t0)
                    p2 = curve.Value(t1)
                    edges.append({
                        'type': 'line',
                        'p1': (p1.X(), p1.Y()),
                        'p2': (p2.X(), p2.Y()),
                    })
                elif ct == GeomAbs_Circle:
                    pts = self._discretize(curve, t0, t1)
                    c = curve.Circle()
                    edges.append({
                        'type': 'circle',
                        'center': (c.Location().X(), c.Location().Y()),
                        'radius': c.Radius(),
                        'points': pts,
                    })
                else:
                    pts = self._discretize(curve, t0, t1)
                    if len(pts) >= 2:
                        edges.append({'type': 'spline', 'points': pts})
            except Exception:
                pass
            exp.Next()
        return edges

    def get_edges_bbox(self, edges):
        """計算一組邊緣的 Bounding Box"""
        pts = []
        for e in edges:
            if e['type'] == 'line':
                pts.extend([e['p1'], e['p2']])
            elif 'points' in e:
                pts.extend(e['points'])
        if not pts:
            return 0, 0, 0, 0
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def _discretize(self, curve, t0, t1, deflection=None):
        """將曲線離散化為點集"""
        if deflection is None:
            deflection = HLR_DEFLECTION
        pts = []
        try:
            approx = GCPnts_UniformDeflection(curve, deflection, t0, t1)
            if approx.IsDone():
                for i in range(1, approx.NbPoints() + 1):
                    p = approx.Value(i)
                    pts.append((p.X(), p.Y()))
        except Exception:
            # Fallback: 均勻採樣 30 點
            for i in range(31):
                t = t0 + (t1 - t0) * i / 30
                p = curve.Value(t)
                pts.append((p.X(), p.Y()))
        return pts

    def _cut_half(self, shape):
        """用布林運算切掉 +X 半部，用於產生剖面圖"""
        from OCC.Core.Bnd import Bnd_Box
        from OCC.Core.BRepBndLib import brepbndlib
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCC.Core.gp import gp_Pnt
        
        bbox = Bnd_Box()
        brepbndlib.Add(shape, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        
        cx = (xmin + xmax) / 2.0
        
        # 建立涵蓋 +X 半邊的巨大方塊
        pad = 10.0
        p1 = gp_Pnt(cx, ymin - pad, zmin - pad)
        p2 = gp_Pnt(xmax + pad, ymax + pad, zmax + pad)
        
        cut_box = BRepPrimAPI_MakeBox(p1, p2).Shape()
        
        # 布林相減
        cut_algo = BRepAlgoAPI_Cut(shape, cut_box)
        cut_algo.Build()
        
        if cut_algo.IsDone():
            return cut_algo.Shape()
        return shape

    def project_all_views(self, shape, cut_half_right=False):
        """
        投影所有三視圖，回傳結構化資料。
        
        Args:
            shape: TopoDS_Shape
            cut_half_right: 若為 True，會將 shape 切一半再投影右視圖，以產生剖面
            
        Returns:
            dict: {
                'front': {'visible': [...], 'hidden': [...], 'bbox': (x0,y0,x1,y1), 'size': (w,h)},
                'top': {...},
                'right': {...},
            }
        """
        result = {}
        for vn in ['front', 'top', 'right']:
            target_shape = shape
            if vn == 'right' and cut_half_right:
                try:
                    target_shape = self._cut_half(shape)
                except Exception as e:
                    print(f"Cut failed: {e}")
                    target_shape = shape

            vis_comp, hid_comp, outline_v, outline_h = self.project(target_shape, vn)
            vis_edges = self.extract_edges(vis_comp)
            hid_edges = self.extract_edges(hid_comp)
            
            # 合併輪廓邊
            if outline_v:
                vis_edges.extend(self.extract_edges(outline_v))
            if outline_h:
                hid_edges.extend(self.extract_edges(outline_h))
            
            all_edges = vis_edges + hid_edges
            x0, y0, x1, y1 = self.get_edges_bbox(all_edges)
            w = max(x1 - x0, 0.01)
            h = max(y1 - y0, 0.01)
            result[vn] = {
                'visible': vis_edges,
                'hidden': hid_edges,
                'bbox': (x0, y0, x1, y1),
                'size': (w, h),
            }
        return result
