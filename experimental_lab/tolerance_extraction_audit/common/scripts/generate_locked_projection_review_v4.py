"""Create V4 review boards with local-region shaft direction disambiguation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.dxf_structure_2d import DxfStructure2DAnalyzer
from auto_2d_drawing.tolerance.global_geometry_search import GlobalGeometryPairSearcher
from auto_2d_drawing.tolerance.ingest_historical_data import (
    ExtractedDimension,
    HistoricalDataIngestor,
    _extract_dxf_worker,
)
from auto_2d_drawing.tolerance.projection_registration_v2 import (
    AdaptiveProjectionCandidateGenerator,
    ProjectionRegistrationEngine,
    SimilarityTransform2D,
)
from dxf_projection_views_v2 import DxfProjectionViewExtractorV2, DxfViewCandidate
from generate_locked_projection_review_v3 import (
    BACKGROUND,
    BLUE,
    GREEN,
    MUTED,
    ORANGE,
    PANEL,
    WHITE,
    _fit,
    _font,
    _matching_hit,
    _safe_name,
)
from run_locked_projection_tolerance_experiment import (
    _balanced_arrays,
    _registration_payload,
    _verify,
)
from run_projection_pairing_v2 import render_overlay, transform_from_registration


def _legacy_candidate(dxf_path: Path, view_id: str):
    extractor = DxfProjectionViewExtractorV2(str(dxf_path))
    analyzer = DxfStructure2DAnalyzer(extractor.document.modelspace())
    cluster_id = view_id.removeprefix("legacy_")
    cluster = next(item for item in analyzer.view_clusters if item.view_id == cluster_id)
    helper = GlobalGeometryPairSearcher(maximum_record_points=256)
    points = helper._reduce_points(helper._dxf_cluster_points(analyzer, cluster))
    primitives = [analyzer.primitives[index] for index in cluster.primitive_indexes]
    return DxfViewCandidate(
        view_id=view_id,
        source_path=str(dxf_path),
        source="legacy_insert_or_path_cluster",
        primitive_indexes=[],
        points=points,
        bbox=cluster.bbox,
        primitive_count=len(primitives),
        curved_primitive_count=sum(
            item.entity_type in {"ARC", "CIRCLE"} for item in primitives
        ),
        layer_count=len({item.layer for item in primitives}),
    ), cluster_id


def _correct_shaft_direction(component, dxf_path: Path, hit: dict, artifact_path: Path):
    candidate, verifier_view_id = _legacy_candidate(dxf_path, str(hit["dxf_view"]))
    helper = GlobalGeometryPairSearcher(maximum_record_points=256)
    step_views = AdaptiveProjectionCandidateGenerator(max_views=6).project_all(component.shape)
    step_view_name = str(hit["step_view"])
    step_view = step_views[step_view_name]
    step_points = helper._reduce_points(helper._step_view_points(step_view))
    engine = ProjectionRegistrationEngine(max_points=320)
    registration = engine.register(candidate.points, step_points)
    target, source = _balanced_arrays(engine, candidate.points, step_points)

    _source, payloads = _extract_dxf_worker(str(dxf_path))
    dimensions = [ExtractedDimension(**item) for item in payloads]
    dimensions, _suppressed = HistoricalDataIngestor._deduplicate_dimensions(dimensions)
    dimensions = [
        item for item in dimensions
        if HistoricalDataIngestor._tolerance_is_plausible(item)
    ]
    ingestor = HistoricalDataIngestor()
    candidates = []
    for alternative in (registration.get("alternatives") or [])[:8]:
        transform = SimilarityTransform2D(
            scale=float(alternative["scale"]),
            rotation_degrees=float(alternative["rotation_degrees"]),
            translation=tuple(alternative["translation"]),
            mirrored=bool(alternative.get("mirrored", False)),
        )
        payload = {
            **_registration_payload(engine, transform, target, source),
            "dxf_view_id": verifier_view_id,
            "step_view": step_view_name,
            "dxf_bbox": [round(float(value), 4) for value in candidate.bbox],
            "step_bbox": [round(float(value), 4) for value in step_view.get("bbox") or ()],
            "projection": dict(step_view.get("projection") or {}),
            "lock_source": "PROJECTION_PAIRING_V2_LOCAL_REGION",
        }
        cases, _stats = _verify(ingestor, component, dxf_path, dimensions, payload)
        candidates.append((len(cases), float(payload["score"]), payload, transform, cases))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    verified_count, _score, selected, transform, cases = candidates[0]
    transformed = transform.apply(step_points)
    render_overlay(candidate, transformed, selected, artifact_path)
    return {
        "method": "LOCKED_LOCAL_REGION_TOLERANCE_DIRECTION_DISAMBIGUATION_V1",
        "verifier_view_id": verifier_view_id,
        "rotation_degrees": selected["rotation_degrees"],
        "mirrored": selected["mirrored"],
        "score": selected["score"],
        "verified_tolerance_count": verified_count,
        "feature_ids": [case.source_metadata.get("matched_feature_id") for case in cases],
        "overlay_path": str(artifact_path.resolve()),
    }


def _render_board(component: dict, hit: dict, source_root: Path, overlay_path: Path, correction, output: Path):
    relative_board = Path(str(hit["review_board"]).replace("\\", "/"))
    original_path = (source_root / relative_board).parent / "02_original_dxf_and_hit_region.png"
    width, height = 2200, 900
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=16, fill=PANEL)
    draw.text((42, 32), str(component["component_name"]), font=_font(30), fill=WHITE)
    correction_text = ""
    if correction:
        correction_text = (
            f"  resolved rotation={float(correction['rotation_degrees']):.2f}deg  "
            f"verified tolerances={int(correction['verified_tolerance_count'])}"
        )
    draw.text(
        (42, 75),
        (
            f"DXF={hit['dxf']}  STEP={hit['step_view']}  region={hit['dxf_view']}  "
            f"pairing score={float(hit['score']):.4f}{correction_text}"
        ),
        font=_font(18), fill=MUTED,
    )
    draw.text((42, 118), "DIRECTION-RESOLVED LOCAL OVERLAY", font=_font(22), fill=WHITE)
    draw.text((1120, 118), "ORIGINAL 2D DRAWING REVIEW", font=_font(22), fill=WHITE)
    with Image.open(overlay_path) as overlay_source, Image.open(original_path) as original_source:
        overlay = _fit(overlay_source, (1030, 690))
        original = _fit(original_source, (1030, 690))
    boxes = ((35, 158, 1085, 858), (1115, 158, 2165, 858))
    for box in boxes:
        draw.rectangle(box, fill=BACKGROUND, outline="#404040", width=2)
    for fitted, box in ((overlay, boxes[0]), (original, boxes[1])):
        image.paste(fitted, (
            box[0] + (box[2] - box[0] - fitted.width) // 2,
            box[1] + (box[3] - box[1] - fitted.height) // 2,
        ))
    draw.rectangle((52, 815, 70, 833), fill=BLUE)
    draw.text((78, 811), "DXF selected-region geometry", font=_font(16), fill=WHITE)
    draw.rectangle((350, 815, 368, 833), fill=ORANGE)
    draw.text((376, 811), "registered STEP HLR", font=_font(16), fill=WHITE)
    draw.text((1135, 811), "Orange box = selected local view", font=_font(16), fill=ORANGE)
    draw.text((1135, 835), "Pairing region fixed; only symmetric direction resolved", font=_font(15), fill=GREEN)
    image.save(output)
    return str(original_path.resolve())


def run(config_path: Path, report_path: Path, output_dir: Path):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    source_root = report_path.parent
    expected = dict(config.get("expected_dxf_by_component") or {})
    components = {
        item.name: item
        for step_path in config.get("step_paths") or []
        for item in extract_leaf_components(str(Path(step_path).resolve()))
        if item.name in expected
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir = output_dir / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    rows, boards = [], []
    for row in report.get("components") or []:
        name = str(row["component_name"])
        hit = _matching_hit(row)
        relative_board = Path(str(hit["review_board"]).replace("\\", "/"))
        source_overlay = (source_root / relative_board).parent / "03_projection_overlay.png"
        correction = None
        overlay_path = source_overlay
        if name == "2AL0W5010H-R01":
            overlay_path = artifact_dir / f"{_safe_name(name)}_direction_resolved_overlay.png"
            dxf_path = Path(config["dxf_dir"]).resolve() / str(hit["dxf"])
            correction = _correct_shaft_direction(
                components[name], dxf_path, hit, overlay_path
            )
        output = output_dir / f"{_safe_name(name)}_02_projection_with_original_review.png"
        original_path = _render_board(
            row, hit, source_root, overlay_path, correction, output
        )
        boards.append(output)
        rows.append({
            "component_name": name,
            "drawing": hit["dxf"],
            "step_view": hit["step_view"],
            "dxf_view": hit["dxf_view"],
            "direction_correction": correction,
            "source_original_2d_review": original_path,
            "review_board": output.name,
        })
    contact = Image.new("RGB", (2200, 900 * len(boards)), BACKGROUND)
    for index, board_path in enumerate(boards):
        with Image.open(board_path) as board:
            contact.paste(board.convert("RGB"), (0, 900 * index))
    contact_path = output_dir / "00_projection_with_original_2d_review_contact_sheet.png"
    contact.save(contact_path)
    payload = {
        "schema_version": 1,
        "method": "PROJECTION_PAIRING_V2_REGION_WITH_TOLERANCE_DIRECTION_DISAMBIGUATION_V4",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "web_ui_updated": False,
        "components": rows,
        "contact_sheet": contact_path.name,
    }
    (output_dir / "projection_with_original_2d_review_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "# Projection Review V4\n\n"
        "The pairing_v2 component/DXF/local-region result is fixed. Symmetric shaft direction is resolved from tolerance-feature location before rendering. The right panel preserves the original 2D drawing review. No web or production database update is performed.\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--pairing-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    payload = run(args.config.resolve(), args.pairing_report.resolve(), args.output_dir.resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
