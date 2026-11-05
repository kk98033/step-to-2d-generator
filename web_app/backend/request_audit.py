"""Request-scoped audit metadata; never collect credentials or request bodies."""

from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import uuid

from starlette.requests import Request


audit_context: ContextVar[dict | None] = ContextVar("audit_context", default=None)


def request_metadata(request: Request, token: str | None = None) -> dict:
    # ASGI client is resolved by the server's trusted-proxy configuration.
    # Do not independently trust user-provided Forwarded/X-Forwarded-For headers.
    return {
        "request_id": str(uuid.uuid4()),
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "client_ip": request.client.host if request.client else None,
        "client_port": request.client.port if request.client else None,
        "scheme": request.url.scheme,
        "http_version": request.scope.get("http_version"),
        "user_agent": request.headers.get("user-agent", "")[:500],
        "method": request.method,
        "path": request.url.path[:500],
        "session_id": hashlib.sha256(token.encode()).hexdigest()[:24] if token else None,
    }


def safe_details(value):
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if any(
                part in str(key).lower() for part in ("password", "token", "secret", "cookie", "authorization")
            ) else safe_details(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe_details(item) for item in value]
    return value
