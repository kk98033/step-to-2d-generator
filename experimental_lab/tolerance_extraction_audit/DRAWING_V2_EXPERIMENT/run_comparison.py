"""Reproducible private-model comparison; dataset manifest stays outside Git.

python run_comparison.py --manifest <private.json> --output <runs/dataset>
Each case supplies step, original_dxf, selection_config, optional baseline_dxf.
Never treats numeric agreement alone as a professional drawing approval.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from renderer import NUMBER_LABEL, _metadata, audit, render


def validate_selection_retention(config, report):
    """Check actual saved CAD dimensions against the engineer's routed choices."""
    import ezdxf
    expected = []
    for group, selections in config.items():
        if isinstance(selections,list):
            for selection in selections:
                match=NUMBER_LABEL.fullmatch(str(selection.get("label","")).strip())
                if not match:
                    raise ValueError("Comparison dataset requires an explicit numeric dimension label")
                expected.append((selection["view"],float(match["value"])))
    actual=[]
    sides=[]
    for drawing in report["orthographic_drawings"]:
        document=ezdxf.readfile(drawing["files"]["dxf"])
        for dimension in document.modelspace().query("DIMENSION"):
            actual.append((drawing["view"],float(dimension.get_measurement())))
            meta=_metadata(dimension)
            if meta: sides.append((meta["feature_id"],meta["view"],meta["side"]))
    remaining=list(actual)
    for view,value in expected:
        index=next((i for i,(a_view,a_value) in enumerate(remaining)
                    if view==a_view and abs(value-a_value)<0.006),None)
        if index is None: raise ValueError(f"Selected dimension missing/changed: {view} {value}")
        remaining.pop(index)
    if remaining: raise ValueError("Unexpected dimensions were added")
    expected_sides=Counter((s["feature_id"],s["view"],s["side"])
                           for s in config.get("shaft_dimension_selections",[]))
    if expected_sides and expected_sides != Counter(sides):
        raise ValueError("Engineer-selected shaft feature/view/side identity changed")
    return {"passed":True,"expected_count":len(expected),"actual_count":len(actual),
            "shaft_feature_view_side_count":sum(expected_sides.values())}


def compare(case, directory):
    import ezdxf
    from PIL import Image, ImageDraw, ImageFont
    from auto_2d_drawing.pdf_exporter import export_png
    directory.mkdir(parents=True,exist_ok=True)
    config = dict(case["selection_config"])
    selections = config.pop("selections")
    report = render(Path(case["step"]),selections,directory/"v2",**config)
    quality = report["combined_multiview_drawing"]
    retention=validate_selection_retention(case["selection_config"],report)
    record = {"name":case["name"],"original_dxf":case["original_dxf"],
              "selected":report["requested_selection_count"],"configured":report["configured_selection_count"],
              "quality_passed":report["quality_passed"],
              "selection_retention":retention,
              "quality":quality,"original_comparison_status":"HUMAN_SEMANTIC_REVIEW_REQUIRED"}
    if not report["success"]:
        raise ValueError("Feature/renderer contract failed: inspect v2_report.json before continuing")
    if not report["quality_passed"]:
        (directory/"comparison.json").write_text(json.dumps(record,indent=2),encoding="utf-8")
        raise ValueError("Drawing quality gate failed: inspect comparison.json before continuing")
    original = ezdxf.readfile(case["original_dxf"])
    original_png = directory/"original.png"
    export_png(original,original.modelspace(),str(original_png),dark_bg=False)
    with Image.open(original_png) as preview:
        longest=max(preview.size)
    if longest < 1200:
        export_png(original,original.modelspace(),str(original_png),dark_bg=False,dpi=min(1200,int(150*1800/longest)))
    originals = []
    for dim in original.modelspace().query("DIMENSION"):
        value = dim.get_measurement()
        if isinstance(value,(int,float)):
            originals.append(float(value)*float(dim.override().get("dimlfac",1.0)))
    record["original_native_dimension_values"] = originals
    panels = [("Original engineering drawing", original_png)]
    if case.get("baseline_dxf"):
        baseline_path = Path(case["baseline_dxf"])
    else:
        scripts = ROOT/"experimental_lab/tolerance_extraction_audit/common/scripts"
        if str(scripts) not in sys.path: sys.path.insert(0,str(scripts))
        spec = importlib.util.spec_from_file_location("_drawing_comparison_v1",scripts/"minimal_multiview_drawing.py")
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        baseline_report = baseline.render_minimal_multiview_drawing(Path(case["step"]),selections,directory/"baseline",**config)
        if not baseline_report["success"]: raise ValueError("V1 baseline geometry validation failed")
        baseline_path = Path(baseline_report["combined_multiview_drawing"]["files"]["dxf"])
    record["baseline_quality"] = audit(baseline_path)
    baseline_png = directory/"baseline.png"
    baseline_doc = ezdxf.readfile(baseline_path)
    export_png(baseline_doc,baseline_doc.modelspace(),str(baseline_png),dark_bg=False)
    panels.extend([("Current renderer, same selections",baseline_png),
                   ("V2 experiment, same selections",Path(quality["files"]["png"]))])
    board = Image.new("RGB",(2400,1800),"#e5e5e5")
    draw = ImageDraw.Draw(board)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf",23)
    # Original at the top; current and V2 below. No raster alteration of CAD content.
    positions = [(0,0,2400,880),(0,900,1190,880),(1210,900,1190,880)]
    for (label,path),(x,y,w,h) in zip(panels,positions):
        draw.text((x+12,y+8),label,font=font,fill="#171717")
        picture = Image.open(path).convert("RGB")
        picture.thumbnail((w-24,h-48),Image.Resampling.LANCZOS)
        board.paste(picture,(x+(w-picture.width)//2,y+40+(h-48-picture.height)//2))
    board.save(directory/"comparison.png")
    record["comparison_image"] = str((directory/"comparison.png").resolve())
    (directory/"comparison.json").write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
    return record


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    cases=json.loads(args.manifest.read_text("utf-8"))["cases"]
    results=[]
    for case in cases:
        result=compare(case,args.output/case["name"])
        results.append({k:result[k] for k in ("name","selected","configured","quality_passed","comparison_image")})
        print(json.dumps(results[-1]),flush=True)
    (args.output/"summary.json").write_text(json.dumps(results,indent=2),encoding="utf-8")


if __name__=="__main__": main()
