# ==============================================================================
# FORCECON STEP-to-2D CAD Generator - Dockerfile
# Base: Conda-forge Python 3.10 with OpenCASCADE PythonOCC 7.7.2
# ==============================================================================
FROM condaforge/miniforge3:latest

WORKDIR /app

# 安裝系統圖形與字型相依套件 (OpenGL, OSMesa, X11, CJK Fonts)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libxrender1 \
    libxext6 \
    fonts-noto-cjk \
    fonts-wqy-microhei \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 建立 Python 3.10 環境並安裝 PythonOCC
RUN conda create -n occenv -c conda-forge python=3.10 pythonocc-core=7.7.2 -y \
    && conda clean -afy

# 複製依賴檔案
COPY requirements.txt .

# 在 Conda 環境中安裝 Python 模組
RUN /opt/conda/envs/occenv/bin/pip install --no-cache-dir -r requirements.txt \
    fastapi uvicorn ezdxf svglib pypdfium2 shapely trimesh reportlab cairosvg

# 複製專案程式碼
COPY . /app/

# 設定環境變數
ENV PATH="/opt/conda/envs/occenv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

# 預設啟動 FastAPI 後端伺服器 (包含已編譯之靜態前端)
WORKDIR /app/web_app/backend
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
