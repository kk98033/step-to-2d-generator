"""Loopback-only supervisor for safely restarting the CAD FastAPI process.

Run this script instead of launching uvicorn directly in a company deployment.
The child backend receives the supervisor URL and a per-launch secret through
environment variables; browser clients never receive that secret.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


class BackendSupervisor:
    def __init__(self, host: str, port: int, backend_host: str, backend_port: int):
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("Supervisor must bind to loopback only")
        self.host = host
        self.port = port
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: Any = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            return {
                "status": "ok", "backend_running": running,
                "backend_pid": self._process.pid if running else None,
                "backend_port": self.backend_port,
            }

    def start_backend(self) -> dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self.status()
            runtime_dir = REPO_ROOT / ".codex_runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._log_handle = (runtime_dir / f"supervised_backend_{stamp}.log").open("ab")
            environment = os.environ.copy()
            environment["CAD_BACKEND_SUPERVISOR_URL"] = f"http://{self.host}:{self.port}"
            environment["CAD_BACKEND_SUPERVISOR_TOKEN"] = self.token
            self._process = subprocess.Popen(
                [
                    sys.executable, "-m", "uvicorn", "web_app.backend.server:app",
                    "--host", self.backend_host, "--port", str(self.backend_port),
                ],
                cwd=REPO_ROOT,
                env=environment,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
            )
            return self.status()

    def stop_backend(self) -> None:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                if os.name == "nt":
                    completed = subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        timeout=20,
                    )
                    if completed.returncode != 0 and process.poll() is None:
                        process.terminate()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self._process = None
            if self._log_handle is not None:
                self._log_handle.close()
                self._log_handle = None

    def restart_after_response(self) -> None:
        time.sleep(0.5)
        self.stop_backend()
        time.sleep(0.5)
        self.start_backend()


def handler_for(supervisor: BackendSupervisor):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CADBackendSupervisor/1.0"

        def _authorized(self) -> bool:
            return secrets.compare_digest(
                self.headers.get("Authorization", ""), f"Bearer {supervisor.token}"
            )

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            if not self._authorized():
                self._send(401, {"detail": "Unauthorized"})
                return
            if self.path != "/health":
                self._send(404, {"detail": "Not found"})
                return
            self._send(200, supervisor.status())

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            if not self._authorized():
                self._send(401, {"detail": "Unauthorized"})
                return
            if self.path != "/restart":
                self._send(404, {"detail": "Not found"})
                return
            threading.Thread(target=supervisor.restart_after_response, daemon=True).start()
            self._send(202, {"status": "accepted", "action": "restart"})

        def log_message(self, format_string: str, *args: Any) -> None:
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--backend-host", default="0.0.0.0")
    parser.add_argument("--backend-port", type=int, default=8000)
    args = parser.parse_args()
    supervisor = BackendSupervisor(args.host, args.port, args.backend_host, args.backend_port)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(supervisor))
    supervisor.start_backend()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        supervisor.stop_backend()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
