import sys

def log(msg):
    with open('crash_test_svg.log', 'a', encoding='utf-8') as f:
        f.write(msg + '\n')
    print(msg)

log("Starting...")

try:
    log("Importing ezdxf...")
    import ezdxf
    log("Importing SVG backend...")
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.svg import SVGBackend
    
    dxf_path = r"f:\School\力致\app\step-to-2d-generator\auto_2d_drawing\reference\example_output\00_ref\給成大資料\FAN\AJ0A-軸流扇\BOM及2D圖面\0AJ0A00009-R03.dxf"
    svg_path = r"f:\School\力致\app\step-to-2d-generator\auto_2d_drawing\reference\example_output\test_0AJ0A00009-R03.svg"
    
    log("Reading DXF...")
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    
    log("Setting up SVG...")
    ctx = RenderContext(doc)
    out = SVGBackend()
    
    log("Drawing layout...")
    Frontend(ctx, out).draw_layout(msp, finalize=True)
    
    log("Getting SVG string...")
    svg_string = out.get_string(out.get_xml_root())
    
    log("Writing to file...")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg_string)
        
    log("Success!")
except Exception as e:
    import traceback
    log(f"Exception: {e}")
    log(traceback.format_exc())

log("Done.")
