import sys

def log(msg):
    with open('crash_test.log', 'a', encoding='utf-8') as f:
        f.write(msg + '\n')
    print(msg)

log("Starting...")

try:
    log("Importing ezdxf...")
    import ezdxf
    log("Importing matplotlib...")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    log("Importing ezdxf rendering addons...")
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    
    dxf_path = r"f:\School\力致\app\step-to-2d-generator\auto_2d_drawing\reference\example_output\00_ref\給成大資料\FAN\AJ0A-軸流扇\BOM及2D圖面\0AJ0A00009-R03.dxf"
    pdf_path = r"f:\School\力致\app\step-to-2d-generator\auto_2d_drawing\reference\example_output\test_0AJ0A00009-R03.pdf"
    
    log("Reading DXF...")
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    
    log("Setting up Matplotlib figure...")
    fig = plt.figure()
    ax = fig.add_axes([0, 0, 1, 1])
    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax)
    
    log("Drawing layout...")
    Frontend(ctx, out).draw_layout(msp, finalize=True)
    
    log("Saving PDF...")
    fig.savefig(pdf_path, dpi=150, bbox_inches="tight")
    log("Closing figure...")
    plt.close(fig)
    log("Success!")
except Exception as e:
    import traceback
    log(f"Exception: {e}")
    log(traceback.format_exc())

log("Done.")
