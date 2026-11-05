"""Bounded in-memory user presence for the single-host CAD application."""

from __future__ import annotations

import hashlib
import threading
import uuid
from collections import deque
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


class PresenceService:
    """Track active browser sessions without durable heartbeat writes."""

    def __init__(
        self,
        *,
        online_seconds: int = 90,
        recent_seconds: int = 15 * 60,
        trend_minutes: int = 60,
    ) -> None:
        self.online_seconds = max(30, online_seconds)
        self.recent_seconds = max(self.online_seconds, recent_seconds)
        self.trend_minutes = max(10, trend_minutes)
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._minute_users: dict[str, set[str]] = defaultdict(set)
        self._minute_active_users: dict[str, set[str]] = defaultdict(set)
        self._activities: deque[dict[str, Any]] = deque(maxlen=1000)

    @staticmethod
    def session_key(token: str | None, user_id: str) -> str:
        source = token or f"user:{user_id}"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]

    def heartbeat(
        self,
        user: Any,
        token: str | None,
        *,
        page: str,
        idle: bool,
        user_agent: str | None = None,
        client_ip: str | None = None,
    ) -> dict[str, Any]:
        now = _utcnow()
        key = self.session_key(token, str(user.id))
        safe_page = str(page or "/")[:240]
        with self._lock:
            existing = self._sessions.get(key) or {}
            self._sessions[key] = {
                "session_id": key,
                "user_id": str(user.id),
                "username": str(user.username),
                "display_name": str(user.display_name),
                "email": user.email,
                "role": str(user.role),
                "page": safe_page,
                "idle": bool(idle),
                "first_seen_at": existing.get("first_seen_at") or _iso(now),
                "last_seen_at": _iso(now),
                "user_agent": str(user_agent or "")[:240] or None,
                "client_ip": client_ip,
            }
            minute = now.replace(second=0, microsecond=0).isoformat()
            self._minute_users[minute].add(str(user.id))
            if not idle:
                self._minute_active_users[minute].add(str(user.id))
            self._prune_locked(now)
            if not existing:
                self._record_activity_locked(
                    user,
                    action="SESSION_ONLINE",
                    method="HEARTBEAT",
                    path=safe_page,
                    status_code=200,
                    at=now,
                    metadata={"client_ip": client_ip, "user_agent": str(user_agent or "")[:500]},
                )
            return dict(self._sessions[key])

    def remove(self, token: str | None, user_id: str) -> None:
        key = self.session_key(token, user_id)
        with self._lock:
            self._sessions.pop(key, None)

    def _record_activity_locked(
        self,
        user: Any,
        *,
        action: str,
        method: str,
        path: str,
        status_code: int,
        at: datetime,
        metadata: dict | None = None,
    ) -> None:
        self._activities.appendleft({
            "id": str(uuid.uuid4()),
            "at": _iso(at),
            "user_id": str(user.id),
            "display_name": str(user.display_name),
            "email": user.email,
            "role": str(user.role),
            "action": str(action)[:100],
            "method": str(method)[:12],
            "path": str(path)[:300],
            "status_code": int(status_code),
            "request": dict(metadata or {}),
        })

    def record_activity(
        self,
        user: Any,
        *,
        action: str,
        method: str,
        path: str,
        status_code: int,
        metadata: dict | None = None,
    ) -> None:
        with self._lock:
            self._record_activity_locked(
                user,
                action=action,
                method=method,
                path=path,
                status_code=status_code,
                at=_utcnow(),
                metadata=metadata,
            )

    def activities(self, limit: int = 300) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in list(self._activities)[:max(1, min(limit, 1000))]]

    def _prune_locked(self, now: datetime) -> None:
        recent_cutoff = now - timedelta(seconds=self.recent_seconds)
        self._sessions = {
            key: value for key, value in self._sessions.items()
            if datetime.fromisoformat(value["last_seen_at"]) >= recent_cutoff
        }
        trend_cutoff = now - timedelta(minutes=self.trend_minutes + 2)
        for collection in (self._minute_users, self._minute_active_users):
            for minute in list(collection):
                if datetime.fromisoformat(minute) < trend_cutoff:
                    collection.pop(minute, None)

    def snapshot(self) -> dict[str, Any]:
        now = _utcnow()
        online_cutoff = now - timedelta(seconds=self.online_seconds)
        with self._lock:
            self._prune_locked(now)
            by_user: dict[str, dict[str, Any]] = {}
            for session in self._sessions.values():
                user_id = session["user_id"]
                last_seen = datetime.fromisoformat(session["last_seen_at"])
                status = "OFFLINE" if last_seen < online_cutoff else ("IDLE" if session["idle"] else "ONLINE")
                current = by_user.get(user_id)
                if current is None:
                    by_user[user_id] = {**session, "status": status, "session_count": 1,
                                        "connections": [{**session, "status": status}]}
                    continue
                session_count = int(current["session_count"]) + 1
                connections = [*current["connections"], {**session, "status": status}]
                current_status = current["status"]
                newest = session["last_seen_at"] > current["last_seen_at"]
                if newest:
                    by_user[user_id] = {**session, "status": status, "session_count": session_count,
                                        "connections": connections}
                else:
                    current["session_count"] = session_count
                    current["connections"] = connections
                # One active tab means the engineer is active even if a newer tab is idle.
                if status == "ONLINE" or current_status == "ONLINE":
                    by_user[user_id]["status"] = "ONLINE"
                elif status == "IDLE" or current_status == "IDLE":
                    by_user[user_id]["status"] = "IDLE"

            users = sorted(by_user.values(), key=lambda item: item["last_seen_at"], reverse=True)
            trend = []
            for offset in range(self.trend_minutes - 1, -1, -1):
                minute_dt = (now - timedelta(minutes=offset)).replace(second=0, microsecond=0)
                minute = minute_dt.isoformat()
                trend.append({
                    "minute": minute,
                    "seen_users": len(self._minute_users.get(minute, set())),
                    "active_users": len(self._minute_active_users.get(minute, set())),
                })
        return {
            "sampled_at": _iso(now),
            "online_timeout_seconds": self.online_seconds,
            "users": users,
            "summary": {
                "online": sum(item["status"] == "ONLINE" for item in users),
                "idle": sum(item["status"] == "IDLE" for item in users),
                "recently_offline": sum(item["status"] == "OFFLINE" for item in users),
                "sessions": sum(int(item["session_count"]) for item in users),
            },
            "trend": trend,
        }


presence_service = PresenceService()
