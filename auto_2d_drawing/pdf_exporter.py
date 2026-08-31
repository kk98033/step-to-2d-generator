"""
PDF/SVG/PNG 輸出模組 — 支援 ezdxf SVG 後端、svglib + reportlab、pypdfium2 及 aspose-cad 多重策略備援
"""
import os
import shutil
import subprocess
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.addons.drawing import layout
try:
    from config import PAPER_SIZES
except ImportError:
    from auto_2d_drawing.config import PAPER_SIZES


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
    dir_name = os.path.dirname(output_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    ctx = RenderContext(doc)
    
    if dark_bg:
        ctx.current_layout_properties.set_colors('#1a1a2e')
    
    backend = SVGBackend()
    Frontend(ctx, backend).draw_layout(msp, finalize=True)
    
    svg_string = backend.get_string(layout.Page(0, 0))
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(svg_string)
        
    print(f"SVG saved: {output_path}")
    return output_path


def export_pdf(doc, msp, output_path, paper="A3", dpi=150, dark_bg=True):
    """
    將 DXF 匯出為高品質向量 PDF 檔案。
    策略順序：
    1. 產生 SVG 後使用 svglib + reportlab 轉向量 PDF (純 Python、快速且保留向量清晰度)
    2. 使用系統 rsvg-convert 工具 (若有安裝)
    3. 使用 aspose-cad 備援渲染
    """
    dir_name = os.path.dirname(output_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    svg_path = output_path.replace('.pdf', '.svg')
    export_svg(doc, msp, svg_path, paper, dark_bg)

    # 策略 1: svglib + reportlab (純 Python 高精度向量轉換)
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPDF
        drawing = svg2rlg(svg_path)
        if drawing is not None:
            renderPDF.drawToFile(drawing, output_path)
            print(f"PDF saved (via svglib): {output_path}")
            return output_path
    except Exception as e:
        print(f"svglib export notice: {e}")

    # 策略 2: rsvg-convert 系統工具
    converter = shutil.which("rsvg-convert")
    if converter:
        try:
            subprocess.run(
                [converter, "-f", "pdf", "-o", output_path, svg_path],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            print(f"PDF saved (via rsvg-convert): {output_path}")
            return output_path
        except Exception as e:
            print(f"rsvg-convert failed: {e}")

    # 策略 3: aspose-cad 備援
    try:
        from aspose.cad import Image
        from aspose.cad.imageoptions import PdfOptions, CadRasterizationOptions
        
        dxf_path = output_path.replace('.pdf', '.dxf')
        if os.path.exists(dxf_path):
            image = Image.load(dxf_path)
            raster_opts = CadRasterizationOptions()
            raster_opts.page_width = 1684.0
            raster_opts.page_height = 1190.0
            pdf_opts = PdfOptions()
            pdf_opts.vector_rasterization_options = raster_opts
            image.save(output_path, pdf_opts)
            print(f"PDF saved (via aspose-cad): {output_path}")
            return output_path
    except Exception as e:
        print(f"aspose-cad fallback failed: {e}")

    print(f"Warning: Could not convert SVG to PDF. SVG kept at {svg_path}")
    return None


def export_png(doc, msp, output_path, paper="A3", dpi=150, dark_bg=True):
    """
    將 DXF/PDF 匯出為高解析度 PNG 預覽圖。
    策略順序：
    1. 使用 pypdfium2 將生成的向量 PDF 高速光柵化為高清晰 PNG 圖檔
    2. 使用 aspose-cad 直接由 DXF 光柵化為 PNG
    """
    dir_name = os.path.dirname(output_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    pdf_path = output_path.replace('.png', '.pdf')
    
    # 若 PDF 尚未產出，先嘗試產出 PDF
    if not os.path.exists(pdf_path):
        export_pdf(doc, msp, pdf_path, paper, dpi, dark_bg)

    # 策略 1: 使用 pypdfium2 將 PDF 高速渲染為高解析度 PNG
    if os.path.exists(pdf_path):
        try:
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(pdf_path)
            if len(pdf) > 0:
                page = pdf[0]
                scale_factor = max(1.5, dpi / 72.0)
                pil_image = page.render(scale=scale_factor).to_pil()
                pil_image.save(output_path, format="PNG")
                print(f"PNG saved (via pypdfium2): {output_path}")
                return output_path
        except Exception as e:
            print(f"pypdfium2 PNG export notice: {e}")

    # 策略 2: aspose-cad 備援
    try:
        from aspose.cad import Image
        from aspose.cad.imageoptions import PngOptions, CadRasterizationOptions
        dxf_path = output_path.replace('.png', '.dxf')
        if os.path.exists(dxf_path):
            image = Image.load(dxf_path)
            raster_opts = CadRasterizationOptions()
            raster_opts.page_width = 1684.0
            raster_opts.page_height = 1190.0
            png_opts = PngOptions()
            png_opts.vector_rasterization_options = raster_opts
            image.save(output_path, png_opts)
            print(f"PNG saved (via aspose-cad): {output_path}")
            return output_path
    except Exception as e:
        print(f"aspose-cad PNG fallback failed: {e}")

    return None
