"""Authentication, engineer settings, artifact, and administrator APIs."""

from __future__ import annotations

import os
import secrets
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from web_app.backend.identity_store import CurrentUser, IdentityStore


SESSION_COOKIE = "cad_session"
AUTH_EXEMPT_PREFIXES = (
    "/api/health",
    "/api/auth/login",
)


store = IdentityStore()
router = APIRouter(prefix="/api", tags=["accounts"])


def _request_token(request: Request) -> Optional[str]:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


def require_user(request: Request) -> CurrentUser:
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator permission required")
    return user


def assert_model_access(user: CurrentUser, model_id: str) -> None:
    if not store.can_access_model(user, model_id):
        # Return 404 to avoid leaking another engineer's model identifiers.
        raise HTTPException(status_code=404, detail="Model not found")


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Require a revocable session for API and protected generated files."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        token = _request_token(request)
        user = store.user_for_token(token)
        request.state.current_user = user

        is_api = path == "/api" or path.startswith("/api/")
        exempt = any(path.startswith(prefix) for prefix in AUTH_EXEMPT_PREFIXES)
        configured_api_key = os.environ.get("CAD_EXTERNAL_PREDICTION_API_KEY", "")
        external_api_authorized = (
            path.startswith("/api/tolerance/external-predictions")
            and bool(configured_api_key)
            and secrets.compare_digest(
                request.headers.get("X-API-Key", ""), configured_api_key
            )
        )
        if is_api and not exempt and not user and not external_api_authorized:
            return JSONResponse({"detail": "Authentication required"}, status_code=401)

        admin_only_reference = (
            path == "/api/examples"
            or path.startswith("/api/examples/")
            or path == "/api/processed/fan-20260625"
        )
        if admin_only_reference and user and not user.is_admin:
            return JSONResponse(
                {"detail": "Administrator permission required"},
                status_code=403,
            )

        if user and path.startswith("/api/files/"):
            remainder = path.removeprefix("/api/files/")
            model_id = remainder.split("/", 1)[0]
            if model_id and not store.can_access_model(user, model_id):
                return JSONResponse({"detail": "File not found"}, status_code=404)

        return await call_next(request)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    email: Optional[str] = Field(default=None, max_length=255)
    role: str = "ENGINEER"
    password: str = Field(min_length=10, max_length=256)


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=10, max_length=256)


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response):
    user = store.authenticate(payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    client_ip = request.client.host if request.client else None
    token, expires_at = store.create_session(
        user.id,
        client_ip=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=os.environ.get("CAD_COOKIE_SECURE", "0") == "1",
        samesite="strict",
        max_age=12 * 60 * 60,
        path="/",
    )
    return {"status": "ok", "user": user.as_dict(), "expires_at": expires_at.isoformat()}


@router.post("/auth/logout")
def logout(request: Request, response: Response, user: CurrentUser = Depends(require_user)):
    store.revoke_session(_request_token(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@router.get("/auth/me")
def me(user: CurrentUser = Depends(require_user)):
    return {"status": "ok", "user": user.as_dict()}


@router.put("/auth/password")
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    user: CurrentUser = Depends(require_user),
):
    try:
        store.change_password(user.id, payload.current_password, payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store.revoke_session(_request_token(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok", "reauthentication_required": True}


@router.get("/engineer/preferences")
def get_preferences(user: CurrentUser = Depends(require_user)):
    return {"status": "ok", "preferences": store.get_preferences(user.id)}


@router.put("/engineer/preferences")
def put_preferences(
    payload: Dict[str, Any] = Body(...),
    user: CurrentUser = Depends(require_user),
):
    try:
        preferences = store.update_preferences(user.id, payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "preferences": preferences}


@router.delete("/engineer/preferences")
def reset_preferences(user: CurrentUser = Depends(require_user)):
    return {
        "status": "ok",
        "preferences": store.reset_preferences(user.id),
    }


@router.get("/engineer/artifacts")
def list_artifacts(
    owner_user_id: Optional[str] = None,
    user: CurrentUser = Depends(require_user),
):
    return {"status": "ok", "artifacts": store.list_artifacts(user, owner_user_id)}


@router.delete("/engineer/artifacts/{artifact_id}")
def delete_artifact(artifact_id: str, user: CurrentUser = Depends(require_user)):
    try:
        found = store.delete_artifact(user, artifact_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not found:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"status": "ok"}


@router.get("/engineer/tolerance-cases")
def list_personal_tolerance_cases(user: CurrentUser = Depends(require_user)):
    return {"status": "ok", "cases": store.personal_cases(user.id)}


@router.delete("/engineer/tolerance-cases")
def clear_personal_tolerance_cases(user: CurrentUser = Depends(require_user)):
    deleted_count = store.clear_personal_cases(user.id)
    return {"status": "ok", "deleted_count": deleted_count}


@router.delete("/engineer/tolerance-cases/{case_record_id}")
def delete_personal_tolerance_case(
    case_record_id: str,
    user: CurrentUser = Depends(require_user),
):
    if not store.delete_personal_case(user.id, case_record_id):
        raise HTTPException(status_code=404, detail="Personal tolerance case not found")
    return {"status": "ok"}


@router.get("/admin/users")
def admin_list_users(admin: CurrentUser = Depends(require_admin)):
    return {"status": "ok", "users": store.list_users()}


@router.post("/admin/users", status_code=201)
def admin_create_user(
    payload: UserCreateRequest,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        user = store.create_user(payload.model_dump(), admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "user": user}


@router.patch("/admin/users/{user_id}")
def admin_update_user(
    user_id: str,
    payload: Dict[str, Any] = Body(...),
    admin: CurrentUser = Depends(require_admin),
):
    if user_id == admin.id and payload.get("is_active") is False:
        raise HTTPException(status_code=400, detail="Administrator cannot disable the active account")
    try:
        user = store.update_user(user_id, payload, admin.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "user": user}


@router.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: str,
    payload: PasswordResetRequest,
    admin: CurrentUser = Depends(require_admin),
):
    try:
        store.reset_password(user_id, payload.new_password, admin.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "must_change_password": True}


@router.get("/admin/audit-events")
def admin_audit_events(limit: int = 200, admin: CurrentUser = Depends(require_admin)):
    return {"status": "ok", "events": store.list_audit_events(limit)}
