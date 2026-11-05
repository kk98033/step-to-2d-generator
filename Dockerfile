FROM node:22-bookworm-slim AS frontend-build

WORKDIR /frontend
COPY web_app/frontend/package.json web_app/frontend/package-lock.json ./
RUN npm ci
COPY web_app/frontend/ ./
RUN npm run build


FROM condaforge/miniforge3:latest AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    fonts-noto-cjk \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY environment.docker.yml /tmp/environment.yml
RUN conda env create -f /tmp/environment.yml \
    && conda clean -afy \
    && rm /tmp/environment.yml

ENV PATH="/opt/conda/envs/pyoccenv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    CAD_MODELS_DIR=/data/models \
    CAD_OUTPUT_DIR=/data/output \
    CAD_REFERENCE_DIR=/data/reference \
    CAD_TEMPLATES_DIR=/data/templates \
    CAD_TOLERANCE_CASE_DB=/data/tolerance/feature_case_base.json \
    CAD_NEW_EXAMPLE_DIR=/data/company-reference \
    CAD_TOLERANCE_DXF_DIR=/data/company-reference/temp_dxf_cache_ref \
    CAD_CN_FONT_PATH=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc

COPY auto_2d_drawing/ /app/auto_2d_drawing/
COPY web_app/backend/ /app/web_app/backend/
COPY tools/ /app/tools/
COPY experimental_lab/tolerance_extraction_audit/common/scripts/ /app/experimental_lab/tolerance_extraction_audit/common/scripts/
COPY experimental_lab/tolerance_extraction_audit/DRAWING_CURRENT_FINAL/ /app/experimental_lab/tolerance_extraction_audit/DRAWING_CURRENT_FINAL/
COPY docs/generated/api_graph.json /app/docs/generated/api_graph.json
COPY --from=frontend-build /frontend/dist /app/web_app/frontend/dist

RUN mkdir -p /data/models /data/output /data/reference /data/company-reference /data/templates /data/tolerance \
    && cp -a /app/auto_2d_drawing/templates/. /data/templates/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl --fail http://localhost:8000/api/health || exit 1

CMD ["python", "-m", "uvicorn", "web_app.backend.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
