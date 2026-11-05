"""Exercise the actual auth middleware/router without an optional HTTP client."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from http.cookies import SimpleCookie
from unittest.mock import patch

from fastapi import FastAPI
from sqlalchemy import update

from web_app.backend import account_api
from web_app.backend.identity_store import IdentityStore
from web_app.backend.presence_service import PresenceService
from web_app.backend.request_audit import audit_context, safe_details


class RequestAuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database_url = f"sqlite:///{(Path(self.directory.name) / 'audit.db').as_posix()}"
        self.store = IdentityStore(self.database_url)
        self.addCleanup(self.store.close)
        self.addCleanup(patch.stopall)
        patch.object(account_api, "store", self.store).start()
        self.presence = PresenceService()
        patch.object(account_api, "presence_service", self.presence).start()
        self.app = FastAPI()
        self.app.include_router(account_api.router)
        self.app.add_middleware(account_api.AuthenticationMiddleware)

        @self.app.post("/api/audit-probe")
        def probe():
            # This executes in FastAPI's sync thread pool.
            self.store.record_request_event(None, "DOMAIN_PROBE", {"password": "do-not-save"})
            return {"ok": True}

        @self.app.post("/api/crash-probe")
        def crash():
            raise RuntimeError("private error detail")

    async def request(self, path, *, method="POST", body=None, token=None, ip="192.0.2.17"):
        payload = json.dumps(body or {}).encode()
        headers = [(b"content-type", b"application/json"), (b"user-agent", b"AuditTest/1.0"),
                   (b"x-forwarded-for", b"203.0.113.99"), (b"x-request-id", b"forged-id")]
        if token:
            headers.append((b"cookie", f"cad_session={token}".encode()))
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": b"password=must-not-log",
                 "root_path": "", "headers": headers, "client": (ip, 12345), "server": ("test", 80)}
        sent = []
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        await self.app(scope, receive, send)
        start = next(item for item in sent if item["type"] == "http.response.start")
        raw = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
        headers = dict(start["headers"])
        for key, value in start["headers"]:
            if key == b"set-cookie" and value.startswith(b"cad_session="):
                headers[key] = value
        return start["status"], headers, json.loads(raw) if raw else {}

    async def login(self, password="ForceconAdmin!2026"):
        return await self.request("/api/auth/login", body={"username": "admin", "password": password})

    def events(self, action):
        return [item for item in self.store.list_audit_events(1000) if item["action"] == action]

    async def test_login_failure_success_logout_are_durable_and_private(self):
        status, _, _ = await self.login("wrong-secret")
        self.assertEqual(status, 401)
        failed = self.events("LOGIN_FAILED")[0]["details"]
        self.assertIn("admin@", failed["attempted_identifier"])
        status, headers, _ = await self.login()
        self.assertEqual(status, 200)
        event = self.events("LOGIN")[0]
        info = event["details"]
        self.assertEqual(info["client_ip"], "192.0.2.17")
        self.assertEqual(info["user_agent"], "AuditTest/1.0")
        self.assertEqual(info["request_id"], headers[b"x-request-id"].decode())
        self.assertNotEqual(info["request_id"], "forged-id")
        self.assertEqual(info["actor_role"], "ADMIN")
        self.assertTrue(event["created_at"].endswith("+00:00"))
        self.assertGreaterEqual(info["duration_ms"], 0)
        cookie = SimpleCookie()
        cookie.load(headers[b"set-cookie"].decode())
        token = cookie["cad_session"].value
        status, _, _ = await self.request("/api/auth/logout", token=token)
        self.assertEqual(status, 200)
        self.assertIsNone(self.store.user_for_token(token))
        self.assertEqual(len(self.events("LOGOUT")), 1)
        self.assertEqual(self.events("LOGOUT")[0]["details"]["session_id"], info["session_id"])
        persisted = IdentityStore(self.database_url)
        try:
            serialized = json.dumps(persisted.list_audit_events(1000))
            self.assertIn('"LOGIN_FAILED"', serialized)
            for secret in ("wrong-secret", "ForceconAdmin!2026", token, "must-not-log"):
                self.assertNotIn(secret, serialized)
        finally:
            persisted.close()
        self.assertIsNone(audit_context.get())

    async def test_expiry_recorded_once_on_next_request(self):
        admin = self.store.authenticate("admin", "ForceconAdmin!2026")
        token, _ = self.store.create_session(admin.id, hours=-1, client_ip="192.0.2.10")
        for _ in range(2):
            status, _, _ = await self.request("/api/auth/me", method="GET", token=token)
            self.assertEqual(status, 401)
        events = self.events("SESSION_EXPIRED")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor_user_id"], admin.id)
        self.assertEqual(events[0]["details"]["request"]["client_ip"], "192.0.2.17")
        self.assertEqual(events[0]["details"]["login_ip"], "192.0.2.10")

    async def test_context_crosses_sync_thread_and_heartbeat_is_not_written(self):
        admin = self.store.authenticate("admin", "ForceconAdmin!2026")
        token, _ = self.store.create_session(admin.id)
        status, _, _ = await self.request("/api/audit-probe", token=token)
        self.assertEqual(status, 200)
        domain = self.events("DOMAIN_PROBE")[0]["details"]
        self.assertEqual(domain["request"]["client_ip"], "192.0.2.17")
        self.assertEqual(domain["password"], "[REDACTED]")
        before = len(self.store.list_audit_events(1000))
        status, _, _ = await self.request("/api/presence/heartbeat", token=token, body={"page": "/", "idle": False})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.store.list_audit_events(1000)), before)
        self.assertEqual(self.presence.snapshot()["users"][0]["client_ip"], "192.0.2.17")

    async def test_denied_and_unhandled_failure_are_recorded(self):
        created = self.store.create_user({"username": "engineer", "display_name": "Engineer", "password": "Engineer-Password"}, None)
        # A normal engineer session after the first-password step.
        from web_app.backend.identity_store import users
        with self.store.engine.begin() as conn:
            conn.execute(update(users).where(users.c.id == created["id"]).values(must_change_password=False))
        token, _ = self.store.create_session(created["id"])
        status, _, _ = await self.request("/api/admin/audit-events", method="GET", token=token)
        self.assertEqual(status, 403)
        self.assertEqual(len(self.events("ACCESS_DENIED")), 1)
        with self.assertRaises(Exception):
            await self.request("/api/crash-probe", token=token)
        self.assertEqual(self.events("API_OPERATION")[0]["details"]["status_code"], 500)
        self.assertIsNone(audit_context.get())

    def test_nested_credentials_are_redacted(self):
        self.assertEqual(safe_details({"nested": [{"new_password": "secret", "access_token": "secret"}]}),
                         {"nested": [{"new_password": "[REDACTED]", "access_token": "[REDACTED]"}]})

    def test_presence_retains_each_connection_ip_and_browser(self):
        admin = self.store.authenticate('admin', 'ForceconAdmin!2026')
        self.presence.heartbeat(admin, 'connection-one', page='/', idle=False,
                                client_ip='192.0.2.17', user_agent='BrowserOne')
        self.presence.heartbeat(admin, 'connection-two', page='/admin', idle=True,
                                client_ip='2001:db8::17', user_agent='BrowserTwo')
        user = self.presence.snapshot()['users'][0]
        self.assertEqual(user['status'], 'ONLINE')
        self.assertEqual(user['session_count'], 2)
        self.assertEqual({item['client_ip'] for item in user['connections']}, {'192.0.2.17', '2001:db8::17'})
        self.assertEqual({item['user_agent'] for item in user['connections']}, {'BrowserOne', 'BrowserTwo'})


if __name__ == "__main__":
    unittest.main()
