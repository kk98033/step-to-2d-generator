"""Read bounded administrator-facing summaries from CAD operation audit files."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

from auto_2d_drawing.config import OUTPUT_DIR


_OPERATION_ID = re.compile(r"^[A-Za-z0-9-]{8,80}$")


def _audit_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return []
    return root.glob("*/_users/*/_annotation_audit/*/*.json")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _find_operation_path(root: Path, operation_id: str) -> Optional[Path]:
    if not _OPERATION_ID.fullmatch(operation_id or ""):
        return None
    matches = list(root.glob(f"*/_users/*/_annotation_audit/*/*_{operation_id}.json"))
    return max(matches, key=lambda item: item.stat().st_mtime_ns) if matches else None


def _safe_diagnostic_path(root: Path, value: Any) -> Optional[Path]:
    if not value:
        return None
    try:
        candidate = Path(str(value)).resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def _read_gzip_json(path: Path) -> Dict[str, Any]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as source:
            payload = json.load(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _summary(path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    request = payload.get("request") or {}
    execution = payload.get("execution") or {}
    failure = payload.get("failure") or {}
    engineer = payload.get("engineer") or {}
    records = request.get("feature_records") or []
    outputs = execution.get("output_files") or {}
    return {
        "operation_id": payload.get("operation_id") or path.stem.rsplit("_", 1)[-1],
        "status": payload.get("status") or "unknown",
        "message": payload.get("message") or "",
        "created_at": payload.get("created_at"),
        "started_at": payload.get("started_at"),
        "completed_at": payload.get("completed_at"),
        "updated_at": payload.get("updated_at"),
        "engineer": engineer,
        "request_metadata": payload.get("request_metadata") or {},
        "model_id": request.get("model_id"),
        "part_id": request.get("part_id"),
        "candidate_engine": request.get("candidate_engine"),
        "include_half_section": bool(request.get("include_half_section")),
        "feature_record_count": len(records) if isinstance(records, list) else 0,
        "worker_pid": execution.get("worker_pid"),
        "worker_elapsed_seconds": execution.get("worker_elapsed_seconds"),
        "error_type": failure.get("error_type"),
        "error": failure.get("public_error") or failure.get("full_error"),
        "output_types": sorted(key for key, value in outputs.items() if value),
        "audit_size_bytes": path.stat().st_size,
    }


def list_operation_logs(
    *,
    limit: int = 200,
    status: Optional[str] = None,
    query: Optional[str] = None,
    output_root: Optional[str] = None,
) -> List[Dict[str, Any]]:
    root = Path(output_root or OUTPUT_DIR).resolve()
    bounded_limit = max(1, min(int(limit), 500))
    requested_status = (status or "").strip().lower()
    requested_query = (query or "").strip().lower()
    candidates = []
    for path in _audit_files(root):
        try:
            candidates.append((path.stat().st_mtime_ns, path))
        except OSError:
            continue
    candidates.sort(key=lambda item: item[0], reverse=True)

    results: List[Dict[str, Any]] = []
    for _, path in candidates[:2000]:
        payload = _read_json(path)
        if not payload:
            continue
        item = _summary(path, payload)
        if requested_status and str(item["status"]).lower() != requested_status:
            continue
        if requested_query:
            searchable = " ".join(
                str(value or "")
                for value in (
                    item.get("operation_id"),
                    item.get("message"),
                    item.get("model_id"),
                    item.get("part_id"),
                    item.get("error_type"),
                    item.get("error"),
                    json.dumps(item.get("request_metadata") or {}, ensure_ascii=False),
                    (item.get("engineer") or {}).get("display_name"),
                    (item.get("engineer") or {}).get("email"),
                )
            ).lower()
            if requested_query not in searchable:
                continue
        results.append(item)
        if len(results) >= bounded_limit:
            break
    return results


def _compact_feature_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "rule_id", "id", "category", "feature_type", "type", "enabled",
            "preferred_view", "views", "target_views", "side", "sides",
            "tolerance", "tolerance_config", "baseline", "offset",
        )
        if record.get(key) is not None
    }


def get_operation_log(operation_id: str, *, output_root: Optional[str] = None) -> Optional[Dict[str, Any]]:
    root = Path(output_root or OUTPUT_DIR).resolve()
    path = _find_operation_path(root, operation_id)
    if path is None:
        return None
    payload = _read_json(path)
    if not payload:
        return None

    request = dict(payload.get("request") or {})
    records = request.pop("feature_records", [])
    request["feature_record_count"] = len(records) if isinstance(records, list) else 0
    request["feature_records"] = [
        _compact_feature_record(record)
        for record in (records[:250] if isinstance(records, list) else [])
        if isinstance(record, dict)
    ]
    request["feature_records_truncated"] = isinstance(records, list) and len(records) > 250
    payload["request"] = request
    payload["audit_size_bytes"] = path.stat().st_size
    payload["model_binary_included"] = False
    return payload


def load_operation_replay_source(
    operation_id: str,
    *,
    output_root: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Load internal replay material while constraining diagnostics to OUTPUT_DIR."""
    root = Path(output_root or OUTPUT_DIR).resolve()
    audit_path = _find_operation_path(root, operation_id)
    if audit_path is None:
        return None
    audit = _read_json(audit_path)
    if not audit:
        return None
    execution = audit.get("execution") or {}
    diagnostics = execution.get("diagnostic_files") or {}
    snapshot_path = _safe_diagnostic_path(root, diagnostics.get("replay_snapshot"))
    snapshot = _read_gzip_json(snapshot_path) if snapshot_path else {}
    worker_request = snapshot.get("worker_request") if isinstance(snapshot.get("worker_request"), dict) else None
    if worker_request is None:
        worker_path = _safe_diagnostic_path(root, diagnostics.get("worker_request"))
        worker_request = _read_json(worker_path) if worker_path else {}
    return {
        "audit_path": audit_path,
        "audit": audit,
        "snapshot": snapshot,
        "worker_request": worker_request or {},
    }


def get_operation_reproduction(
    operation_id: str,
    *,
    output_root: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    root = Path(output_root or OUTPUT_DIR).resolve()
    source = load_operation_replay_source(operation_id, output_root=str(root))
    if not source:
        return None
    audit = source["audit"]
    snapshot = source["snapshot"]
    worker_request = source["worker_request"]
    request = audit.get("request") or {}
    model_id = str(request.get("model_id") or "")
    part_id = str(request.get("part_id") or worker_request.get("part_id") or "")
    records = worker_request.get("feature_records")
    if not isinstance(records, list):
        records = request.get("feature_records") if isinstance(request.get("feature_records"), list) else []
    enabled_count = sum(bool(record.get("enabled")) for record in records if isinstance(record, dict))
    stl_path = root / model_id / "_parts" / f"{part_id}.stl"
    model_preview_url = (
        f"/api/files/{quote(model_id, safe='')}/_parts/{quote(part_id, safe='')}.stl"
        if stl_path.is_file()
        else None
    )
    reproduction = audit.get("reproduction") or {}
    model_fingerprint = snapshot.get("model") or reproduction.get("model") or {}
    runtime = snapshot.get("runtime") or reproduction.get("runtime") or {}
    step_path = _safe_diagnostic_path(root, worker_request.get("step_path"))
    if step_path and not model_fingerprint:
        stat = step_path.stat()
        model_fingerprint = {
            "sha256": _sha256(step_path),
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "fingerprint_derived_from_current_file": True,
        }
    replay_available = bool(worker_request and model_id and part_id and step_path)
    return {
        "operation_id": operation_id,
        "status": audit.get("status"),
        "message": audit.get("message"),
        "created_at": audit.get("created_at"),
        "completed_at": audit.get("completed_at"),
        "engineer": audit.get("engineer") or {},
        "model": {
            "model_id": model_id,
            "part_id": part_id,
            "preview_url": model_preview_url,
            **model_fingerprint,
        },
        "request": {
            "candidate_engine": request.get("candidate_engine") or worker_request.get("candidate_engine") or "legacy_smart",
            "include_half_section": bool(request.get("include_half_section") or worker_request.get("include_half_section")),
            "part_category": request.get("part_category"),
            "product_family": request.get("product_family"),
            "title_info": worker_request.get("title_info") or request.get("title_info") or {},
            "storage_tag_ids": request.get("storage_tag_ids") or [],
            "request_hash": request.get("request_hash"),
            "feature_record_count": len(records),
            "enabled_feature_record_count": enabled_count,
            "feature_records": records,
        },
        "runtime": runtime,
        "events": audit.get("events") or [],
        "failure": audit.get("failure"),
        "output_files": (audit.get("execution") or {}).get("output_files") or {},
        "source_operation_id": (audit.get("reproduction") or {}).get("source_operation_id"),
        "replay_available": replay_available,
        "model_binary_included": False,
    }
