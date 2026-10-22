"""Read bounded administrator-facing summaries from CAD operation audit files."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

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
    if not _OPERATION_ID.fullmatch(operation_id or ""):
        return None
    root = Path(output_root or OUTPUT_DIR).resolve()
    matches = list(root.glob(f"*/_users/*/_annotation_audit/*/*_{operation_id}.json"))
    if not matches:
        return None
    path = max(matches, key=lambda item: item.stat().st_mtime_ns)
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
