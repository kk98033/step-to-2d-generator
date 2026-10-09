import os
import tempfile
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

_tempdir = tempfile.TemporaryDirectory()
os.environ["CAD_DATABASE_URL"] = f"sqlite:///{(Path(_tempdir.name) / 'account-api.db').as_posix()}"
os.environ["CAD_COMPANY_EMAIL_DOMAINS"] = "forcecon.com.tw,forcecon-group.com"
os.environ["CAD_EMAIL_DELIVERY_API_KEY"] = "test-email-delivery-key"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from web_app.backend.account_api import AuthenticationMiddleware, router, store


app = FastAPI()
app.add_middleware(AuthenticationMiddleware)
app.include_router(router)
client = TestClient(app)


def teardown_module():
    client.close()
    store.close()
    _tempdir.cleanup()


def admin_client() -> TestClient:
    response = client.post("/api/auth/login", json={
        "email_local": "admin",
        "email_domain": "forcecon.com.tw",
        "password": "ForceconAdmin!2026",
    })
    assert response.status_code == 200
    return client


def test_auth_config_and_domain_login_cookie():
    config = client.get("/api/auth/config")
    assert config.status_code == 200
    assert config.json()["company_email_domains"] == ["forcecon.com.tw", "forcecon-group.com"]
    response = admin_client().get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "admin@forcecon.com.tw"
    assert response.json()["user"]["must_change_password"] is False
    assert client.cookies.get("cad_email_domain") == "forcecon.com.tw"


def test_admin_account_creation_password_visibility_and_reset_policy():
    authenticated = admin_client()
    created = authenticated.post("/api/admin/users", json={
        "email": "api.engineer@forcecon-group.com",
        "display_name": "API Engineer",
        "role": "ENGINEER",
    })
    assert created.status_code == 201
    password = created.json()["user"]["initial_password"]
    assert password
    user_id = created.json()["user"]["id"]

    invalid = authenticated.post("/api/admin/users", json={
        "email": "outsider@example.com", "display_name": "Outsider",
    })
    assert invalid.status_code == 409

    reset = authenticated.post(f"/api/admin/users/{user_id}/reset-password", json={})
    assert reset.status_code == 200
    assert reset.json()["initial_password"] != password

    authenticated.post("/api/auth/logout")
    engineer_login = authenticated.post("/api/auth/login", json={
        "email_local": "api.engineer",
        "email_domain": "forcecon-group.com",
        "password": reset.json()["initial_password"],
    })
    assert engineer_login.status_code == 200
    forbidden = authenticated.post(f"/api/admin/users/{user_id}/reset-password", json={})
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "PASSWORD_CHANGE_REQUIRED"
    changed = authenticated.put("/api/auth/password", json={
        "current_password": reset.json()["initial_password"],
        "new_password": "Engineer-Changed-Password-2026!",
    })
    assert changed.status_code == 200


def test_excel_bulk_import_and_email_delivery_contract():
    authenticated = admin_client()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["email", "display_name", "role"])
    sheet.append(["batch.one@forcecon.com.tw", "Batch One", "ENGINEER"])
    sheet.append(["batch.two@forcecon-group.com", "Batch Two", "ENGINEER"])
    content = BytesIO()
    workbook.save(content)
    response = authenticated.post(
        "/api/admin/users/import",
        files={"file": ("accounts.xlsx", content.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 201
    assert response.json()["created_count"] == 2
    assert all(item["initial_password"] for item in response.json()["users"])

    authenticated.post("/api/auth/logout")
    pending = authenticated.get(
        "/api/integrations/email/credential-notifications",
        headers={"X-API-Key": "test-email-delivery-key"},
    )
    assert pending.status_code == 200
    assert len(pending.json()["notifications"]) >= 2
    first = pending.json()["notifications"][0]
    acknowledged = authenticated.post(
        f"/api/integrations/email/credential-notifications/{first['id']}/ack",
        headers={"X-API-Key": "test-email-delivery-key"},
        json={"status": "SENT", "provider_message_id": "mail-001"},
    )
    assert acknowledged.status_code == 200
