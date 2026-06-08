import traceback
try:
    import ezdxf
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    doc = ezdxf.readfile(r'f:\School\力致\app\step-to-2d-generator\auto_2d_drawing\reference\example_output\00_ref\給成大資料\FAN\AJ0A-軸流扇\BOM及2D圖面\0AJ0A00009-R03.dxf')
    msp = doc.modelspace()
    fig = plt.figure()
    ax = fig.add_axes([0, 0, 1, 1])
    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax)
    Frontend(ctx, out).draw_layout(msp, finalize=True)
    fig.savefig('test_output.pdf')
    print('SUCCESS')
except Exception as e:
    with open('error_log.txt', 'w', encoding='utf-8') as f:
        traceback.print_exc(file=f)
    print('FAILED')
