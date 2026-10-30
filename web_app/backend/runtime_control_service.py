"""Persistent administrator control state for the single-host CAD backend."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import threading
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint32), ("memory_load", ctypes.c_uint32),
        ("total_physical", ctypes.c_uint64), ("available_physical", ctypes.c_uint64),
        ("total_page_file", ctypes.c_uint64), ("available_page_file", ctypes.c_uint64),
        ("total_virtual", ctypes.c_uint64), ("available_virtual", ctypes.c_uint64),
        ("available_extended_virtual", ctypes.c_uint64),
    ]


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32), ("page_fault_count", ctypes.c_uint32),
        ("peak_working_set_size", ctypes.c_size_t), ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t), ("peak_pagefile_usage", ctypes.c_size_t),
        ("private_usage", ctypes.c_size_t),
    ]


class _SystemResourceSampler:
    """Low-overhead Windows host/process sampler using native counters only."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_cpu: tuple[int, int] | None = None

    @staticmethod
    def _ticks(value: _FileTime) -> int:
        return (int(value.high) << 32) | int(value.low)

    def sample(self, disk_path: Path) -> dict[str, Any]:
        disk = shutil.disk_usage(disk_path)
        result: dict[str, Any] = {
            "sampled_at": _utc_iso(),
            "cpu_percent": None,
            "memory_percent": None,
            "memory_total_bytes": None,
            "memory_available_bytes": None,
            "process_working_set_bytes": None,
            "process_private_bytes": None,
            "disk_total_bytes": int(disk.total),
            "disk_free_bytes": int(disk.free),
            "disk_percent": round(100.0 * (disk.total - disk.free) / disk.total, 3) if disk.total else None,
        }
        if os.name != "nt":
            return result
        with self._lock:
            idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
            if ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            ):
                idle_ticks = self._ticks(idle)
                total_ticks = self._ticks(kernel) + self._ticks(user)
                if self._last_cpu is not None:
                    idle_delta = idle_ticks - self._last_cpu[0]
                    total_delta = total_ticks - self._last_cpu[1]
                    if total_delta > 0:
                        result["cpu_percent"] = round(
                            max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta)), 3
                        )
                self._last_cpu = (idle_ticks, total_ticks)

            memory = _MemoryStatusEx()
            memory.length = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
                result.update({
                    "memory_percent": float(memory.memory_load),
                    "memory_total_bytes": int(memory.total_physical),
                    "memory_available_bytes": int(memory.available_physical),
                })

            counters = _ProcessMemoryCountersEx()
            counters.cb = ctypes.sizeof(_ProcessMemoryCountersEx)
            get_current_process = ctypes.windll.kernel32.GetCurrentProcess
            get_current_process.restype = ctypes.c_void_p
            get_process_memory = ctypes.windll.psapi.GetProcessMemoryInfo
            get_process_memory.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_ProcessMemoryCountersEx),
                ctypes.c_uint32,
            ]
            get_process_memory.restype = ctypes.c_int
            if get_process_memory(
                get_current_process(), ctypes.byref(counters), counters.cb
            ):
                result.update({
                    "process_working_set_bytes": int(counters.working_set_size),
                    "process_private_bytes": int(counters.private_usage),
                })
        return result


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.025 * (2 ** attempt))


class RuntimeControlService:
    def __init__(self, output_root: str | Path):
        self._lock = threading.RLock()
        self._started_monotonic = time.monotonic()
        self.started_at = _utc_iso()
        self.state_path = Path(output_root).resolve() / "_system" / "runtime_control.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.supervisor_url = str(os.environ.get("CAD_BACKEND_SUPERVISOR_URL") or "").strip()
        self.supervisor_token = str(os.environ.get("CAD_BACKEND_SUPERVISOR_TOKEN") or "").strip()
        self._resource_sampler = _SystemResourceSampler()

    def _load(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"maintenance_mode": False, "reason": "", "updated_at": None, "updated_by": None}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {"maintenance_mode": False, "reason": "", "updated_at": None, "updated_by": None}

    @property
    def maintenance_mode(self) -> bool:
        with self._lock:
            return bool(self._load().get("maintenance_mode"))

    def set_maintenance(self, enabled: bool, actor_user_id: str, reason: str = "") -> dict[str, Any]:
        with self._lock:
            state = {
                "maintenance_mode": bool(enabled),
                "reason": str(reason or "").strip()[:500],
                "updated_at": _utc_iso(),
                "updated_by": actor_user_id,
            }
            _atomic_json(self.state_path, state)
            return state

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self._load()
        resources = self._resource_sampler.sample(self.state_path.parent)
        return {
            **state,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "uptime_seconds": round(time.monotonic() - self._started_monotonic, 3),
            "disk_free_bytes": resources["disk_free_bytes"],
            "resources": resources,
            "process_control": {
                "supervisor_available": bool(self.supervisor_url and self.supervisor_token),
                "supervisor_url": self.supervisor_url or None,
                "restart_available": bool(self.supervisor_url and self.supervisor_token),
                "reason": (
                    "尚未連接外部 supervisor；後端不能安全地停止自己後再由同一個 API 啟動。"
                    if not (self.supervisor_url and self.supervisor_token) else
                    "後端由本機 supervisor 管理，可安全地執行程序重啟。"
                ),
            },
        }

    def request_restart(self) -> dict[str, Any]:
        if not self.supervisor_url or not self.supervisor_token:
            raise RuntimeError("尚未安裝或連接後端 supervisor")
        request = urllib.request.Request(
            f"{self.supervisor_url.rstrip('/')}/restart",
            data=b"{}",
            method="POST",
            headers={
                "Authorization": f"Bearer {self.supervisor_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Supervisor restart request failed: {exc}") from exc
        return payload if isinstance(payload, dict) else {"status": "accepted"}
