"""
PDF/SVG 輸出模組 — 使用 ezdxf SVG 後端將 DXF 渲染為 SVG 以避免 matplotlib 崩潰
"""
import os
import shutil
import subprocess
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.addons.drawing import layout
from config import PAPER_SIZES


def export_svg(doc, msp, output_path, paper="A3", dark_bg=True):
    """
    將 DXF 渲染為 SVG。
    
    Args:
        doc: ezdxf Document
        msp: DXF modelspace
        output_path: SVG 輸出路徑
        paper: 紙張尺寸
        dark_bg: True=暗色背景(CAD風格), False=白底(列印風格)
    """
    ctx = RenderContext(doc)
    
    if dark_bg:
        ctx.current_layout_properties.set_colors('#1a1a2e')
        bg_color = '#1a1a2e'
    else:
        bg_color = '#ffffff'

    backend = SVGBackend()
    Frontend(ctx, backend).draw_layout(msp, finalize=True)
    
    # 使用 ezdxf SVGBackend 取得字串
    svg_string = backend.get_string(layout.Page(0, 0))
    
    # 寫入檔案
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(svg_string)
        
    print(f"SVG saved: {output_path}")

def export_pdf(doc, msp, output_path, paper="A3", dpi=150, dark_bg=True):
    """
    先用 ezdxf 產 SVG，再用 librsvg 的 rsvg-convert 轉成 PDF。
    若執行環境沒有 rsvg-convert，保留 SVG 作為可預覽輸出。
    """
    svg_path = output_path.replace('.pdf', '.svg')
    export_svg(doc, msp, svg_path, paper, dark_bg)

    converter = shutil.which("rsvg-convert")
    if not converter:
        print(f"PDF skipped: rsvg-convert not found. SVG kept at {svg_path}")
        return

    try:
        subprocess.run(
            [converter, "-f", "pdf", "-o", output_path, svg_path],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        print(f"PDF saved: {output_path}")
    except Exception as e:
        print(f"PDF skipped: rsvg-convert failed ({e}). SVG kept at {svg_path}")

def export_png(doc, msp, output_path, paper="A3", dpi=200, dark_bg=True):
    """暫不實作 PNG，避免 matplotlib 崩潰"""
    pass
