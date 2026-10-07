"""Persistent identity, ownership, and engineer-personalization storage.

The application uses SQLite for a zero-configuration workstation install and
PostgreSQL in company deployments.  SQLAlchemy Core keeps the schema and query
semantics identical in both environments while avoiding coupling CAD modules
to a specific database driver.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    delete,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Engine


UTC = timezone.utc
PASSWORD_ITERATIONS = 600_000
DEFAULT_SESSION_HOURS = 12


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CurrentUser:
    id: str
    username: str
    display_name: str
    email: Optional[str]
    role: str
    is_active: bool
    must_change_password: bool

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "email": self.email,
            "role": self.role,
            "is_active": self.is_active,
            "must_change_password": self.must_change_password,
        }


metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(80), nullable=False, unique=True),
    Column("display_name", String(120), nullable=False),
    Column("email", String(255), nullable=True, unique=True),
    Column("role", String(20), nullable=False, default="ENGINEER"),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("must_change_password", Boolean, nullable=False, default=True),
    Column("password_hash", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("last_login_at", DateTime(timezone=True), nullable=True),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("client_ip", String(64), nullable=True),
    Column("user_agent", String(500), nullable=True),
)

engineer_preferences = Table(
    "engineer_preferences",
    metadata,
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("recommendation_mode", String(32), nullable=False, default="BALANCED"),
    Column("personal_case_weight", Integer, nullable=False, default=35),
    Column("personal_case_min_similarity", Integer, nullable=False, default=82),
    Column("default_tolerance_json", Text, nullable=False, default="{}"),
    Column("dimension_placement_json", Text, nullable=False, default="{}"),
    Column("annotation_style_json", Text, nullable=False, default="{}"),
    Column("ui_settings_json", Text, nullable=False, default="{}"),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

owned_models = Table(
    "owned_models",
    metadata,
    Column("model_id", String(255), primary_key=True),
    Column("owner_user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("source_filename", String(500), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

annotation_artifacts = Table(
    "annotation_artifacts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("owner_user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("model_id", String(255), nullable=False),
    Column("part_id", String(255), nullable=False),
    Column("title", String(255), nullable=False),
    Column("status", String(32), nullable=False, default="COMPLETED"),
    Column("input_snapshot_json", Text, nullable=False, default="{}"),
    Column("output_files_json", Text, nullable=False, default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

engineer_tolerance_cases = Table(
    "engineer_tolerance_cases",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("owner_user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("artifact_id", String(36), ForeignKey("annotation_artifacts.id", ondelete="CASCADE"), nullable=True),
    Column("case_id", String(160), nullable=False),
    Column("model_id", String(255), nullable=False),
    Column("part_id", String(255), nullable=False),
    Column("rule_id", String(255), nullable=False),
    Column("part_type", String(80), nullable=True),
    Column("product_family", String(80), nullable=True),
    Column("feature_type", String(80), nullable=False),
    Column("inferred_role", String(120), nullable=True),
    Column("nominal_json", Text, nullable=False),
    Column("tolerance_json", Text, nullable=False),
    Column("placement_json", Text, nullable=False, default="{}"),
    Column("geometry_json", Text, nullable=False, default="{}"),
    Column("is_enabled", Boolean, nullable=False, default=True),
    Column("use_count", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("owner_user_id", "case_id", name="uq_engineer_case_owner_case"),
)

engineer_templates = Table(
    "engineer_templates",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("owner_user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(160), nullable=False),
    Column("description", Text, nullable=True),
    Column("template_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("actor_user_id", String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("action", String(120), nullable=False),
    Column("target_type", String(80), nullable=True),
    Column("target_id", String(255), nullable=True),
    Column("details_json", Text, nullable=False, default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class IdentityStore:
    def __init__(self, database_url: Optional[str] = None):
        default_db = Path(__file__).resolve().parent / "data" / "forcecon_identity.db"
        default_db.parent.mkdir(parents=True, exist_ok=True)
        self.database_url = database_url or os.environ.get(
            "CAD_DATABASE_URL", f"sqlite:///{default_db.as_posix()}"
        )
        connect_args = {"check_same_thread": False} if self.database_url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(
            self.database_url,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        metadata.create_all(self.engine)
        self._bootstrap_admin()

    def close(self) -> None:
        """Release pooled database connections (primarily used by tests/tools)."""
        self.engine.dispose()

    @staticmethod
    def _row_user(row: Any) -> CurrentUser:
        mapping = row._mapping if hasattr(row, "_mapping") else row
        return CurrentUser(
            id=mapping["id"],
            username=mapping["username"],
            display_name=mapping["display_name"],
            email=mapping["email"],
            role=mapping["role"],
            is_active=bool(mapping["is_active"]),
            must_change_password=bool(mapping["must_change_password"]),
        )

    def _bootstrap_admin(self) -> None:
        username = os.environ.get("CAD_ADMIN_USERNAME", "admin").strip().lower()
        password = os.environ.get("CAD_ADMIN_PASSWORD", "ForceconAdmin!2026")
        display_name = os.environ.get("CAD_ADMIN_DISPLAY_NAME", "System Administrator")
        with self.engine.begin() as conn:
            exists = conn.execute(select(users.c.id).limit(1)).first()
            if exists:
                return
            now = _utcnow()
            user_id = str(uuid.uuid4())
            conn.execute(insert(users).values(
                id=user_id,
                username=username,
                display_name=display_name,
                email=None,
                role="ADMIN",
                is_active=True,
                must_change_password=True,
                password_hash=hash_password(password),
                created_at=now,
                updated_at=now,
            ))
            self._insert_default_preferences(conn, user_id, now)

    @staticmethod
    def _insert_default_preferences(conn: Any, user_id: str, now: datetime) -> None:
        conn.execute(insert(engineer_preferences).values(
            user_id=user_id,
            recommendation_mode="BALANCED",
            personal_case_weight=35,
            personal_case_min_similarity=82,
            default_tolerance_json="{}",
            dimension_placement_json="{}",
            annotation_style_json="{}",
            ui_settings_json="{}",
            updated_at=now,
        ))

    def authenticate(self, username: str, password: str) -> Optional[CurrentUser]:
        normalized = username.strip().lower()
        with self.engine.begin() as conn:
            row = conn.execute(select(users).where(users.c.username == normalized)).first()
            encoded = row._mapping["password_hash"] if row else hash_password("invalid-password-value")
            valid = verify_password(password, encoded)
            if not row or not valid or not row._mapping["is_active"]:
                return None
            conn.execute(update(users).where(users.c.id == row._mapping["id"]).values(
                last_login_at=_utcnow(), updated_at=_utcnow()
            ))
            return self._row_user(row)

    def create_session(
        self,
        user_id: str,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        hours: int = DEFAULT_SESSION_HOURS,
    ) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(48)
        now = _utcnow()
        expires = now + timedelta(hours=hours)
        with self.engine.begin() as conn:
            conn.execute(insert(sessions).values(
                id=str(uuid.uuid4()),
                user_id=user_id,
                token_hash=_token_hash(token),
                created_at=now,
                expires_at=expires,
                revoked_at=None,
                client_ip=client_ip,
                user_agent=(user_agent or "")[:500],
            ))
        return token, expires

    def user_for_token(self, token: Optional[str]) -> Optional[CurrentUser]:
        if not token:
            return None
        now = _utcnow()
        stmt = (
            select(users)
            .select_from(sessions.join(users, sessions.c.user_id == users.c.id))
            .where(and_(
                sessions.c.token_hash == _token_hash(token),
                sessions.c.revoked_at.is_(None),
                sessions.c.expires_at > now,
                users.c.is_active.is_(True),
            ))
        )
        with self.engine.connect() as conn:
            row = conn.execute(stmt).first()
            return self._row_user(row) if row else None

    def revoke_session(self, token: Optional[str]) -> None:
        if not token:
            return
        with self.engine.begin() as conn:
            conn.execute(update(sessions).where(
                sessions.c.token_hash == _token_hash(token)
            ).values(revoked_at=_utcnow()))

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        with self.engine.begin() as conn:
            row = conn.execute(select(users).where(users.c.id == user_id)).first()
            if not row or not verify_password(current_password, row._mapping["password_hash"]):
                raise ValueError("Current password is incorrect")
            conn.execute(update(users).where(users.c.id == user_id).values(
                password_hash=hash_password(new_password),
                must_change_password=False,
                updated_at=_utcnow(),
            ))
            conn.execute(update(sessions).where(and_(
                sessions.c.user_id == user_id,
                sessions.c.revoked_at.is_(None),
            )).values(revoked_at=_utcnow()))

    def list_users(self) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(users).order_by(users.c.username)).all()
        return [self._public_user_row(row) for row in rows]

    @staticmethod
    def _public_user_row(row: Any) -> Dict[str, Any]:
        value = dict(row._mapping)
        value.pop("password_hash", None)
        for key in ("created_at", "updated_at", "last_login_at"):
            if value.get(key):
                value[key] = value[key].isoformat()
        return value

    def create_user(self, payload: Dict[str, Any], actor_user_id: str) -> Dict[str, Any]:
        username = str(payload.get("username") or "").strip().lower()
        if not username or not username.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username may contain letters, numbers, '-' and '_'")
        role = str(payload.get("role") or "ENGINEER").upper()
        if role not in {"ADMIN", "ENGINEER"}:
            raise ValueError("Role must be ADMIN or ENGINEER")
        now = _utcnow()
        user_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conflict = conn.execute(select(users.c.id).where(or_(
                users.c.username == username,
                and_(users.c.email.is_not(None), users.c.email == payload.get("email")),
            ))).first()
            if conflict:
                raise ValueError("Username or email already exists")
            conn.execute(insert(users).values(
                id=user_id,
                username=username,
                display_name=str(payload.get("display_name") or username).strip(),
                email=(str(payload["email"]).strip().lower() if payload.get("email") else None),
                role=role,
                is_active=True,
                must_change_password=True,
                password_hash=hash_password(str(payload.get("password") or "")),
                created_at=now,
                updated_at=now,
            ))
            self._insert_default_preferences(conn, user_id, now)
            self._audit_conn(conn, actor_user_id, "USER_CREATED", "USER", user_id, {"username": username, "role": role})
            row = conn.execute(select(users).where(users.c.id == user_id)).first()
        return self._public_user_row(row)

    def update_user(self, user_id: str, payload: Dict[str, Any], actor_user_id: str) -> Dict[str, Any]:
        allowed: Dict[str, Any] = {}
        if "display_name" in payload:
            allowed["display_name"] = str(payload["display_name"]).strip()
        if "email" in payload:
            allowed["email"] = str(payload["email"]).strip().lower() or None
        if "role" in payload:
            role = str(payload["role"]).upper()
            if role not in {"ADMIN", "ENGINEER"}:
                raise ValueError("Role must be ADMIN or ENGINEER")
            allowed["role"] = role
        if "is_active" in payload:
            allowed["is_active"] = bool(payload["is_active"])
        allowed["updated_at"] = _utcnow()
        with self.engine.begin() as conn:
            result = conn.execute(update(users).where(users.c.id == user_id).values(**allowed))
            if result.rowcount != 1:
                raise KeyError("User not found")
            if payload.get("is_active") is False:
                conn.execute(update(sessions).where(sessions.c.user_id == user_id).values(revoked_at=_utcnow()))
            self._audit_conn(conn, actor_user_id, "USER_UPDATED", "USER", user_id, payload)
            row = conn.execute(select(users).where(users.c.id == user_id)).first()
        return self._public_user_row(row)

    def reset_password(self, user_id: str, new_password: str, actor_user_id: str) -> None:
        with self.engine.begin() as conn:
            result = conn.execute(update(users).where(users.c.id == user_id).values(
                password_hash=hash_password(new_password),
                must_change_password=True,
                updated_at=_utcnow(),
            ))
            if result.rowcount != 1:
                raise KeyError("User not found")
            conn.execute(update(sessions).where(sessions.c.user_id == user_id).values(revoked_at=_utcnow()))
            self._audit_conn(conn, actor_user_id, "PASSWORD_RESET", "USER", user_id, {})

    def get_preferences(self, user_id: str) -> Dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(select(engineer_preferences).where(
                engineer_preferences.c.user_id == user_id
            )).first()
        if not row:
            return {}
        value = dict(row._mapping)
        for column, default in (
            ("default_tolerance_json", {}),
            ("dimension_placement_json", {}),
            ("annotation_style_json", {}),
            ("ui_settings_json", {}),
        ):
            value[column.removesuffix("_json")] = _load_json(value.pop(column, None), default)
        value["personal_case_weight"] = value["personal_case_weight"] / 100.0
        value["personal_case_min_similarity"] = value["personal_case_min_similarity"] / 100.0
        if value.get("updated_at"):
            value["updated_at"] = value["updated_at"].isoformat()
        return value

    def update_preferences(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        mode = str(payload.get("recommendation_mode", "BALANCED")).upper()
        if mode not in {"BALANCED", "PERSONAL_FIRST", "COMPANY_ONLY"}:
            raise ValueError("Unsupported recommendation_mode")
        weight = max(0.0, min(1.0, float(payload.get("personal_case_weight", 0.35))))
        minimum = max(0.5, min(1.0, float(payload.get("personal_case_min_similarity", 0.82))))
        values = {
            "recommendation_mode": mode,
            "personal_case_weight": round(weight * 100),
            "personal_case_min_similarity": round(minimum * 100),
            "default_tolerance_json": _json(payload.get("default_tolerance", {})),
            "dimension_placement_json": _json(payload.get("dimension_placement", {})),
            "annotation_style_json": _json(payload.get("annotation_style", {})),
            "ui_settings_json": _json(payload.get("ui_settings", {})),
            "updated_at": _utcnow(),
        }
        with self.engine.begin() as conn:
            result = conn.execute(update(engineer_preferences).where(
                engineer_preferences.c.user_id == user_id
            ).values(**values))
            if result.rowcount == 0:
                conn.execute(insert(engineer_preferences).values(user_id=user_id, **values))
        return self.get_preferences(user_id)

    def reset_preferences(self, user_id: str) -> Dict[str, Any]:
        now = _utcnow()
        with self.engine.begin() as conn:
            conn.execute(delete(engineer_preferences).where(
                engineer_preferences.c.user_id == user_id
            ))
            self._insert_default_preferences(conn, user_id, now)
            self._audit_conn(
                conn, user_id, "ENGINEER_PREFERENCES_RESET", "USER", user_id, {}
            )
        return self.get_preferences(user_id)

    def claim_model(self, model_id: str, owner_user_id: str, source_filename: Optional[str] = None) -> None:
        with self.engine.begin() as conn:
            row = conn.execute(select(owned_models).where(owned_models.c.model_id == model_id)).first()
            if row:
                return
            conn.execute(insert(owned_models).values(
                model_id=model_id,
                owner_user_id=owner_user_id,
                source_filename=source_filename,
                created_at=_utcnow(),
            ))

    def can_access_model(self, user: CurrentUser, model_id: str) -> bool:
        if user.is_admin:
            return True
        with self.engine.connect() as conn:
            owner = conn.execute(select(owned_models.c.owner_user_id).where(
                owned_models.c.model_id == model_id
            )).scalar_one_or_none()
        return owner == user.id

    def accessible_model_ids(self, user: CurrentUser) -> Optional[set[str]]:
        if user.is_admin:
            return None
        with self.engine.connect() as conn:
            rows = conn.execute(select(owned_models.c.model_id).where(
                owned_models.c.owner_user_id == user.id
            )).scalars().all()
        return set(rows)

    def record_artifact_and_cases(
        self,
        user: CurrentUser,
        model_id: str,
        part_id: str,
        feature_records: Iterable[Dict[str, Any]],
        output_files: Dict[str, Any],
        title: Optional[str] = None,
        part_type: Optional[str] = None,
        product_family: Optional[str] = None,
    ) -> Dict[str, Any]:
        artifact_id = str(uuid.uuid4())
        now = _utcnow()
        records = list(feature_records)
        learned = 0
        with self.engine.begin() as conn:
            conn.execute(insert(annotation_artifacts).values(
                id=artifact_id,
                owner_user_id=user.id,
                model_id=model_id,
                part_id=part_id,
                title=title or f"{part_id} annotation",
                status="COMPLETED",
                input_snapshot_json=_json({"feature_records": records}),
                output_files_json=_json(output_files),
                created_at=now,
                updated_at=now,
            ))
            for record in records:
                tolerance = record.get("tolerance_config") or {}
                mode = str(tolerance.get("mode") or "NONE").upper()
                if mode == "NONE" and not record.get("tolerance"):
                    continue
                rule_id = str(record.get("rule_id") or record.get("id") or uuid.uuid4().hex[:8])
                case_id = f"ENG_{user.id[:8]}_{artifact_id[:8]}_{rule_id}"[:160]
                nominal = record.get("nominal") or {
                    "value": record.get("nominal_value", record.get("value", 0.0))
                }
                placement = {
                    key: record.get(key)
                    for key in ("preferred_view", "views", "target_views", "side", "sides", "baseline", "offset", "rank")
                    if record.get(key) is not None
                }
                conn.execute(insert(engineer_tolerance_cases).values(
                    id=str(uuid.uuid4()),
                    owner_user_id=user.id,
                    artifact_id=artifact_id,
                    case_id=case_id,
                    model_id=model_id,
                    part_id=part_id,
                    rule_id=rule_id,
                    part_type=part_type,
                    product_family=product_family,
                    feature_type=str(record.get("category") or record.get("feature_type") or record.get("type") or "unknown"),
                    inferred_role=record.get("inferred_role") or record.get("role"),
                    nominal_json=_json(nominal),
                    tolerance_json=_json(tolerance or {"text": record.get("tolerance")}),
                    placement_json=_json(placement),
                    geometry_json=_json(record.get("geometry_payload") or record.get("geometry") or {}),
                    is_enabled=True,
                    use_count=0,
                    created_at=now,
                    updated_at=now,
                ))
                learned += 1
            self._audit_conn(conn, user.id, "ANNOTATION_ARTIFACT_CREATED", "ARTIFACT", artifact_id, {
                "model_id": model_id, "part_id": part_id, "learned_case_count": learned
            })
        return {"artifact_id": artifact_id, "learned_case_count": learned}

    def list_artifacts(self, user: CurrentUser, owner_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        stmt = select(annotation_artifacts).order_by(annotation_artifacts.c.created_at.desc())
        if not user.is_admin:
            stmt = stmt.where(annotation_artifacts.c.owner_user_id == user.id)
        elif owner_user_id:
            stmt = stmt.where(annotation_artifacts.c.owner_user_id == owner_user_id)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        result = []
        for row in rows:
            value = dict(row._mapping)
            value["input_snapshot"] = _load_json(value.pop("input_snapshot_json"), {})
            value["output_files"] = _load_json(value.pop("output_files_json"), {})
            value["created_at"] = value["created_at"].isoformat()
            value["updated_at"] = value["updated_at"].isoformat()
            result.append(value)
        return result

    def delete_artifact(self, user: CurrentUser, artifact_id: str) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(select(annotation_artifacts).where(
                annotation_artifacts.c.id == artifact_id
            )).first()
            if not row:
                return False
            if not user.is_admin and row._mapping["owner_user_id"] != user.id:
                raise PermissionError("Artifact belongs to another engineer")
            conn.execute(delete(annotation_artifacts).where(annotation_artifacts.c.id == artifact_id))
            self._audit_conn(conn, user.id, "ANNOTATION_ARTIFACT_DELETED", "ARTIFACT", artifact_id, {})
        return True

    def personal_cases(self, user_id: str) -> List[Dict[str, Any]]:
        source = engineer_tolerance_cases.outerjoin(
            annotation_artifacts,
            engineer_tolerance_cases.c.artifact_id == annotation_artifacts.c.id,
        )
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(
                    engineer_tolerance_cases,
                    annotation_artifacts.c.output_files_json.label("artifact_output_files_json"),
                )
                .select_from(source)
                .where(and_(
                    engineer_tolerance_cases.c.owner_user_id == user_id,
                    engineer_tolerance_cases.c.is_enabled.is_(True),
                ))
            ).all()
        result = []
        for row in rows:
            value = dict(row._mapping)
            value["nominal"] = _load_json(value.pop("nominal_json"), {})
            value["tolerance_config"] = _load_json(value.pop("tolerance_json"), {})
            value["placement"] = _load_json(value.pop("placement_json"), {})
            value["geometry"] = _load_json(value.pop("geometry_json"), {})
            value["artifact_output_files"] = _load_json(
                value.pop("artifact_output_files_json", None), {}
            )
            result.append(value)
        return result

    def delete_personal_case(self, user_id: str, case_record_id: str) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(select(engineer_tolerance_cases.c.id).where(and_(
                engineer_tolerance_cases.c.id == case_record_id,
                engineer_tolerance_cases.c.owner_user_id == user_id,
            ))).first()
            if not row:
                return False
            conn.execute(delete(engineer_tolerance_cases).where(and_(
                engineer_tolerance_cases.c.id == case_record_id,
                engineer_tolerance_cases.c.owner_user_id == user_id,
            )))
            self._audit_conn(
                conn,
                user_id,
                "ENGINEER_TOLERANCE_CASE_DELETED",
                "ENGINEER_TOLERANCE_CASE",
                case_record_id,
                {},
            )
        return True

    def clear_personal_cases(self, user_id: str) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(delete(engineer_tolerance_cases).where(
                engineer_tolerance_cases.c.owner_user_id == user_id
            ))
            deleted_count = int(result.rowcount or 0)
            self._audit_conn(
                conn,
                user_id,
                "ENGINEER_TOLERANCE_CASES_CLEARED",
                "USER",
                user_id,
                {"deleted_count": deleted_count},
            )
        return deleted_count

    def list_templates(self, user_id: str) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(engineer_templates).where(
                engineer_templates.c.owner_user_id == user_id
            ).order_by(engineer_templates.c.updated_at.desc())).all()
        result = []
        for row in rows:
            value = dict(row._mapping)
            payload = _load_json(value.pop("template_json"), {})
            payload.update({
                "id": value["id"],
                "name": value["name"],
                "description": value["description"],
                "scope": "PERSONAL",
                "owner_user_id": value["owner_user_id"],
            })
            result.append(payload)
        return result

    def save_template(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        template_id = str(payload.get("id") or uuid.uuid4())
        now = _utcnow()
        values = {
            "owner_user_id": user_id,
            "name": str(payload.get("name") or "Personal template")[:160],
            "description": str(payload.get("description") or ""),
            "template_json": _json({key: value for key, value in payload.items() if key not in {"id", "owner_user_id"}}),
            "updated_at": now,
        }
        with self.engine.begin() as conn:
            row = conn.execute(select(engineer_templates).where(
                engineer_templates.c.id == template_id
            )).first()
            if row and row._mapping["owner_user_id"] != user_id:
                raise PermissionError("Template belongs to another engineer")
            if row:
                conn.execute(update(engineer_templates).where(
                    engineer_templates.c.id == template_id
                ).values(**values))
            else:
                conn.execute(insert(engineer_templates).values(id=template_id, created_at=now, **values))
        return next(item for item in self.list_templates(user_id) if item["id"] == template_id)

    def delete_template(self, user: CurrentUser, template_id: str) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(select(engineer_templates).where(
                engineer_templates.c.id == template_id
            )).first()
            if not row:
                return False
            if not user.is_admin and row._mapping["owner_user_id"] != user.id:
                raise PermissionError("Template belongs to another engineer")
            conn.execute(delete(engineer_templates).where(engineer_templates.c.id == template_id))
        return True

    def list_audit_events(self, limit: int = 200) -> List[Dict[str, Any]]:
        stmt = select(audit_events).order_by(audit_events.c.created_at.desc()).limit(max(1, min(limit, 1000)))
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        result = []
        for row in rows:
            value = dict(row._mapping)
            value["details"] = _load_json(value.pop("details_json"), {})
            value["created_at"] = value["created_at"].isoformat()
            result.append(value)
        return result

    @staticmethod
    def _audit_conn(
        conn: Any,
        actor_user_id: Optional[str],
        action: str,
        target_type: Optional[str],
        target_id: Optional[str],
        details: Dict[str, Any],
    ) -> None:
        conn.execute(insert(audit_events).values(
            id=str(uuid.uuid4()),
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details_json=_json(details),
            created_at=_utcnow(),
        ))
