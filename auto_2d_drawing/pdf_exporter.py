"""
PDF 輸出模組 — 使用 ezdxf matplotlib 後端將 DXF 渲染為 PDF
"""
import os
import matplotlib
import matplotlib.pyplot as plt
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from config import CN_FONT_PATH, CN_FONT_NAME, PAPER_SIZES


def setup_matplotlib():
    """設定 matplotlib 中文字型與繪圖參數"""
    matplotlib.use('Agg')  # 非互動式後端
    if os.path.exists(CN_FONT_PATH):
        matplotlib.font_manager.fontManager.addfont(CN_FONT_PATH)
        matplotlib.rcParams['font.family'] = CN_FONT_NAME
    matplotlib.rcParams['axes.unicode_minus'] = False


def export_pdf(doc, msp, output_path, paper="A3", dpi=150, dark_bg=True):
    """
    將 DXF 渲染為 PDF。
    
    Args:
        doc: ezdxf Document
        msp: DXF modelspace
        output_path: PDF 輸出路徑
        paper: 紙張尺寸 ('A3', 'A4')
        dpi: 解析度
        dark_bg: True=暗色背景(CAD風格), False=白底(列印風格)
    """
    setup_matplotlib()

    pw, ph = PAPER_SIZES[paper]
    fig = plt.figure(figsize=(pw / 25.4, ph / 25.4), dpi=dpi)
    ax = fig.add_axes([0.01, 0.01, 0.98, 0.98])
    ax.set_aspect('equal')

    if dark_bg:
        fig.patch.set_facecolor('#1a1a2e')
        ax.set_facecolor('#1a1a2e')

    ctx = RenderContext(doc)

    if dark_bg:
        # 覆蓋背景色為暗色 (CAD 風格)
        ctx.current_layout_properties.set_colors('#1a1a2e')

    backend = MatplotlibBackend(ax)
    Frontend(ctx, backend).draw_layout(msp, finalize=True)

    fig.savefig(output_path, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"PDF saved: {output_path}")


def export_png(doc, msp, output_path, paper="A3", dpi=200, dark_bg=True):
    """
    將 DXF 渲染為 PNG 圖片。
    """
    setup_matplotlib()

    pw, ph = PAPER_SIZES[paper]
    fig = plt.figure(figsize=(pw / 25.4, ph / 25.4), dpi=dpi)
    ax = fig.add_axes([0.01, 0.01, 0.98, 0.98])
    ax.set_aspect('equal')

    if dark_bg:
        fig.patch.set_facecolor('#1a1a2e')
        ax.set_facecolor('#1a1a2e')

    ctx = RenderContext(doc)
    if dark_bg:
        ctx.current_layout_properties.set_colors('#1a1a2e')

    backend = MatplotlibBackend(ax)
    Frontend(ctx, backend).draw_layout(msp, finalize=True)

    fig.savefig(output_path, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"PNG saved: {output_path}")
