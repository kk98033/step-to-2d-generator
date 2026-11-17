"""Isolated native-mm drawing experiment. Never imported by the web worker.

Reuse V1 feature/selection validation in a PRIVATE module namespace, replacing
only its drawing callbacks. Geometry is converted to native millimetres before
paper presentation. Each invocation owns its callbacks and view cache.
"""
from __future__ import annotations

import importlib.util
import json
import math
import re
import sys
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf import xref
from ezdxf.math import Matrix44

LAB = Path(__file__).resolve().parents[1]
ROOT = LAB.parents[1]
SCRIPTS = LAB / "common/scripts"
APPID = "DRAWING_V2"
NUMBER_LABEL = re.compile(r"^(?P<prefix>%%[cC]|[Øø⌀])?(?P<value>[-+]?\d+(?:\.\d+)?)(?:\s*\((?P<upper>[+-]\d+(?:\.\d+)?)/(?P<lower>[+-]\d+(?:\.\d+)?)\))?$")


def _metadata(entity):
    if not entity.has_xdata(APPID):
        return {}
    return json.loads(entity.get_xdata(APPID)[0].value)


def _native_text(dimension, scale):
    """Use CAD's computed value, retaining explicit limit tolerances."""
    match = NUMBER_LABEL.fullmatch(dimension.dxf.get("text", "").strip())
    if not match:
        return  # Composite/angular callouts retain their existing semantics.
    measured = dimension.get_measurement()
    if not isinstance(measured, (int, float)) or not math.isclose(
        abs(measured), abs(float(match["value"])), abs_tol=0.006, rel_tol=1e-5
    ):
        raise ValueError(f"Native dimension disagrees with displayed value: {measured}, {match['value']}")
    # Diameter renderer supplies its own diameter sign for computed text.
    prefix = "" if int(dimension.dxf.dimtype) & 7 == 3 else (match["prefix"] or "")
    dimension.dxf.text = prefix + "<>"
    override = dimension.override()
    override.update({"dimlfac": 1.0, "dimdec": 2, "dimrnd": 0.01,
                     "dimdsep": 46, "dimzin": 0, "dimtxt": 2.5 / scale,
                     "dimtfill": 1,
                     "dimasz": 2.0 / scale, "dimgap": 0.6 / scale,
                     "dimexo": 0.6 / scale, "dimexe": 1.0 / scale})
    if match["upper"] is not None:
        override.update({"dimtol": 1, "dimtp": float(match["upper"]),
                         "dimtm": -float(match["lower"]), "dimtdec": 3, "dimtzin": 0})
    if int(dimension.dxf.dimtype) & 128:
        # With a leader ezdxf stores its corner, not the label centre, in group 11.
        texts = [e for e in dimension.virtual_entities() if e.dxftype() in ("TEXT","MTEXT")]
        location = texts[0].dxf.insert if texts else dimension.dxf.text_midpoint
        override.set_location(location, leader=override.get("dimtmove", 0)==1, relative=False)
    override.render()
    if int(dimension.dxf.dimtype) & 7 == 3:
        from engineering_dimension_renderer import orient_diameter_arrows_inward
        orient_diameter_arrows_inward(dimension.doc, dimension)


def _sheet(document, target, part_name, views, scale, width=420.0, height=297.0):
    """Paper frame/text is in paper space; CAD geometry remains 1:1 mm."""
    from auto_2d_drawing.pdf_exporter import export_pdf, export_png
    document.units = ezdxf.units.MM
    sheet = document.layouts.new("ENGINEERING_SHEET")
    sheet.page_setup(size=(width, height), margins=(0, 0, 0, 0))
    sheet.add_lwpolyline([(8, 8), (width-8, 8), (width-8, height-8), (8, height-8)], close=True)
    sheet.add_line((8, 35), (width-8, 35))
    sheet.add_text(str(part_name)[:68], dxfattribs={"insert": (13, 25), "height": 3.2})
    sheet.add_text(f"UNITS: mm   SCALE: {scale:g}:1   EXPERIMENT V2 - ENGINEER REVIEW REQUIRED",
                   dxfattribs={"insert": (13, 15), "height": 2.4})
    for view in views:
        cx, cy = view["paper_center"]
        vw, vh = view["paper_size"]
        sheet.add_viewport(center=(cx, cy), size=(vw, vh),
                           view_center_point=view["model_center"], view_height=vh/scale)
        sheet.add_text(f"{view['name'].upper()} VIEW", dxfattribs={
            "insert": (cx-vw/2+3, cy-vh/2-6), "height": 2.5})
    auditor = document.audit()
    if auditor.errors or auditor.fixes:
        raise ValueError("V2 DXF audit failed before preview export")
    document.saveas(target)
    export_pdf(document, sheet, str(target.with_suffix(".pdf")), dark_bg=False)
    export_png(document, sheet, str(target.with_suffix(".png")), dark_bg=False)


def audit(path):
    document = ezdxf.readfile(path)
    auditor = document.audit()
    dimensions = list(document.modelspace().query("DIMENSION"))
    collisions = []
    text_boxes = []
    for dim in dimensions:
        extent = bbox.extents([e for e in dim.virtual_entities() if e.dxftype() in ("TEXT", "MTEXT")])
        if extent.has_data:
            text_boxes.append((dim.dxf.handle, extent, _metadata(dim)))
    for i, (handle, first, meta) in enumerate(text_boxes):
        for other_handle, other, other_meta in text_boxes[i+1:]:
            if (min(first.extmax.x,other.extmax.x) > max(first.extmin.x,other.extmin.x)+1e-6
                    and min(first.extmax.y,other.extmax.y) > max(first.extmin.y,other.extmin.y)+1e-6):
                collisions.append([handle, other_handle])
    records = [{"handle": d.dxf.handle, "measurement": d.get_measurement(),
                "text": d.dxf.text, **_metadata(d)} for d in dimensions]
    return {"units_mm": document.units == ezdxf.units.MM,
            "dimension_count": len(dimensions), "audit_error_count": len(auditor.errors),
            "audit_fix_count": len(auditor.fixes), "text_collision_pairs": collisions,
            "dimension_records": records}


def _enforce_parallel_label_side(document, dimension, size, scale):
    meta = _metadata(dimension)
    if meta.get("direction_policy") != "LABEL_WITH_LEADER":
        return
    side = meta["side"]
    for _ in range(4):
        bounds = bbox.extents([e for e in dimension.virtual_entities() if e.dxftype() in ("TEXT","MTEXT")])
        if not bounds.has_data: raise ValueError("Selected dimension has no visible label")
        gap = 4.0/scale
        dx=dy=0.0
        if side=="RIGHT" and bounds.extmin.x <= size[0]: dx=size[0]+gap-bounds.extmin.x
        if side=="LEFT" and bounds.extmax.x >= 0: dx=-gap-bounds.extmax.x
        if side=="TOP" and bounds.extmin.y <= size[1]: dy=size[1]+gap-bounds.extmin.y
        if side=="BOTTOM" and bounds.extmax.y >= 0: dy=-gap-bounds.extmax.y
        if dx==dy==0: return
        location = next(e.dxf.insert for e in dimension.virtual_entities() if e.dxftype() in ("TEXT","MTEXT"))
        override = dimension.override()
        override.set_location((location.x+dx,location.y+dy),leader=True,relative=False)
        override.render()
    raise ValueError(f"Cannot honor selected label side: {side}")


def render(step_path, selections, output_dir, **kwargs):
    for path in (ROOT, SCRIPTS):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    spec = importlib.util.spec_from_file_location("_drawing_v2_private_baseline", SCRIPTS / "minimal_multiview_drawing.py")
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    original_inject = base._inject_specialized_dimensions
    original_annular = base._inject_annular_dimensions
    original_reproject = base.reproject_measurement
    original_one = base._render_one
    native_views = {}

    def reproject(candidate, target_view, *, annular_feature=None):
        task=original_reproject(candidate,target_view,annular_feature=annular_feature)
        if annular_feature and str(task.get("dim_type","")).upper()=="DIAMETER":
            from auto_2d_drawing.drawing_projection import view_basis
            axis=annular_feature["axis"]
            normal=view_basis(target_view)["normal"]
            alignment=abs(sum(float(a)*float(b) for a,b in zip(axis,normal)))/math.sqrt(sum(float(a)**2 for a in axis))
            if alignment < 1-1e-5:
                # An edge-on cylinder is not a circle in this image. Use an
                # offset linear dimension with a diameter prefix and true anchors.
                task["dim_type"]="LINEAR"
                task["renderer"]="LINEAR_DIAMETER_RENDERER"
                task["measurement_kind"]="DIAMETER"
                task["require_anchor_alignment"]=True
        return task

    def annular(document,tasks,view,placement=None):
        msp=document.modelspace()
        before={d.dxf.handle for d in msp.query("DIMENSION")}
        original_annular(document,tasks,view,placement)
        added=[d for d in msp.query("DIMENSION") if d.dxf.handle not in before]
        if len(added)!=len(tasks): raise ValueError("Annular renderer lost selected dimensions")
        mapper,scale,_=base._coordinate_mapper(msp,view,placement)
        for task,dimension in zip(tasks,added):
            if not task.get("require_anchor_alignment"): continue
            expected=[mapper(p) for p in task["anchors"]]
            actual=[tuple(dimension.dxf.defpoint2)[:2],tuple(dimension.dxf.defpoint3)[:2]]
            distance=min(max(math.dist(expected[i],actual[i]) for i in (0,1)),
                         max(math.dist(expected[i],actual[1-i]) for i in (0,1)))
            if distance > max(1e-5,scale*1e-5):
                raise ValueError("Side-view diameter endpoints disagree with projected feature anchors")

    def inject(document, tasks, view, placement=None):
        original_inject(document, [t for t in tasks if t.get("renderer") == "DIAMETER_RENDERER"], view, placement)
        document.appids.add(APPID) if APPID not in document.appids else None
        msp = document.modelspace()
        mapper, scale, box = base._coordinate_mapper(msp, view, placement)
        lane_counts = {}
        rail_counts = {}
        for task in tasks:
            if task.get("renderer") == "DIAMETER_RENDERER":
                continue
            p1, p2 = [mapper(p) for p in (task.get("anchors") or (task.get("p1"), task.get("p2")))]
            if p1 > p2:
                p1,p2 = p2,p1
            dx, dy = p2[0]-p1[0], p2[1]-p1[1]
            length = math.hypot(dx, dy)
            if length < 1e-7:
                raise ValueError("Collapsed selected-view dimension")
            normal = (-dy/length, dx/length)
            side = str(task.get("side") or "TOP").upper()
            lane = lane_counts.get(side, 0)
            lane_counts[side] = lane + 1
            dot = sum(a*b for a,b in zip(normal, base._side_vector(side)))
            parallel = abs(dot) < 0.1
            sign = (-1 if side in ("LEFT", "BOTTOM") else 1) if parallel else (1 if dot > 0 else -1)
            # Fixed paper clearances, independent of model size.
            rail_key = (round(abs(normal[0]),4),round(abs(normal[1]),4),sign)
            rail = rail_counts.get(rail_key,0)
            rail_counts[rail_key] = rail + 1
            distance = sign * (12.0 + rail * 9.0)
            dim = msp.add_aligned_dim(p1, p2, distance=distance,
                text=str(task.get("label") or f"{task['nominal_value']:.3f}"),
                dimstyle="EZDXF", override={"dimtxt":2.5,"dimasz":2.0,"dimgap":0.6},
                dxfattribs={"layer":"DIM"})
            if parallel:
                # Direction specifies label placement, not an invented measurement axis.
                if side in ("LEFT", "RIGHT"):
                    location = ((box[0]-20-lane*12) if side=="LEFT" else (box[2]+20+lane*12),
                                (p1[1]+p2[1])/2 + distance + sign*5)
                else:
                    location = ((p1[0]+p2[0])/2 + distance + sign*5,
                                (box[1]-20-lane*12) if side=="BOTTOM" else (box[3]+20+lane*12))
                dim.set_location(location, leader=True, relative=False)
            dim.dimension.set_xdata(APPID, [(1000, json.dumps({
                "feature_id":str(task.get("feature_id") or task.get("dimension_id") or task.get("id") or ""),
                "view":task.get("view"), "side":side, "nominal":float(task["nominal_value"]),
                "direction_policy":"LABEL_WITH_LEADER" if parallel else "NORMAL_DIMENSION_LINE"}))])
            dim.render()

    def one(engine, out, name, view_name, view, rules, title, **options):
        result = original_one(engine, out, name, view_name, view, rules, title, **options)
        if result["audit_error_count"] or not result["full_span_diameter_contract_passed"]:
            raise ValueError("Baseline geometry/diameter validation failed; stop V2")
        path = Path(result["files"]["dxf"])
        document = ezdxf.readfile(path)
        msp = document.modelspace()
        _, scale, box = base._coordinate_mapper(msp, view)
        if scale <= 0:
            raise ValueError("Invalid view scale")
        # Keep only geometry and dimensions, remove V1 frame/material placeholders.
        for entity in list(msp):
            if (entity.dxftype() != "DIMENSION" and
                    entity.dxf.layer.upper() not in {"VISIBLE", "HIDDEN", "DIM", "CENTER", "CENTERLINE"}):
                msp.delete_entity(entity)
        if len(msp.query("DIMENSION")) != result["dimension_count"]:
            raise ValueError("Removing frame entities lost selected dimensions")
        matrix = Matrix44.chain(Matrix44.translate(-box[0], -box[1], 0), Matrix44.scale(1/scale))
        for entity in list(msp):
            entity.transform(matrix)
            if entity.dxftype() == "DIMENSION":
                _native_text(entity, scale)
                _enforce_parallel_label_side(document,entity,
                    (float(view['bbox'][2])-float(view['bbox'][0]),
                     float(view['bbox'][3])-float(view['bbox'][1])),scale)
        document.units = ezdxf.units.MM
        content = bbox.extents(msp)
        width, height = content.size.x, content.size.y
        paper_scale = min(380/max(width,1e-9), 225/max(height,1e-9), 5.0)
        _sheet(document, path, title.get("part_name") or step_path.stem, [{
            "name":name,"paper_center":(210,161),"paper_size":(390,235),
            "model_center":((content.extmin.x+content.extmax.x)/2,(content.extmin.y+content.extmax.y)/2)}],paper_scale)
        native_views[name] = path
        result.update(audit(path))
        result.update(base._audit_dxf(path))
        if result["audit_error_count"] or not result["full_span_diameter_arrows_inward"]:
            raise ValueError("Native-mm conversion violated dimension geometry contract")
        result["native_geometry_scale"] = 1.0
        result["paper_scale"] = paper_scale
        return result

    def combined(engine, out, views, rules, title, angular, annular, specialized):
        document = ezdxf.new("R2010", setup=True)
        msp = document.modelspace()
        cells = []
        content_rectangles = []
        viewport_rectangles = []
        sources = []
        for index, name in enumerate(base.ORTHOGRAPHIC_VIEWS):
            source = ezdxf.readfile(native_views[name])
            bounds = bbox.extents(source.modelspace())
            sources.append((name, source, bounds))
        # Uniform scale preserves orthographic proportions. Bounds include annotations.
        scale = min(min(120/max(b.size.x,1e-9),210/max(b.size.y,1e-9)) for _,_,b in sources)
        scale = min(scale,5.0)
        for index, (name, source, bounds) in enumerate(sources):
            before = {e.dxf.handle for e in msp}
            loader = xref.Loader(source,document,conflict_policy=xref.ConflictPolicy.XREF_PREFIX)
            loader.load_modelspace()
            loader.execute(xref_prefix=name)
            entities = [e for e in msp if e.dxf.handle not in before]
            if APPID not in document.appids:
                document.appids.add(APPID)
            if len(entities) != len(source.modelspace()):
                raise ValueError("View import lost native CAD entities")
            # Restore audit identity independently of cross-document handle remapping.
            for old, new in zip(source.modelspace(),entities):
                if old.has_xdata(APPID): new.set_xdata(APPID,old.get_xdata(APPID))
            # Viewports must not see adjacent native views when their widths vary.
            offset = (index*(500/scale),0)
            matrix = Matrix44.translate(offset[0]-bounds.extmin.x,-bounds.extmin.y,0)
            for entity in entities: entity.transform(matrix)
            center = (offset[0]+bounds.size.x/2,bounds.size.y/2)
            content_rectangles.append((offset[0],0,offset[0]+bounds.size.x,bounds.size.y))
            viewport_rectangles.append((center[0]-62.5/scale,center[1]-115/scale,
                                        center[0]+62.5/scale,center[1]+115/scale))
            cells.append({"name":name,"paper_center":(75+index*135,161),"paper_size":(125,230),
                          "model_center":center})
        isolation_errors = []
        for i, viewport in enumerate(viewport_rectangles):
            for j, content in enumerate(content_rectangles):
                if i != j and (min(viewport[2],content[2]) > max(viewport[0],content[0])
                               and min(viewport[3],content[3]) > max(viewport[1],content[1])):
                    isolation_errors.append([i,j])
        if isolation_errors:
            raise ValueError(f"Viewport exposes adjacent model view: {isolation_errors}")
        path = Path(out)/"multiview/multiview.dxf"
        path.parent.mkdir(parents=True,exist_ok=True)
        expected_dimensions = sum(len(s.modelspace().query("DIMENSION")) for _,s,_ in sources)
        if len(msp.query("DIMENSION")) != expected_dimensions:
            raise ValueError("Combined drawing lost selected dimensions")
        _sheet(document,path,title.get("part_name") or step_path.stem,cells,scale)
        result = base._audit_dxf(path)
        expected = sum(v["full_span_diameter_count"] for v in [base._audit_dxf(p) for p in native_views.values()])
        result.update(audit(path))
        return {"view":"multiview","files":{ext:str(path.with_suffix('.'+ext).resolve()) for ext in ('dxf','pdf','png')},
                "layout":{"overlap_free":not isolation_errors,"viewport_isolation_errors":isolation_errors,
                          "strategy":"NATIVE_MM_PAPER_VIEWPORTS","scale":scale},
                "full_span_diameter_contract_passed":result["full_span_diameter_count"]==expected and result["full_span_diameter_arrows_inward"],
                "native_geometry_scale":1.0,**result}

    base._inject_specialized_dimensions = inject
    base._inject_annular_dimensions = annular
    base.reproject_measurement = reproject
    base._render_one = one
    base._render_combined_multiview = combined
    report = base.render_minimal_multiview_drawing(Path(step_path), selections, Path(output_dir), **kwargs)
    report["method"] = "DRAWING_V2_NATIVE_MM_EXPERIMENT"
    report["production_integrated"] = False
    quality = report["combined_multiview_drawing"]
    report["quality_passed"] = bool(report["success"] and quality["units_mm"]
                                    and not quality["text_collision_pairs"] and not quality["audit_fix_count"])
    (Path(output_dir)/"v2_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report
