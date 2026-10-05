import os
import sys
import uuid
import json
import shutil
import secrets
from math import isfinite
from typing import Dict, Any, List, Optional, Literal
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, Body, HTTPException, Header
from fastapi.responses import Response, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

# Add parent dir to path so we can import original modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from auto_2d_drawing.config import MODELS_DIR, OUTPUT_DIR
from auto_2d_drawing.batch_generate import batch_generate

app = FastAPI(
    title="FORCECON STEP-to-2D API",
    version="0.4.0-dev",
    description=(
        "STEP/STP 轉工程圖、3D 特徵查找、智慧標註與開發中的 CAD-RAG 公差推薦 API。"
        "整合方請以 /docs、/openapi.json 與 docs/api_reference.md 為契約入口。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_header(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Serve output files statically
os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/api/files", StaticFiles(directory=OUTPUT_DIR), name="files")

jobs = {}
executor = ThreadPoolExecutor(max_workers=2)


def _api_file_url(output_dir_name: str, *parts: str) -> str:
    return "/api/files/" + "/".join([output_dir_name, *parts])


def _add_if_exists(entry: dict, key: str, output_dir_name: str, output_dir: str, *parts: str):
    path = os.path.join(output_dir, *parts)
    if os.path.exists(path):
        entry[key] = _api_file_url(output_dir_name, *parts)


def _build_output_entry(output_dir_name: str, output_dir: str, base: str, stl_name: str) -> dict:
    entry = {}
    _add_if_exists(entry, "png", output_dir_name, output_dir, f"{base}.png")
    _add_if_exists(entry, "pdf", output_dir_name, output_dir, f"{base}.pdf")
    _add_if_exists(entry, "svg", output_dir_name, output_dir, f"{base}.svg")
    _add_if_exists(entry, "dxf", output_dir_name, output_dir, f"{base}.dxf")
    _add_if_exists(entry, "stl", output_dir_name, output_dir, "_parts", stl_name)
    _add_if_exists(entry, "front_pdf", output_dir_name, output_dir, f"{base}_front.pdf")
    _add_if_exists(entry, "back_pdf", output_dir_name, output_dir, f"{base}_back.pdf")
    _add_if_exists(entry, "top_pdf", output_dir_name, output_dir, f"{base}_top.pdf")
    _add_if_exists(entry, "right_pdf", output_dir_name, output_dir, f"{base}_right.pdf")
    _add_if_exists(entry, "left_pdf", output_dir_name, output_dir, f"{base}_left.pdf")
    _add_if_exists(entry, "front_svg", output_dir_name, output_dir, f"{base}_front.svg")
    _add_if_exists(entry, "back_svg", output_dir_name, output_dir, f"{base}_back.svg")
    _add_if_exists(entry, "top_svg", output_dir_name, output_dir, f"{base}_top.svg")
    _add_if_exists(entry, "right_svg", output_dir_name, output_dir, f"{base}_right.svg")
    _add_if_exists(entry, "left_svg", output_dir_name, output_dir, f"{base}_left.svg")
    _add_if_exists(entry, "features_pdf", output_dir_name, output_dir, f"{base}_features_view.pdf")
    _add_if_exists(entry, "features_svg", output_dir_name, output_dir, f"{base}_features_view.svg")
    _add_if_exists(entry, "features_json", output_dir_name, output_dir, f"{base}_feature_records.json")
    return entry


def build_parts_map(output_dir: str, output_dir_name: str) -> dict:
    """Build frontend file links for generated assembly and part drawings."""
    parts_map = {}
    parts_dir = os.path.join(output_dir, "_parts")
    part_prefixes = []
    if os.path.exists(parts_dir):
        part_prefixes = [
            os.path.splitext(filename)[0]
            for filename in os.listdir(parts_dir)
            if filename.lower().endswith(".stp") and filename != "_full_assembly.stp"
        ]

    for filename in os.listdir(output_dir):
        lower_name = filename.lower()
        if not lower_name.endswith((".png", ".pdf", ".svg", ".dxf")):
            continue

        base = os.path.splitext(filename)[0]
        if base.endswith(("_front", "_back", "_top", "_right", "_left", "_features_view")):
            continue

        if base.endswith("_assembly"):
            parts_map["_full_assembly"] = _build_output_entry(
                output_dir_name, output_dir, base, "_full_assembly.stl"
            )
            continue

        for part_prefix in part_prefixes:
            if base.endswith(f"_{part_prefix}"):
                parts_map[part_prefix] = _build_output_entry(
                    output_dir_name, output_dir, base, f"{part_prefix}.stl"
                )
                break

    return parts_map


def _safe_output_dir(model_id: str) -> str:
    if not model_id or model_id != os.path.basename(model_id):
        raise HTTPException(status_code=400, detail="Invalid model_id")

    output_dir = os.path.abspath(os.path.join(OUTPUT_DIR, model_id))
    output_root = os.path.abspath(OUTPUT_DIR)
    if os.path.commonpath([output_root, output_dir]) != output_root:
        raise HTTPException(status_code=400, detail="Invalid model_id")
    if not os.path.isdir(output_dir):
        raise HTTPException(status_code=404, detail="Model output not found")
    return output_dir


def _safe_part_id(part_id: str) -> str:
    if not part_id or part_id != os.path.basename(part_id):
        raise HTTPException(status_code=400, detail="Invalid part_id")
    return part_id


def _view_urls(entry: dict) -> dict:
    views = {}
    for view_name in ("front", "back", "top", "right", "left"):
        view_entry = {}
        for ext in ("pdf", "svg"):
            key = f"{view_name}_{ext}"
            if key in entry:
                view_entry[ext] = entry[key]
        if view_entry:
            views[view_name] = view_entry
    return views


def _annotation_path(output_dir: str, part_id: str) -> str:
    return os.path.join(output_dir, "_annotations", f"{part_id}_annotations.json")


from auto_2d_drawing.step_reader import load_step
from auto_2d_drawing.feature_extractor import FeatureExtractor
from auto_2d_drawing.feature_layer import build_feature_records
from auto_2d_drawing.smart_annotation_engine import TemplateManager, SmartAnnotationEngine
from auto_2d_drawing.view_projector import ViewProjector
from auto_2d_drawing.tolerance.tolerance_decision_service import ToleranceDecisionService
from auto_2d_drawing.tolerance.iso_tolerance_table import lookup_iso_fit_deviation
from auto_2d_drawing.tolerance.case_base import FeatureCaseBase, ToleranceCase

template_manager = TemplateManager()
case_base = FeatureCaseBase()
tolerance_service = ToleranceDecisionService(case_base=case_base)


class ExternalTolerancePrediction(BaseModel):
    """One independently produced neural-model prediction for a drawing rule."""

    rule_id: str = Field(min_length=1)
    feature_id: Optional[str] = None
    predicted_mode: Literal[
        "FIT", "CUSTOM_SYMMETRIC", "CUSTOM_LIMITS", "GROOVE", "NONE"
    ] = "NONE"
    tolerance_config: Dict[str, Any] = Field(default_factory=dict)
    formatted_display: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: List[str] = Field(default_factory=list)
    input_features: Dict[str, Any] = Field(default_factory=dict)
    uncertainty: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tolerance_config(self):
        config = self.tolerance_config
        config_mode = config.get("mode") if config else None
        if self.predicted_mode == "NONE":
            if config_mode not in (None, "NONE"):
                raise ValueError("predicted_mode NONE requires tolerance_config.mode NONE")
            return self
        if not config:
            raise ValueError("tolerance_config is required when predicted_mode is not NONE")
        if config_mode != self.predicted_mode:
            raise ValueError("tolerance_config.mode must match predicted_mode")
        if self.predicted_mode == "FIT" and not str(config.get("fit_class") or "").strip():
            raise ValueError("FIT requires tolerance_config.fit_class")
        if self.predicted_mode == "CUSTOM_SYMMETRIC":
            dev = config.get("dev")
            if type(dev) not in (int, float) or not isfinite(float(dev)) or dev < 0:
                raise ValueError("CUSTOM_SYMMETRIC requires a non-negative numeric dev")
        if self.predicted_mode in {"CUSTOM_LIMITS", "GROOVE"}:
            upper = config.get("upper_dev")
            lower = config.get("lower_dev")
            if (
                type(upper) not in (int, float)
                or type(lower) not in (int, float)
                or not isfinite(float(upper))
                or not isfinite(float(lower))
            ):
                raise ValueError(f"{self.predicted_mode} requires numeric upper_dev and lower_dev")
            if lower > upper:
                raise ValueError("lower_dev cannot exceed upper_dev")
        return self


class ExternalTolerancePredictionSet(BaseModel):
    """Versioned prediction batch submitted by an external neural model."""

    schema_version: Literal["1.0"] = "1.0"
    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    model_artifact_id: Optional[str] = None
    request_id: Optional[str] = None
    training_data_scope: Optional[str] = None
    predictions: List[ExternalTolerancePrediction] = Field(min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExternalPredictionWriteResponse(BaseModel):
    status: Literal["ok"]
    model_id: str
    part_id: str
    source_type: Literal["EXTERNAL_NEURAL_MODEL"]
    provider: str
    model_name: str
    model_version: str
    prediction_count: int
    received_at_utc: datetime


class ExternalPredictionReadResponse(ExternalTolerancePredictionSet):
    status: Literal["ok"]
    source_type: Literal["EXTERNAL_NEURAL_MODEL"]
    model_id: str
    part_id: str
    received_at_utc: datetime
    predictions_by_rule: Dict[str, ExternalTolerancePrediction]


class ExternalPredictionDeleteResponse(BaseModel):
    status: Literal["ok"]
    model_id: str
    part_id: str


def _external_prediction_path(output_dir: str, part_id: str) -> str:
    directory = os.path.join(output_dir, "_external_tolerance_predictions")
    return os.path.join(directory, f"{part_id}.json")


def _load_external_prediction_set(output_dir: str, part_id: str) -> Optional[Dict[str, Any]]:
    path = _external_prediction_path(output_dir, part_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    predictions = payload.get("predictions") or []
    payload["predictions_by_rule"] = {
        item["rule_id"]: item for item in predictions if item.get("rule_id")
    }
    return payload


def _authorize_external_prediction_api(x_api_key: Optional[str]) -> None:
    configured_key = os.environ.get("CAD_EXTERNAL_PREDICTION_API_KEY", "")
    provided_key = x_api_key if isinstance(x_api_key, str) else ""
    if configured_key and not secrets.compare_digest(provided_key, configured_key):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

from auto_2d_drawing.tolerance.dxf_tolerance_extractor import DxfToleranceExtractor
from auto_2d_drawing.tolerance.feature_inference_2d import FeatureInference2DEngine
import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.addons.drawing.layout import Page

dxf_extractor = DxfToleranceExtractor()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOLERANCE_DXF_DIR = os.path.abspath(os.environ.get(
    "CAD_TOLERANCE_DXF_DIR",
    r"D:\School\力致\力致_ref\temp_dxf_cache_ref",
))

DWG_INDEX_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "auto_2d_drawing", "tolerance", "data", "dwg_file_index.json"
)
SVG_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "auto_2d_drawing", "tolerance", "data", "svg_cache"
)
PDF_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "auto_2d_drawing", "tolerance", "data", "pdf_cache"
)
os.makedirs(SVG_CACHE_DIR, exist_ok=True)
os.makedirs(PDF_CACHE_DIR, exist_ok=True)

dwg_index = {}
if os.path.exists(DWG_INDEX_PATH):
    try:
        with open(DWG_INDEX_PATH, "r", encoding="utf-8") as f:
            dwg_index = json.load(f)
    except Exception as e:
        print(f"Failed to load dwg_file_index: {e}")

def lookup_drawing_paths(name: str):
    clean = os.path.splitext(name)[0].upper()
    indexed = None
    if clean in dwg_index:
        indexed = dict(dwg_index[clean])
    else:
        for k, v in dwg_index.items():
            if clean == k or clean in k or k in clean:
                indexed = dict(v)
                break

    resolved = indexed or {}
    mounted_dxf = os.path.join(TOLERANCE_DXF_DIR, f"{os.path.splitext(name)[0]}.dxf")
    if os.path.exists(mounted_dxf):
        resolved["dxf"] = mounted_dxf
    return resolved or None

def _add_tolerance_highlight(doc, msp, entity_handle: Optional[str]):
    if not entity_handle:
        return
    entity = doc.entitydb.get(entity_handle)
    if entity is None:
        return
    point = getattr(entity.dxf, "text_midpoint", None) or getattr(entity.dxf, "insert", None)
    if point is None:
        point = getattr(entity.dxf, "defpoint", None)
    if point is None:
        return
    try:
        from ezdxf import bbox as ezdxf_bbox
        ext = ezdxf_bbox.extents(msp)
        size = ext.size
        marker_size = max(float(size.x), float(size.y), 1.0) * 0.018
    except Exception:
        marker_size = 5.0
    msp.add_circle((point.x, point.y), marker_size, dxfattribs={"color": 1, "lineweight": 70})
    marker = msp.add_mtext(
        f"TOL [{entity_handle}]",
        dxfattribs={"char_height": marker_size * 0.55, "color": 1},
    )
    marker.set_location((point.x + marker_size, point.y + marker_size))


def render_dxf_to_svg_cached(
    dxf_path: str,
    model_name: str,
    highlight_handle: Optional[str] = None,
) -> Optional[str]:
    if not dxf_path or not os.path.exists(dxf_path):
        return None
    cache_file = os.path.join(SVG_CACHE_DIR, f"{model_name}.svg")
    if not highlight_handle and os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    try:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        _add_tolerance_highlight(doc, msp, highlight_handle)
        ctx = RenderContext(doc)
        backend = SVGBackend()
        frontend = Frontend(ctx, backend)
        frontend.draw_layout(msp)
        page = Page.from_dxf_layout(msp)
        svg_str = backend.get_string(page)
        if not highlight_handle:
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(svg_str)
        return svg_str
    except Exception as e:
        print(f"Error rendering DXF {dxf_path} to SVG: {e}")
        return None


def render_dxf_to_pdf_cached(dxf_path: str, model_name: str) -> Optional[str]:
    if not dxf_path or not os.path.exists(dxf_path):
        return None
    cache_file = os.path.join(PDF_CACHE_DIR, f"{model_name}.pdf")
    if os.path.exists(cache_file):
        return cache_file
    try:
        # Matplotlib's DXF renderer can hang indefinitely on some large company
        # drawings.  Reuse the already-tested SVG renderer and convert the
        # vector output with svglib/reportlab instead.
        svg_file = os.path.join(SVG_CACHE_DIR, f"{model_name}.svg")
        if not os.path.exists(svg_file):
            if not render_dxf_to_svg_cached(dxf_path, model_name):
                return None
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPDF
        drawing = svg2rlg(svg_file)
        if drawing is None:
            return None
        renderPDF.drawToFile(drawing, cache_file)
        return cache_file if os.path.exists(cache_file) else None
    except Exception as e:
        print(f"Error rendering PDF {dxf_path}: {e}")
        return None


@app.get("/api/tolerance/stats")
def get_tolerance_stats():
    """取得歷史特徵案例庫統計資料"""
    try:
        from collections import Counter
        total_cases = len(case_base.cases)
        roles = Counter()
        series_cnt = Counter()
        drawings = set()
        verification = Counter()
        feature_linkage = Counter()

        for c in case_base.cases:
            roles[c.inferred_role] += 1
            verification[c.effective_verification_status()] += 1
            metadata = c.source_metadata or {}
            feature_linkage[
                "FEATURE_LINKED" if metadata.get("feature_identity_verified") else "UNRESOLVED_FEATURE"
            ] += 1
            src = c.evidence_source or "UNKNOWN"
            drawings.add(src)
            family = (
                (c.source_metadata or {}).get("product_family")
                or FeatureCaseBase.infer_product_family(
                    (c.source_metadata or {}).get("drawing_file") or c.evidence_source
                )
            )
            if family:
                series_cnt[family] += 1

        return {
            "status": "ok",
            "total_cases": total_cases,
            "total_drawings": len(drawings),
            "roles": dict(roles.most_common()),
            "verification": dict(verification.most_common()),
            "feature_linkage": dict(feature_linkage),
            "retrieval_eligible_cases": sum(1 for c in case_base.cases if c.is_retrieval_eligible()),
            "verified_extraction_cases": sum(1 for c in case_base.cases if c.is_verified_extraction()),
            "top_series": dict(series_cnt.most_common(20))
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tolerance/cases")
def list_tolerance_cases(
    category: Optional[str] = None,
    quality: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 36,
    group_by_drawing: bool = False,
):
    """
    取得歷史特徵案例庫詳細案例清單，支援分頁、分類篩選與關鍵字搜尋
    """
    try:
        case_list = []
        for c in case_base.cases:
            if category and category.upper() != "ALL":
                cat_u = category.upper()
                r_u = c.inferred_role.upper()
                f_u = c.feature_type.upper()
                p_u = c.part_type.upper()

                if cat_u == "BEARING_FIT":
                    if not any(k in r_u for k in ("BEARING", "CYLINDER", "PIN", "JOURNAL")):
                        continue
                elif cat_u == "GROOVE_CHAMFER":
                    if not any(k in r_u for k in ("GROOVE", "PILOT", "FILLET", "ROUND", "CHAMFER")):
                        continue
                elif cat_u == "OVERALL_LENGTH":
                    if not any(k in r_u for k in ("OVERALL", "SHOULDER", "LENGTH")):
                        continue
                elif cat_u == "PRESS_FIT":
                    if not any(k in r_u for k in ("PRESS", "HUB", "BORE")):
                        continue
                elif cat_u == "GENERAL_LINEAR":
                    if "GENERAL" not in r_u and "LINEAR" not in r_u:
                        continue
                else:
                    if cat_u not in (p_u, r_u, f_u):
                        continue

            quality_u = str(quality or "ALL").strip().upper()
            verified_extraction = c.is_verified_extraction()
            if quality_u in {"VERIFIED", "CORRECT", "VERIFIED_EXTRACTION"}:
                if not verified_extraction:
                    continue
            elif quality_u in {"UNVERIFIED", "NEEDS_REVIEW"}:
                if verified_extraction:
                    continue

            d = c.to_dict()
            d["verification_status"] = c.effective_verification_status()
            d["retrieval_eligible"] = c.is_retrieval_eligible()
            d["feature_identity_verified"] = bool((c.source_metadata or {}).get("feature_identity_verified"))
            d["verified_extraction"] = verified_extraction
            d["candidate_feature_types"] = list((c.source_metadata or {}).get("candidate_feature_types") or [])
            d["feature_inference_2d"] = dict((c.source_metadata or {}).get("feature_inference_2d") or {})

            t_cfg = c.tolerance_config or {}
            mode = t_cfg.get("mode", "FIT")
            fit_cls = t_cfg.get("fit_class")
            u_dev = t_cfg.get("upper_dev", 0.0)
            l_dev = t_cfg.get("lower_dev", 0.0)
            dev = t_cfg.get("dev", 0.05)
            nom_dims = c.nominal_dimensions or {}
            dia = nom_dims.get("diameter", nom_dims.get("groove_diameter", 0.0))
            length = nom_dims.get("length", nom_dims.get("groove_width", 0.0))

            if mode == "FIT" and fit_cls:
                if u_dev == 0.0 and l_dev == 0.0:
                    u_dev, l_dev = lookup_iso_fit_deviation(dia if dia > 0 else 3.0, fit_cls, is_hole=t_cfg.get("is_hole", False))
                fmt_tol = f"{fit_cls} ({u_dev:+.3f} / {l_dev:+.3f} mm)"
            elif mode == "GROOVE":
                fmt_tol = f"(+{u_dev:.3f} / {l_dev:.3f} mm)"
            elif mode == "CUSTOM_SYMMETRIC":
                fmt_tol = f"±{dev:.2f} mm"
            elif mode == "CUSTOM_LIMITS":
                fmt_tol = f"(+{u_dev:.3f} / {l_dev:.3f} mm)"
            else:
                fmt_tol = "未注公差 (ISO 2768-m)"

            d["formatted_tolerance"] = fmt_tol

            src = c.evidence_source or "UNKNOWN"
            model_name = src
            if model_name.endswith(".dxf") or model_name.endswith(".stp") or model_name.endswith(".step"):
                model_name = os.path.splitext(model_name)[0]
            d["model_name"] = model_name

            dim_summary_parts = []
            if dia > 0:
                dim_summary_parts.append(f"Φ{dia:.2f} mm")
            if length > 0:
                dim_summary_parts.append(f"長度 {length:.2f} mm")
            for k_dim, v_dim in nom_dims.items():
                if k_dim not in ("diameter", "groove_diameter", "length", "groove_width", "radius"):
                    dim_summary_parts.append(f"{k_dim}: {v_dim}")
            d["dim_summary"] = " × ".join(dim_summary_parts) if dim_summary_parts else "一般幾何"

            prev_url = None
            if os.path.exists(OUTPUT_DIR):
                m_clean = model_name.lower().replace("-", "").replace("_", "")
                for out_name in os.listdir(OUTPUT_DIR):
                    o_clean = out_name.lower().replace("-", "").replace("_", "")
                    if (m_clean in o_clean or o_clean in m_clean) and len(m_clean) > 3:
                        out_folder = os.path.join(OUTPUT_DIR, out_name)
                        if os.path.isdir(out_folder):
                            # Try png first
                            for p_f in os.listdir(out_folder):
                                if p_f.lower().endswith(".png") and not p_f.startswith("."):
                                    prev_url = f"/api/files/{out_name}/{p_f}"
                                    break
                            # Then try svg
                            if not prev_url:
                                for p_f in os.listdir(out_folder):
                                    if p_f.lower().endswith(".svg") and not p_f.startswith("."):
                                        prev_url = f"/api/files/{out_name}/{p_f}"
                                        break
                            if prev_url:
                                break

            paths_info = lookup_drawing_paths(model_name)
            has_dwg = bool(paths_info and paths_info.get("dwg"))
            has_dxf = bool(paths_info and paths_info.get("dxf"))
            d["has_dwg"] = has_dwg
            d["has_dxf"] = has_dxf
            if not prev_url and has_dxf:
                prev_url = f"/api/tolerance/drawing-svg/{model_name}"
            d["preview_image_url"] = prev_url
            d["svg_url"] = f"/api/tolerance/drawing-svg/{model_name}" if has_dxf else prev_url
            d["pdf_url"] = f"/api/tolerance/drawing-pdf/{model_name}" if has_dxf else None
            d["details_url"] = f"/api/tolerance/drawing-details/{model_name}"

            if search:
                s_lower = search.lower().strip()
                text_blob = f"{c.case_id} {model_name} {c.part_type} {c.feature_type} {c.inferred_role} {fmt_tol} {d['dim_summary']} {c.description} {c.evidence_source}".lower()
                if s_lower not in text_blob:
                    continue

            case_list.append(d)

        evidence_count = len(case_list)
        if group_by_drawing:
            grouped = {}
            for item in case_list:
                key = item["model_name"].lower()
                if key not in grouped:
                    representative = dict(item)
                    representative["drawing_case_count"] = 0
                    representative["drawing_verified_count"] = 0
                    representative["drawing_eligible_count"] = 0
                    representative["drawing_verified_extraction_count"] = 0
                    representative["drawing_feature_types"] = []
                    grouped[key] = representative
                aggregate = grouped[key]
                aggregate["drawing_case_count"] += 1
                aggregate["drawing_verified_count"] += int(bool(item.get("feature_identity_verified")))
                aggregate["drawing_eligible_count"] += int(bool(item.get("retrieval_eligible")))
                aggregate["drawing_verified_extraction_count"] += int(bool(item.get("verified_extraction")))
                feature_types = aggregate["drawing_feature_types"]
                for feature_type in ([item.get("feature_type")] if item.get("feature_identity_verified") else item.get("candidate_feature_types", [])):
                    if feature_type and feature_type not in feature_types:
                        feature_types.append(feature_type)
            case_list = list(grouped.values())

        total_filtered = len(case_list)
        import math
        if page_size > 0:
            total_pages = math.ceil(total_filtered / page_size) if total_filtered > 0 else 1
            curr_page = max(1, min(page, total_pages))
            start_idx = (curr_page - 1) * page_size
            end_idx = start_idx + page_size
            paged_cases = case_list[start_idx:end_idx]
        else:
            total_pages = 1
            curr_page = 1
            paged_cases = case_list

        return {
            "status": "ok",
            "total_count": total_filtered,
            "evidence_count": evidence_count,
            "all_cases_count": len(case_base.cases),
            "group_by_drawing": group_by_drawing,
            "page": curr_page,
            "page_size": page_size,
            "total_pages": total_pages,
            "cases": paged_cases
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tolerance/drawing-svg/{model_name}")
def get_drawing_svg(model_name: str, highlight: Optional[str] = None):
    """
    動態生成或讀取 DXF 轉出的高解析向量 SVG 圖面
    """
    clean_name = os.path.splitext(model_name)[0]
    paths = lookup_drawing_paths(clean_name)
    dxf_path = paths.get("dxf") if paths else None

    if not dxf_path or not os.path.exists(dxf_path):
        candidate = os.path.join(r"D:\School\力致\力致_ref\temp_dxf_cache_ref", f"{clean_name}.dxf")
        if os.path.exists(candidate):
            dxf_path = candidate

    if not dxf_path or not os.path.exists(dxf_path):
        raise HTTPException(status_code=404, detail=f"DXF file for {model_name} not found")

    svg_str = render_dxf_to_svg_cached(dxf_path, clean_name, highlight_handle=highlight)
    if not svg_str:
        raise HTTPException(status_code=500, detail="Failed to render DXF to SVG")

    return Response(content=svg_str, media_type="image/svg+xml")


@app.get("/api/tolerance/drawing-pdf/{model_name}")
def get_drawing_pdf(model_name: str):
    """將來源 DXF 轉成可縮放的 PDF，供 Inspector 點擊縮圖後檢視。"""
    clean_name = os.path.splitext(model_name)[0]
    paths = lookup_drawing_paths(clean_name) or {}
    dxf_path = paths.get("dxf")
    if not dxf_path or not os.path.exists(dxf_path):
        candidate = os.path.join(r"D:\School\力致\力致_ref\temp_dxf_cache_ref", f"{clean_name}.dxf")
        if os.path.exists(candidate):
            dxf_path = candidate
    if not dxf_path or not os.path.exists(dxf_path):
        raise HTTPException(status_code=404, detail=f"DXF file for {model_name} not found")
    pdf_path = render_dxf_to_pdf_cached(dxf_path, clean_name)
    if not pdf_path:
        raise HTTPException(status_code=500, detail="Failed to render DXF to PDF")
    # Do not pass ``filename=`` here: Starlette treats it as an attachment and
    # browsers download the file instead of rendering it inside the iframe.
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{clean_name}.pdf"'},
    )


@app.get("/api/tolerance/drawing-details/{model_name}")
def get_drawing_details(model_name: str):
    """
    取得該 DWG/DXF 圖紙的完整資訊，包含所有讀取到的尺寸標註與公差細節
    """
    clean_name = os.path.splitext(model_name)[0]
    normalized_name = clean_name.lower().replace("-", "").replace("_", "")
    case_evidence_by_handle = {}
    for case in case_base.cases:
        metadata = case.source_metadata or {}
        drawing_name = os.path.splitext(
            str(metadata.get("drawing_file") or case.evidence_source or "")
        )[0]
        if drawing_name.lower().replace("-", "").replace("_", "") != normalized_name:
            continue
        handle = str(metadata.get("entity_handle") or "")
        if not handle:
            continue
        case_evidence_by_handle[handle] = {
            "case_id": case.case_id,
            "verification_status": case.effective_verification_status(),
            "retrieval_eligible": case.is_retrieval_eligible(),
            "matched_feature_id": metadata.get("matched_feature_id"),
            "matched_feature_type": case.feature_type,
            "matched_nominal_field": metadata.get("matched_nominal_field"),
            "pair_method": metadata.get("pair_method"),
            "component_name": metadata.get("component_name"),
            "component_fingerprint": metadata.get("component_fingerprint"),
            "matched_dxf_view_ids": metadata.get("matched_dxf_view_ids", []),
            "matched_step_view": metadata.get("matched_step_view"),
            "global_geometry_score": metadata.get("global_geometry_score"),
            "global_geometry_pair_evidence": metadata.get("global_geometry_pair_evidence"),
            "geometry_verification": metadata.get("geometry_verification"),
            "verification_candidate": metadata.get("verification_candidate"),
        }
    paths = lookup_drawing_paths(clean_name) or {}
    dwg_p = paths.get("dwg")
    dxf_p = paths.get("dxf")

    if not dxf_p or not os.path.exists(dxf_p):
        candidate = os.path.join(r"D:\School\力致\力致_ref\temp_dxf_cache_ref", f"{clean_name}.dxf")
        if os.path.exists(candidate):
            dxf_p = candidate

    extracted_dims = []
    tolerances_only = []
    if dxf_p and os.path.exists(dxf_p):
        try:
            doc = ezdxf.readfile(dxf_p)
            modelspace = doc.modelspace()
            dims = dxf_extractor.extract_from_modelspace(
                modelspace,
                os.path.basename(dxf_p),
                include_rejected=True,
            )
            inference_engine = FeatureInference2DEngine(modelspace)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"DXF parse failed: {exc}")
        for d in dims:
            inference = inference_engine.infer(d) if d.is_feature_dimension else {}
            t_cfg = d.tolerance_config or {}
            mode = t_cfg.get("mode", "FIT")
            fit_cls = t_cfg.get("fit_class", "")
            u_dev = t_cfg.get("upper_dev", 0.0)
            l_dev = t_cfg.get("lower_dev", 0.0)
            dev = t_cfg.get("dev", 0.05)
            nom = d.nominal_value

            if mode == "FIT" and fit_cls:
                fmt = f"{fit_cls} ({u_dev:+.3f} / {l_dev:+.3f} mm)"
            elif mode == "GROOVE":
                fmt = f"(+{u_dev:.3f} / {l_dev:.3f} mm)"
            elif mode == "CUSTOM_SYMMETRIC":
                fmt = f"±{dev:.2f} mm"
            elif mode == "CUSTOM_LIMITS":
                fmt = f"(+{u_dev:.3f} / {l_dev:.3f} mm)"
            else:
                fmt = "一般未注 (ISO 2768-m)"

            item = {
                "dim_type": d.dim_type,
                "nominal_value": nom,
                "raw_text": d.raw_text,
                "prefix": d.prefix or "",
                "tolerance_mode": mode,
                "formatted_tolerance": fmt,
                "layer": d.layer,
                "points": d.points
                ,"entity_handle": d.entity_handle
                ,"source_entity_type": d.source_entity_type
                ,"dimension_category": d.dimension_category
                ,"validation_status": d.validation_status
                ,"extraction_confidence": d.extraction_confidence
                ,"is_feature_dimension": d.is_feature_dimension
                ,"validation_reasons": d.validation_reasons
                ,"feature_inference_2d": inference
            }
            item.update(case_evidence_by_handle.get(str(d.entity_handle or ""), {}))
            extracted_dims.append(item)
            if mode != "NONE" and d.is_feature_dimension and d.validation_status in ("AUTO_VALIDATED", "REVIEW_REQUIRED"):
                tolerances_only.append(item)

    return {
        "status": "ok",
        "model_name": clean_name,
        "dwg_path": dwg_p,
        "dxf_path": dxf_p,
        "has_dwg": bool(dwg_p and os.path.exists(dwg_p)),
        "has_dxf": bool(dxf_p and os.path.exists(dxf_p)),
        "svg_url": f"/api/tolerance/drawing-svg/{clean_name}" if dxf_p else None,
        "pdf_url": f"/api/tolerance/drawing-pdf/{clean_name}" if dxf_p else None,
        "total_dimensions_count": len(extracted_dims),
        "total_tolerances_count": len(tolerances_only),
        "tolerances": tolerances_only,
        "drawing_defaults": [d for d in extracted_dims if d["validation_status"] == "DRAWING_DEFAULT"],
        "rejected_items": [d for d in extracted_dims if d["validation_status"] == "REJECTED"],
        "all_dimensions": extracted_dims
    }


@app.post("/api/tolerance/open-local")
def open_local_drawing(body: Dict[str, Any] = Body(...)):
    """
    在 Windows 本機以預設 CAD 軟體 (AutoCAD / DWG TrueView 等) 開啟 DWG 或 DXF
    """
    model_name = body.get("model_name", "")
    fmt = body.get("format", "dwg").lower()
    clean_name = os.path.splitext(model_name)[0]
    paths = lookup_drawing_paths(clean_name) or {}

    target_path = None
    if fmt == "dwg" and paths.get("dwg") and os.path.exists(paths["dwg"]):
        target_path = paths["dwg"]
    elif paths.get("dxf") and os.path.exists(paths["dxf"]):
        target_path = paths["dxf"]
    elif paths.get("dwg") and os.path.exists(paths["dwg"]):
        target_path = paths["dwg"]

    if not target_path or not os.path.exists(target_path):
        candidate_dxf = os.path.join(r"D:\School\力致\力致_ref\temp_dxf_cache_ref", f"{clean_name}.dxf")
        if os.path.exists(candidate_dxf):
            target_path = candidate_dxf

    if not target_path or not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail=f"找不到 {model_name} 的本地圖面檔案 ({fmt})")

    try:
        os.startfile(target_path)
        return {
            "status": "ok",
            "message": f"已在 Windows 本機啟動預設程式開啟圖檔: {os.path.basename(target_path)}",
            "opened_file": target_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"本機開啟失敗: {str(e)}")


@app.get("/api/tolerance/download/{model_name}")
def download_drawing_file(model_name: str, format: str = "dwg"):
    """
    下載原始 DWG 或 DXF 檔案
    """
    clean_name = os.path.splitext(model_name)[0]
    paths = lookup_drawing_paths(clean_name) or {}
    fmt = format.lower()

    target_path = None
    if fmt == "dwg" and paths.get("dwg") and os.path.exists(paths["dwg"]):
        target_path = paths["dwg"]
    elif paths.get("dxf") and os.path.exists(paths["dxf"]):
        target_path = paths["dxf"]
    elif paths.get("dwg") and os.path.exists(paths["dwg"]):
        target_path = paths["dwg"]
    else:
        candidate_dxf = os.path.join(r"D:\School\力致\力致_ref\temp_dxf_cache_ref", f"{clean_name}.dxf")
        if os.path.exists(candidate_dxf):
            target_path = candidate_dxf

    if not target_path or not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail=f"找不到 {model_name} 的圖檔 ({fmt})")

    filename = os.path.basename(target_path)
    return FileResponse(target_path, filename=filename)


@app.post(
    "/api/tolerance/external-predictions/{model_id}/{part_id}",
    response_model=ExternalPredictionWriteResponse,
)
def save_external_tolerance_predictions(
    model_id: str,
    part_id: str,
    prediction_set: ExternalTolerancePredictionSet,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    """Store a versioned neural-model prediction batch beside model outputs.

    Predictions remain an independent source. They are never inserted into the
    verified historical case base and do not silently override CAD-RAG output.
    """
    _authorize_external_prediction_api(x_api_key)
    output_dir = _safe_output_dir(model_id)
    part_id = _safe_part_id(part_id)
    rule_ids = [item.rule_id for item in prediction_set.predictions]
    if len(rule_ids) != len(set(rule_ids)):
        raise HTTPException(status_code=400, detail="Duplicate rule_id in predictions")
    current_rules = get_candidate_annotation_rules(model_id, part_id).get("rules") or []
    valid_rule_ids = {
        str(rule.get("rule_id") or rule.get("id"))
        for rule in current_rules
        if rule.get("rule_id") or rule.get("id")
    }
    unknown_rule_ids = sorted(set(rule_ids) - valid_rule_ids)
    if unknown_rule_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Predictions contain rule_id values not present in current candidate rules",
                "unknown_rule_ids": unknown_rule_ids,
            },
        )

    payload = prediction_set.model_dump()
    payload.update({
        "source_type": "EXTERNAL_NEURAL_MODEL",
        "model_id": model_id,
        "part_id": part_id,
        "received_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    path = _external_prediction_path(output_dir, part_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return {
        "status": "ok",
        "model_id": model_id,
        "part_id": part_id,
        "source_type": payload["source_type"],
        "provider": payload["provider"],
        "model_name": payload["model_name"],
        "model_version": payload["model_version"],
        "prediction_count": len(payload["predictions"]),
        "received_at_utc": payload["received_at_utc"],
    }


@app.get(
    "/api/tolerance/external-predictions/{model_id}/{part_id}",
    response_model=ExternalPredictionReadResponse,
)
def get_external_tolerance_predictions(
    model_id: str,
    part_id: str,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    """Return the latest external neural-model prediction batch for a part."""
    _authorize_external_prediction_api(x_api_key)
    output_dir = _safe_output_dir(model_id)
    part_id = _safe_part_id(part_id)
    payload = _load_external_prediction_set(output_dir, part_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="External tolerance predictions not found")
    return {"status": "ok", **payload}


@app.delete(
    "/api/tolerance/external-predictions/{model_id}/{part_id}",
    response_model=ExternalPredictionDeleteResponse,
)
def delete_external_tolerance_predictions(
    model_id: str,
    part_id: str,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    """Remove the active external prediction batch without touching CAD-RAG cases."""
    _authorize_external_prediction_api(x_api_key)
    output_dir = _safe_output_dir(model_id)
    part_id = _safe_part_id(part_id)
    path = _external_prediction_path(output_dir, part_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="External tolerance predictions not found")
    os.remove(path)
    return {"status": "ok", "model_id": model_id, "part_id": part_id}


@app.post("/api/tolerance/recommend")
def recommend_tolerances(body: Dict[str, Any] = Body(...)):
    """
    智慧公差推薦 API：依據 3D 特徵關係圖 (FRG) 與歷史特徵案例庫 (CAD-RAG)，
    為候選標註規則進行三層分流公差決策 (Tier 1~3)
    """
    model_id = body.get("model_id")
    part_id = body.get("part_id")
    candidate_rules = body.get("candidate_rules")
    part_category = body.get("part_category")
    product_family = (
        body.get("product_family")
        or FeatureCaseBase.infer_product_family(part_id)
        or FeatureCaseBase.infer_product_family(model_id)
    )

    if not model_id or not part_id:
        raise HTTPException(status_code=400, detail="model_id and part_id are required")

    output_dir = _safe_output_dir(model_id)
    _safe_part_id(part_id)

    stp_candidates = [
        os.path.join(output_dir, "_parts", f"{part_id}.stp"),
        os.path.join(output_dir, "_parts", f"{part_id}.step"),
        os.path.join(output_dir, f"{part_id}.stp"),
        os.path.join(output_dir, f"{part_id}.step"),
        os.path.join(MODELS_DIR, f"{model_id}.stp"),
        os.path.join(MODELS_DIR, f"{model_id.replace('_batch', '')}.stp"),
    ]
    stp_path = None
    for p in stp_candidates:
        if os.path.exists(p):
            stp_path = p
            break

    if not stp_path:
        raise HTTPException(status_code=404, detail=f"STEP file for {part_id} not found")

    try:
        shape = load_step(stp_path)
        projector = ViewProjector()
        view_data = projector.project_all_views(shape, view_names=['front', 'top', 'right', 'left'])
        
        if not candidate_rules:
            engine = SmartAnnotationEngine()
            candidate_rules = engine.get_candidate_rules(shape, view_data)

        # 執行公差決策推薦
        rec_result = tolerance_service.recommend_for_rules(
            shape=shape,
            candidate_rules=candidate_rules,
            view_data=view_data,
            part_type=part_category,
            product_family=product_family,
        )

        # Attach traceable drawing links only when the referenced source DXF
        # really exists.  Seed/rule-only cases remain visible but non-clickable.
        for recommendation in (rec_result.get("recommendations") or {}).values():
            for evidence in recommendation.get("evidence_cases") or []:
                source_model = os.path.splitext(os.path.basename(str(
                    evidence.get("source_model") or evidence.get("drawing") or ""
                )))[0]
                paths = lookup_drawing_paths(source_model) if source_model else None
                has_dxf = bool(paths and paths.get("dxf") and os.path.exists(paths["dxf"]))
                evidence["source_model"] = source_model
                evidence["has_source_drawing"] = has_dxf
                evidence["drawing_urls"] = ({
                    "pdf": f"/api/tolerance/drawing-pdf/{source_model}",
                    "svg": f"/api/tolerance/drawing-svg/{source_model}",
                    "details": f"/api/tolerance/drawing-details/{source_model}",
                } if has_dxf else None)

        return {
            "status": "ok",
            "model_id": model_id,
            "part_id": part_id,
            "part_type": rec_result.get("part_type"),
            "product_family": rec_result.get("product_family"),
            "total_rules": rec_result.get("total_rules"),
            "high_confidence_count": rec_result.get("high_confidence_count"),
            "recommendations": rec_result.get("recommendations"),
            "feature_graph": rec_result.get("feature_graph"),
            "external_prediction_set": _load_external_prediction_set(output_dir, part_id),
        }
    except Exception as e:
        print(f"Tolerance recommendation error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tolerance/save-case")
def save_tolerance_case(body: Dict[str, Any] = Body(...)):
    """將審定公差或使用者確認後的標註儲存為歷史案例"""
    try:
        case_id = body.get("case_id", f"case_custom_{uuid.uuid4().hex[:6]}")
        new_case = ToleranceCase(
            case_id=case_id,
            part_type=body.get("part_type", body.get("part_category", "SHAFT")),
            feature_type=body.get("feature_type", "shaft_segment"),
            inferred_role=body.get("inferred_role", "FUNCTIONAL_JOURNAL"),
            nominal_dimensions=body.get("nominal_dimensions", {}),
            neighbor_types=body.get("neighbor_types", []),
            boundary_position=body.get("boundary_position", "INTERIOR"),
            tolerance_config=body.get("tolerance_config", {}),
            confidence=1.0,
            evidence_source="ENGINEER_CONFIRMED",
            description=body.get("description", "工程師前端審定確認之公差案例"),
            verification_status="ENGINEER_VERIFIED",
            source_metadata=body.get("source_metadata", {}),
        )
        case_base.add_case(new_case)
        return {"status": "ok", "case_id": case_id, "total_cases": len(case_base.cases)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/annotation/templates")
def get_annotation_templates():
    """取得所有可用標註樣板清單"""
    templates = template_manager.list_templates()
    return {"status": "ok", "templates": templates}


@app.post("/api/annotation/templates")
def save_annotation_template(data: Dict[str, Any] = Body(...)):
    """儲存或建立新標註樣板"""
    try:
        saved = template_manager.save_template(data)
        return {"status": "ok", "template": saved}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/annotation/templates/{template_id}")
def delete_annotation_template(template_id: str):
    """刪除自訂標註樣板"""
    try:
        success = template_manager.delete_template(template_id)
        if not success:
            raise HTTPException(status_code=404, detail="Template not found")
        return {"status": "ok", "message": "Template deleted"}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/annotation/apply-template")
def apply_annotation_template(body: Dict[str, Any] = Body(...)):
    """
    將指定樣板的規則動態套用至特徵清單
    Body: {"template_id": "...", "feature_records": [...]} 或 {"template": {...}, "feature_records": [...]}
    """
    template_id = body.get("template_id")
    template = body.get("template")
    feature_records = body.get("feature_records", [])

    if not template and template_id:
        template = template_manager.get_template(template_id)

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    updated_records = template_manager.match_and_apply(template, feature_records)
    return {"status": "ok", "records": updated_records, "applied_template": template.get("name")}


@app.post("/api/annotation/render")
def render_custom_annotation_drawing(body: Dict[str, Any] = Body(...)):
    """
    依據使用者選定之特徵與自訂公差組態，即時產出客製化工程圖 (DXF, PDF, PNG)
    Body: {
      "model_id": "...",
      "part_id": "...",
      "feature_records": [...],
      "title_info": {...}
    }
    """
    model_id = body.get("model_id")
    part_id = body.get("part_id")
    feature_records = body.get("feature_records", [])
    title_info = body.get("title_info", {})

    if not model_id or not part_id:
        raise HTTPException(status_code=400, detail="model_id and part_id are required")

    output_dir = _safe_output_dir(model_id)
    _safe_part_id(part_id)

    # 尋找對應的實體 STEP 檔
    stp_candidates = [
        os.path.join(output_dir, "_parts", f"{part_id}.stp"),
        os.path.join(output_dir, f"{part_id}.stp"),
        os.path.join(MODELS_DIR, f"{model_id}.stp"),
        os.path.join(MODELS_DIR, f"{model_id.replace('_batch', '')}.stp"),
    ]
    stp_path = None
    for p in stp_candidates:
        if os.path.exists(p):
            stp_path = p
            break

    if not stp_path:
        raise HTTPException(status_code=404, detail=f"STEP file for {part_id} not found")

    try:
        # 1. 載入實體並執行投影
        shape = load_step(stp_path)
        projector = ViewProjector()
        view_data = projector.project_all_views(shape, view_names=['front', 'top', 'right', 'left'])

        # 2. 準備客製輸出目錄
        custom_out_dir = os.path.join(output_dir, "_custom_annotations", part_id)
        os.makedirs(custom_out_dir, exist_ok=True)

        dxf_path = os.path.join(custom_out_dir, f"{part_id}_custom.dxf")
        pdf_path = os.path.join(custom_out_dir, f"{part_id}_custom.pdf")
        png_path = os.path.join(custom_out_dir, f"{part_id}_custom.png")

        # 3. 呼叫智慧標註引擎渲染
        engine = SmartAnnotationEngine()
        engine.render_custom_drawing(
            dxf_path=dxf_path,
            pdf_path=pdf_path,
            png_path=png_path,
            feature_records=feature_records,
            view_data=view_data,
            title_info=title_info
        )

        return {
            "status": "ok",
            "dxf_url": _api_file_url(model_id, "_custom_annotations", part_id, f"{part_id}_custom.dxf"),
            "pdf_url": _api_file_url(model_id, "_custom_annotations", part_id, f"{part_id}_custom.pdf"),
            "svg_url": _api_file_url(model_id, "_custom_annotations", part_id, f"{part_id}_custom.svg"),
            "png_url": _api_file_url(model_id, "_custom_annotations", part_id, f"{part_id}_custom.png"),
            "timestamp": uuid.uuid4().hex[:8]
        }
    except Exception as e:
        print(f"Custom annotation render error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/annotation/candidate-rules/{model_id}/{part_id}")
def get_candidate_annotation_rules(model_id: str, part_id: str):
    """
    為指定零件動態掃描並產出所有候選標註規則 (Candidate Dimension Rules)
    """
    output_dir = _safe_output_dir(model_id)
    _safe_part_id(part_id)

    stp_candidates = [
        os.path.join(output_dir, "_parts", f"{part_id}.stp"),
        os.path.join(output_dir, "_parts", f"{part_id}.step"),
        os.path.join(output_dir, f"{part_id}.stp"),
        os.path.join(output_dir, f"{part_id}.step"),
    ]
    stp_path = None
    for p in stp_candidates:
        if os.path.exists(p):
            stp_path = p
            break

    if not stp_path:
        raise HTTPException(status_code=404, detail=f"STEP file for {part_id} not found")

    try:
        shape = load_step(stp_path)
        projector = ViewProjector()
        view_data = projector.project_all_views(shape, view_names=['front', 'top', 'right', 'left'])
        engine = SmartAnnotationEngine()
        rules = engine.get_candidate_rules(shape, view_data)
        return {
            "status": "ok",
            "model_id": model_id,
            "part_id": part_id,
            "rules": rules
        }
    except Exception as e:
        print(f"Candidate rules extraction error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/features/{model_id}/{part_id}")
def get_part_features(model_id: str, part_id: str):
    """
    動態取得或即時提取任何零件的完整 3D 機械特徵清單
    若無快取或為舊版 2D 格式，會自動載入實體 STEP 進行即時提取並更新快取
    """
    output_dir = _safe_output_dir(model_id)
    _safe_part_id(part_id)

    parts_map = build_parts_map(output_dir, model_id)
    part_entry = parts_map.get(part_id, {})

    feature_json_rel = part_entry.get("features_json")
    json_path = None
    if feature_json_rel:
        json_path = os.path.join(output_dir, os.path.basename(feature_json_rel))
    else:
        json_path = os.path.join(output_dir, f"{part_id}_feature_records.json")

    # 1. 檢查快取是否有有效 3D 特徵 (center 必須為 3D 座標)
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                records = json.load(f)
            if isinstance(records, list) and len(records) > 0:
                has_3d = any(
                    isinstance(r.get("geometry"), dict) and
                    isinstance(r.get("geometry", {}).get("center"), list) and
                    len(r.get("geometry", {}).get("center", [])) >= 3
                    for r in records
                )
                if has_3d:
                    return {"status": "ok", "records": records, "source": "cache"}
        except Exception:
            pass

    # 2. 若無 3D 快取，即時從實體 STEP 進行 OpenCASCADE 幾何拓撲動態提取
    stp_candidates = [
        os.path.join(output_dir, "_parts", f"{part_id}.stp"),
        os.path.join(output_dir, f"{part_id}.stp"),
        os.path.join(MODELS_DIR, f"{model_id}.stp"),
        os.path.join(MODELS_DIR, f"{model_id.replace('_batch', '')}.stp"),
    ]

    for stp_path in stp_candidates:
        if os.path.exists(stp_path):
            try:
                shape = load_step(stp_path)
                if shape and not shape.IsNull():
                    fe = FeatureExtractor(shape)
                    records = build_feature_records(fe, None)
                    save_path = json_path or os.path.join(output_dir, f"{part_id}_feature_records.json")
                    with open(save_path, "w", encoding="utf-8") as f:
                        json.dump(records, f, indent=2, ensure_ascii=False)
                    return {"status": "ok", "records": records, "source": "dynamic_extracted"}
            except Exception as e:
                print(f"Dynamic feature extraction failed for {stp_path}: {e}")

    # Fallback
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return {"status": "ok", "records": json.load(f), "source": "2d_fallback"}
        except Exception:
            pass

    return {"status": "ok", "records": [], "source": "none"}


def _build_drawing_package(model_id: str) -> dict:
    output_dir = _safe_output_dir(model_id)
    parts_map = build_parts_map(output_dir, model_id)
    parts = {}

    for part_id, entry in parts_map.items():
        package_entry = {
            "part_id": part_id,
            "main": {
                key: entry[key]
                for key in ("pdf", "svg", "png", "dxf", "stl")
                if key in entry
            },
            "views": _view_urls(entry),
            "feature_layer": {
                key.replace("features_", ""): entry[key]
                for key in ("features_pdf", "features_svg", "features_json")
                if key in entry
            },
        }

        annotations_path = _annotation_path(output_dir, part_id)
        if os.path.exists(annotations_path):
            package_entry["external_annotations"] = _api_file_url(
                model_id, "_annotations", os.path.basename(annotations_path)
            )

        parts[part_id] = package_entry

    tree_path = os.path.join(output_dir, "_parts", "assembly_tree.json")
    tree_data = None
    if os.path.exists(tree_path):
        with open(tree_path, "r", encoding="utf-8") as f:
            tree_data = json.load(f)

    return {
        "model_id": model_id,
        "output_dir": model_id,
        "tree": tree_data,
        "parts": parts,
    }


def run_job(job_id: str, file_path: str):
    def progress_callback(total, current, message):
        jobs[job_id]["total"] = total
        jobs[job_id]["current"] = current
        jobs[job_id]["message"] = message
        if total > 0 and total == current:
            jobs[job_id]["status"] = "completed"

    try:
        model_name = os.path.splitext(os.path.basename(file_path))[0]
        output_dir_name = model_name + "_batch"
        output_dir = os.path.join(OUTPUT_DIR, output_dir_name)
        
        assembly_info = {
            '_assembly': {
                'name': '組合件',
                'drawing_no': model_name,
                'revision': 'R00',
                'material': '---',
                'model_code': '---',
            }
        }
        
        batch_generate(file_path, output_dir, assembly_info, progress_cb=progress_callback)
        
        # Read assembly tree to return as result
        tree_path = os.path.join(output_dir, "_parts", "assembly_tree.json")
        if os.path.exists(tree_path):
            with open(tree_path, "r", encoding="utf-8") as f:
                jobs[job_id]["result"] = json.load(f)
        jobs[job_id]["parts_map"] = build_parts_map(output_dir, output_dir_name)
        jobs[job_id]["output_dir"] = output_dir_name
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["message"] = "處理完成"
        
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["message"] = f"發生錯誤: {str(e)}"


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    
    os.makedirs(MODELS_DIR, exist_ok=True)
    file_path = os.path.join(MODELS_DIR, file.filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    jobs[job_id] = {
        "status": "processing",
        "total": 0,
        "current": 0,
        "message": "已接收檔案，準備開始處理...",
        "filename": file.filename,
        "result": None,
        "output_dir": None
    }
    
    executor.submit(run_job, job_id, file_path)
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    return {
        "status": job["status"],
        "message": job["message"],
        "progress": {"current": job.get("current", 0), "total": job.get("total", 0)},
        "logs": job.get("logs", [])
    }


@app.get("/api/results/{job_id}")
async def get_results(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not completed yet")
        
    return {
        "tree": job.get("result"),
        "parts_map": job.get("parts_map", {}),
        "output_dir": job.get("output_dir"),
        "diff_result": job.get("diff_result"),
        "stats": job.get("stats"),
        "tree_old": job.get("tree_old"),
        "tree_new": job.get("tree_new")
    }

# === 模型比對 API ===
def run_compare_job(job_id: str, old_path: str, new_path: str):
    try:
        from auto_2d_drawing.compare_models import compare_step_files
        output_dir_name = f"diff_{job_id[:8]}"
        output_dir = os.path.join(OUTPUT_DIR, output_dir_name)
        
        def progress_cb(msg):
            jobs[job_id]["message"] = msg
            if "logs" not in jobs[job_id]:
                jobs[job_id]["logs"] = []
            jobs[job_id]["logs"].append(msg)
            
        results = compare_step_files(old_path, new_path, output_dir, progress_callback=progress_cb)
        
        # 提取非路徑的資料
        stats = results.pop('stats', None)
        tree_old = results.pop('tree_old', None)
        tree_new = results.pop('tree_new', None)
        
        # 將本地路徑轉換為 URL
        diff_urls = {}
        for key, path in results.items():
            if isinstance(path, str) and path.endswith('.stl'):
                filename = os.path.basename(path)
                diff_urls[key] = f"/api/files/{output_dir_name}/{filename}"
            
        jobs[job_id]["diff_result"] = diff_urls
        jobs[job_id]["stats"] = stats
        jobs[job_id]["tree_old"] = tree_old
        jobs[job_id]["tree_new"] = tree_new
        jobs[job_id]["output_dir"] = output_dir_name
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["message"] = "比對完成"
        
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["message"] = f"比對發生錯誤: {str(e)}"
        
@app.post("/api/compare")
async def compare_files(file_old: UploadFile = File(...), file_new: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    
    os.makedirs(MODELS_DIR, exist_ok=True)
    old_path = os.path.join(MODELS_DIR, f"old_{job_id[:8]}_{file_old.filename}")
    new_path = os.path.join(MODELS_DIR, f"new_{job_id[:8]}_{file_new.filename}")
    
    with open(old_path, "wb") as buffer:
        shutil.copyfileobj(file_old.file, buffer)
    with open(new_path, "wb") as buffer:
        shutil.copyfileobj(file_new.file, buffer)
        
    jobs[job_id] = {
        "status": "processing",
        "total": 0,
        "current": 0,
        "message": "已接收比對檔案，準備開始分析差異...",
        "filename": f"{file_old.filename} vs {file_new.filename}",
        "result": None,
        "output_dir": None,
        "is_diff": True,
        "logs": []
    }
    
    executor.submit(run_compare_job, job_id, old_path, new_path)
    return {"job_id": job_id}

@app.get("/api/models")
async def list_models():
    """列出所有已生成的模型 (output 目錄下的資料夾)"""
    models = []
    if os.path.exists(OUTPUT_DIR):
        for item in os.listdir(OUTPUT_DIR):
            item_path = os.path.join(OUTPUT_DIR, item)
            if os.path.isdir(item_path) and item.endswith("_batch"):
                model_name = item.replace("_batch", "")
                models.append({
                    "id": item,
                    "name": model_name
                })
    return {"models": models}

@app.get("/api/model/{model_id}")
async def get_model(model_id: str):
    """直接讀取已生成模型的資料"""
    output_dir = os.path.join(OUTPUT_DIR, model_id)
    if not os.path.exists(output_dir):
        raise HTTPException(status_code=404, detail="Model not found")
    
    # 讀取 tree
    tree_path = os.path.join(output_dir, "_parts", "assembly_tree.json")
    tree_data = None
    if os.path.exists(tree_path):
        with open(tree_path, "r", encoding="utf-8") as f:
            tree_data = json.load(f)
            
    parts_map = build_parts_map(output_dir, model_id)
                
    return {
        "tree": tree_data,
        "parts_map": parts_map,
        "output_dir": model_id
    }


@app.get("/api/drawings/{model_id}")
async def get_drawing_package(model_id: str):
    """取得外部對接用的工程圖套件：合圖、三視圖、STL、特徵標註檔。"""
    return _build_drawing_package(model_id)


@app.get("/api/drawings/{model_id}/parts/{part_id}/features")
async def get_part_features(model_id: str, part_id: str):
    """取得指定零件/組合件的特徵標註候選資料。"""
    output_dir = _safe_output_dir(model_id)
    part_id = _safe_part_id(part_id)
    parts_map = build_parts_map(output_dir, model_id)
    if part_id not in parts_map:
        raise HTTPException(status_code=404, detail="Part not found")

    features_url = parts_map[part_id].get("features_json")
    if not features_url:
        raise HTTPException(status_code=404, detail="Feature records not found")

    features_path = os.path.join(output_dir, os.path.basename(features_url))
    if not os.path.exists(features_path):
        raise HTTPException(status_code=404, detail="Feature records not found")

    with open(features_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    return {
        "model_id": model_id,
        "part_id": part_id,
        "features_url": features_url,
        "records": records,
    }


@app.get("/api/drawings/{model_id}/parts/{part_id}/annotations")
async def get_part_annotations(model_id: str, part_id: str):
    """取得外部系統回傳並已儲存的標註資訊。"""
    output_dir = _safe_output_dir(model_id)
    part_id = _safe_part_id(part_id)
    annotations_path = _annotation_path(output_dir, part_id)
    if not os.path.exists(annotations_path):
        raise HTTPException(status_code=404, detail="Annotations not found")

    with open(annotations_path, "r", encoding="utf-8") as f:
        annotations = json.load(f)

    return {
        "model_id": model_id,
        "part_id": part_id,
        "annotations": annotations,
    }


@app.post("/api/drawings/{model_id}/parts/{part_id}/annotations")
async def save_part_annotations(
    model_id: str,
    part_id: str,
    annotations: Dict[str, Any] = Body(...),
):
    """接收外部系統回傳的標註資訊，儲存在該模型輸出資料夾內。"""
    output_dir = _safe_output_dir(model_id)
    part_id = _safe_part_id(part_id)
    parts_map = build_parts_map(output_dir, model_id)
    if part_id not in parts_map:
        raise HTTPException(status_code=404, detail="Part not found")

    annotations_dir = os.path.join(output_dir, "_annotations")
    os.makedirs(annotations_dir, exist_ok=True)
    annotations_path = _annotation_path(output_dir, part_id)
    with open(annotations_path, "w", encoding="utf-8") as f:
        json.dump(annotations, f, indent=2, ensure_ascii=False)

    return {
        "status": "success",
        "message": "Annotations saved.",
        "model_id": model_id,
        "part_id": part_id,
        "annotations_url": _api_file_url(model_id, "_annotations", os.path.basename(annotations_path)),
    }

# === 範例圖 API ===
EXAMPLE_DIR = os.path.join(PROJECT_ROOT, "auto_2d_drawing", "reference", "example_output")
NEW_EXAMPLE_DIR = os.path.abspath(os.environ.get("CAD_NEW_EXAMPLE_DIR", r"F:\School\力致\new_data"))
PROCESSED_FAN_20260625_DIR_NAME = "fan_20260625_autodraw"
PROCESSED_FAN_20260625_DIR = os.path.join(OUTPUT_DIR, PROCESSED_FAN_20260625_DIR_NAME)

def clean_example_filename(filename):
    """清理檔名作為顯示名稱 — 直接使用原始檔名（去掉副檔名）"""
    return os.path.splitext(filename)[0]

def build_file_tree(current_path, name, base_dir, url_prefix, allowed_exts):
    node = {"name": name, "type": "folder", "children": []}
    if os.path.isdir(current_path):
        for entry in sorted(os.listdir(current_path)):
            entry_path = os.path.join(current_path, entry)
            if os.path.isdir(entry_path):
                child = build_file_tree(entry_path, entry, base_dir, url_prefix, allowed_exts)
                if child["children"]:
                    node["children"].append(child)
            elif entry.lower().endswith(allowed_exts):
                rel_path = os.path.relpath(entry_path, base_dir)
                node["children"].append({
                    "name": clean_example_filename(entry),
                    "display_name": entry,
                    "type": "file",
                    "url": f"{url_prefix}/{rel_path.replace(os.sep, '/')}",
                    "filename": entry
                })
    return node

VIEW_SUFFIX_LABELS = {
    "_features_view": "特徵圖層",
    "_front": "前視圖",
    "_back": "背面視圖",
    "_top": "俯視圖",
    "_right": "右側視圖",
    "_left": "左側視圖",
}
VIEW_ORDER = {
    "main": 0,
    "_features_view": 1,
    "_front": 2,
    "_back": 3,
    "_top": 4,
    "_right": 5,
    "_left": 6,
    "dxf": 7,
}
EXT_PRIORITY = {".pdf": 0, ".svg": 1, ".dxf": 2}

def split_generated_view_name(filename):
    stem, ext = os.path.splitext(filename)
    lower_ext = ext.lower()
    for suffix in VIEW_SUFFIX_LABELS:
        if stem.endswith(suffix):
            return stem[:-len(suffix)], suffix, lower_ext
    return stem, "dxf" if lower_ext == ".dxf" else "main", lower_ext

def build_grouped_processed_tree(current_path, name, base_dir, url_prefix):
    """Group generated variants so each model is one collapsible node in the UI."""
    node = {"name": name, "type": "folder", "children": []}
    if not os.path.isdir(current_path):
        return node

    file_groups = {}
    for entry in sorted(os.listdir(current_path)):
        entry_path = os.path.join(current_path, entry)
        if os.path.isdir(entry_path):
            child = build_grouped_processed_tree(entry_path, entry, base_dir, url_prefix)
            if child["children"]:
                node["children"].append(child)
            continue

        ext = os.path.splitext(entry)[1].lower()
        if ext not in (".pdf", ".svg", ".dxf"):
            continue

        group_name, variant, ext = split_generated_view_name(entry)
        file_groups.setdefault(group_name, {})

        # Keep the most viewable artifact for each variant: PDF, then SVG, then DXF.
        previous = file_groups[group_name].get(variant)
        if previous and EXT_PRIORITY.get(previous["ext"], 99) <= EXT_PRIORITY.get(ext, 99):
            continue

        rel_path = os.path.relpath(entry_path, base_dir)
        file_groups[group_name][variant] = {
            "ext": ext,
            "node": {
                "name": VIEW_SUFFIX_LABELS.get(variant, "DXF" if variant == "dxf" else "合圖"),
                "display_name": VIEW_SUFFIX_LABELS.get(variant, "DXF" if variant == "dxf" else "合圖"),
                "type": "file",
                "url": f"{url_prefix}/{rel_path.replace(os.sep, '/')}",
                "filename": entry,
            },
        }

    for group_name in sorted(file_groups):
        variants = file_groups[group_name]
        children = [
            variants[key]["node"]
            for key in sorted(variants, key=lambda item: VIEW_ORDER.get(item, 99))
        ]
        if len(children) == 1:
            only_child = dict(children[0])
            only_child["name"] = group_name
            only_child["display_name"] = f"{group_name} / {children[0]['display_name']}"
            node["children"].append(only_child)
            continue

        node["children"].append({
            "name": group_name,
            "display_name": group_name,
            "type": "folder",
            "children": children,
        })

    return node

if os.path.exists(EXAMPLE_DIR):
    app.mount("/api/examples/files", StaticFiles(directory=EXAMPLE_DIR), name="example_files")

if os.path.exists(NEW_EXAMPLE_DIR):
    app.mount("/api/examples/new_files", StaticFiles(directory=NEW_EXAMPLE_DIR), name="new_example_files")

@app.get("/api/examples")
async def list_examples():
    """列出範例資料夾下的所有檔案，並維持資料夾結構"""
    def _build_tree(current_path, name, base_dir, url_prefix):
        return build_file_tree(
            current_path,
            name,
            base_dir,
            url_prefix,
            ('.pdf', '.svg', '.dwg', '.xls', '.xlsx', '.7z', '.zip')
        )

    root_node = {"name": "所有公司範例圖", "type": "folder", "children": []}
    
    if os.path.exists(EXAMPLE_DIR):
        old_tree = _build_tree(EXAMPLE_DIR, "公司範例圖 (舊版)", EXAMPLE_DIR, "/api/examples/files")
        if old_tree["children"]:
            root_node["children"].append(old_tree)
            
    if os.path.exists(NEW_EXAMPLE_DIR):
        new_tree = _build_tree(NEW_EXAMPLE_DIR, "公司範例圖 (新版)", NEW_EXAMPLE_DIR, "/api/examples/new_files")
        if new_tree["children"]:
            root_node["children"].append(new_tree)

    return {"example_tree": root_node if root_node["children"] else None}

@app.get("/api/processed/fan-20260625")
async def list_processed_fan_20260625():
    """列出 FAN 20260625 批次輸出的工程圖，維持原始資料夾結構。"""
    if not os.path.exists(PROCESSED_FAN_20260625_DIR):
        return {"processed_tree": None, "manifest": None}

    tree = build_grouped_processed_tree(
        PROCESSED_FAN_20260625_DIR,
        "FAN 20260625 已處理工程圖",
        PROCESSED_FAN_20260625_DIR,
        f"/api/files/{PROCESSED_FAN_20260625_DIR_NAME}"
    )
    manifest = None
    manifest_path = os.path.join(PROCESSED_FAN_20260625_DIR, "batch_index.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    return {"processed_tree": tree if tree["children"] else None, "manifest": manifest}


@app.get("/api/health", tags=["system"])
def health_check():
    """Container/orchestrator liveness endpoint; does not execute CAD work."""
    return {
        "status": "ok",
        "service": "forcecon-step-to-2d",
        "version": app.version,
        "models_dir": MODELS_DIR,
        "output_dir": OUTPUT_DIR,
    }


# === 公差設定相容 API ===
# 保留給仍使用全域預設值的舊整合；新的逐特徵整合應使用
# /api/tolerance/external-predictions 與 annotation render payload。
class ToleranceConfig(BaseModel):
    default_tolerance: Optional[str] = "±0.1"
    feature_overrides: Optional[Dict[str, str]] = Field(default_factory=dict)


global_tolerances = ToleranceConfig(
    default_tolerance="±0.1",
    feature_overrides={"shaft": "±0.05", "hole": "±0.02"},
)


@app.get("/api/tolerances")
async def get_tolerances():
    """取得目前的相容性全域公差設定。"""
    return global_tolerances.model_dump()


@app.post("/api/tolerances")
async def update_tolerances(config: ToleranceConfig):
    """更新相容性全域公差設定；資料只存在目前 process。"""
    global global_tolerances
    global_tolerances = config
    return {
        "status": "success",
        "message": "Tolerances updated.",
        "data": global_tolerances.model_dump(),
    }

# === 前端網頁路由 ===
@app.get("/tolerance-inspector.html", include_in_schema=False)
def tolerance_inspector_page():
    """Serve the source-controlled inspector even before a frontend rebuild."""
    public_page = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend",
        "public",
        "tolerance-inspector.html",
    )
    if not os.path.exists(public_page):
        raise HTTPException(status_code=404, detail="Tolerance inspector is not installed")
    return FileResponse(public_page, media_type="text/html")


FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    print(f"Warning: Frontend dist directory not found at {FRONTEND_DIR}. Please run 'npm run build' in frontend folder.")

if __name__ == "__main__":
    import uvicorn
    # Trigger reload 22 — Force reload for backend stats & tree_diff
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
