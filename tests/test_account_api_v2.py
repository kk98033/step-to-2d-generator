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
        "confirm_password": "Engineer-Changed-Password-2026!",
    })
    assert changed.status_code == 200

    second_login = authenticated.post("/api/auth/login", json={
        "email_local": "api.engineer",
        "email_domain": "forcecon-group.com",
        "password": "Engineer-Changed-Password-2026!",
    })
    assert second_login.status_code == 200
    blocked_change = authenticated.put("/api/auth/password", json={
        "current_password": "Engineer-Changed-Password-2026!",
        "new_password": "Engineer-Second-Password-2026!",
        "confirm_password": "Engineer-Second-Password-2026!",
    })
    assert blocked_change.status_code == 403


def test_admin_manages_company_domains_and_import_templates():
    authenticated = admin_client()
    created = authenticated.post("/api/admin/company-email-domains", json={
        "domain": "forcecon-lab.com",
    })
    assert created.status_code == 201
    assert "forcecon-lab.com" in authenticated.get("/api/auth/config").json()["company_email_domains"]

    renamed = authenticated.put("/api/admin/company-email-domains/forcecon-lab.com", json={
        "domain": "forcecon-cad.com",
    })
    assert renamed.status_code == 200
    deleted = authenticated.delete("/api/admin/company-email-domains/forcecon-cad.com")
    assert deleted.status_code == 200

    csv_template = authenticated.get("/api/admin/users/import-template?format=csv")
    assert csv_template.status_code == 200
    assert "text/csv" in csv_template.headers["content-type"]
    assert b"email,display_name,role" in csv_template.content
    xlsx_template = authenticated.get("/api/admin/users/import-template?format=xlsx")
    assert xlsx_template.status_code == 200
    assert xlsx_template.content.startswith(b"PK")


def test_admin_updates_cad_concurrency_limits():
    authenticated = admin_client()
    initial = authenticated.get("/api/admin/settings/cad-concurrency")
    assert initial.status_code == 200
    hard_limit = initial.json()["hard_limit"]
    selected = min(2, hard_limit)
    updated = authenticated.put("/api/admin/settings/cad-concurrency", json={
        "analysis_max_concurrency": selected,
        "annotation_max_concurrency": selected,
    })
    assert updated.status_code == 200
    assert updated.json()["analysis_max_concurrency"] == selected
    assert updated.json()["annotation_max_concurrency"] == selected
    rejected = authenticated.put("/api/admin/settings/cad-concurrency", json={
        "analysis_max_concurrency": hard_limit + 1,
        "annotation_max_concurrency": 1,
    })
    assert rejected.status_code in {400, 422}


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


def test_bulk_import_skips_unknown_domains_then_admin_resolves_each_domain():
    authenticated = admin_client()
    csv_content = "\n".join([
        "email,display_name,role",
        "partial.valid@forcecon.com.tw,Partial Valid,ENGINEER",
        "partial.typo@forcecon.con.tw,Partial Typo,ENGINEER",
        "partial.partner@partner-forcecon.com,Partial Partner,ENGINEER",
        "missing.name@forcecon.com.tw,,ENGINEER",
    ])
    imported = authenticated.post(
        "/api/admin/users/import",
        files={"file": ("accounts.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert imported.status_code == 201
    result = imported.json()
    assert result["status"] == "review_required"
    assert result["created_count"] == 1
    assert len(result["unresolved_domain_rows"]) == 2
    assert len(result["rejected_rows"]) == 1

    resolved = authenticated.post(
        "/api/admin/users/import/resolve-domains",
        json={
            "rows": result["unresolved_domain_rows"],
            "resolutions": {
                "forcecon.con.tw": "forcecon.com.tw",
                "partner-forcecon.com": "__add__",
            },
        },
    )
    assert resolved.status_code == 201
    resolved_result = resolved.json()
    assert resolved_result["created_count"] == 2
    assert resolved_result["rejected_rows"] == []
    assert resolved_result["domains_added"] == ["partner-forcecon.com"]
    assert {item["email"] for item in resolved_result["users"]} == {
        "partial.typo@forcecon.com.tw",
        "partial.partner@partner-forcecon.com",
    }
