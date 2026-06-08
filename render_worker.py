import sys
import os

try:
    import ezdxf
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.svg import SVGBackend

    dxf_path = sys.argv[1]
    svg_path = sys.argv[2]
    
    print(f"Reading {dxf_path}...")
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    
    print("Setting up SVG Backend...")
    ctx = RenderContext(doc)
    out = SVGBackend()
    
    print("Drawing...")
    Frontend(ctx, out).draw_layout(msp, finalize=True)
    
    print("Saving SVG...")
    from ezdxf.addons.drawing import layout
    svg_string = out.get_string(layout.Page(0, 0))
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg_string)
        
    print("OK")
    sys.exit(0)
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
