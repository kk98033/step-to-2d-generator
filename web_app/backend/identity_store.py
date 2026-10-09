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
import re
import secrets
import uuid
from base64 import urlsafe_b64encode
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
from cryptography.fernet import Fernet, InvalidToken


UTC = timezone.utc
PASSWORD_ITERATIONS = 600_000
DEFAULT_SESSION_HOURS = 12


def company_email_domains() -> List[str]:
    raw = os.environ.get("CAD_COMPANY_EMAIL_DOMAINS", "forcecon.com.tw")
    domains = []
    for value in raw.split(","):
        domain = value.strip().lower().lstrip("@")
        if domain and domain not in domains:
            domains.append(domain)
    return domains or ["forcecon.com.tw"]


def canonical_company_email(identifier: str, domain: Optional[str] = None) -> str:
    value = str(identifier or "").strip().lower()
    allowed = company_email_domains()
    if "@" in value:
        local, selected_domain = value.rsplit("@", 1)
    else:
        local = value
        selected_domain = str(domain or allowed[0]).strip().lower().lstrip("@")
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9._+\-]{0,63}", local)
        or local.endswith(".")
        or ".." in local
    ):
        raise ValueError("A valid email local-part is required")
    if selected_domain not in allowed:
        raise ValueError("Email domain is not allowed by company policy")
    email = f"{local}@{selected_domain}"
    if len(email) > 80:
        raise ValueError("Company email exceeds the account identifier limit")
    return email


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


def _snapshot_row(row: Any) -> Dict[str, Any]:
    """Convert a SQLAlchemy row into a JSON-safe restoration snapshot."""
    mapping = row._mapping if hasattr(row, "_mapping") else row
    result: Dict[str, Any] = {}
    for key, value in dict(mapping).items():
        result[key] = value.isoformat() if isinstance(value, datetime) else value
    return result


def _restore_row(table: Table, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce ISO timestamps from a trash snapshot back to table values."""
    result: Dict[str, Any] = {}
    for column in table.columns:
        if column.name not in payload:
            continue
        value = payload[column.name]
        if value is not None and isinstance(column.type, DateTime) and isinstance(value, str):
            value = datetime.fromisoformat(value)
        result[column.name] = value
    return result


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

trash_records = Table(
    "trash_records",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("record_type", String(80), nullable=False),
    Column("record_id", String(255), nullable=False),
    Column("owner_user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("deleted_by_user_id", String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("snapshot_json", Text, nullable=False),
    Column("deleted_at", DateTime(timezone=True), nullable=False),
    Column("purge_after", DateTime(timezone=True), nullable=False),
    UniqueConstraint("record_type", "record_id", name="uq_trash_record_type_id"),
)

system_settings = Table(
    "system_settings",
    metadata,
    Column("key", String(120), primary_key=True),
    Column("value_json", Text, nullable=False),
    Column("updated_by_user_id", String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

recommendation_tags = Table(
    "recommendation_tags",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("key", String(160), nullable=False, unique=True),
    Column("name", String(160), nullable=False),
    Column("dimension", String(40), nullable=False),
    Column("description", Text, nullable=True),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_by_user_id", String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

artifact_tag_assignments = Table(
    "artifact_tag_assignments",
    metadata,
    Column("artifact_id", String(36), ForeignKey("annotation_artifacts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", String(36), ForeignKey("recommendation_tags.id", ondelete="CASCADE"), primary_key=True),
)

engineer_case_tag_assignments = Table(
    "engineer_case_tag_assignments",
    metadata,
    Column("case_record_id", String(36), ForeignKey("engineer_tolerance_cases.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", String(36), ForeignKey("recommendation_tags.id", ondelete="CASCADE"), primary_key=True),
)

company_case_tag_assignments = Table(
    "company_case_tag_assignments",
    metadata,
    Column("case_id", String(160), primary_key=True),
    Column("tag_id", String(36), ForeignKey("recommendation_tags.id", ondelete="CASCADE"), primary_key=True),
)

initial_credentials = Table(
    "initial_credentials",
    metadata,
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("encrypted_password", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
)

credential_notifications = Table(
    "credential_notifications",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("recipient_email", String(255), nullable=False),
    Column("status", String(20), nullable=False, default="PENDING"),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("provider_message_id", String(255), nullable=True),
    Column("last_error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
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
        encryption_secret = os.environ.get("CAD_CREDENTIAL_ENCRYPTION_KEY", "").strip()
        if encryption_secret:
            try:
                self._credential_cipher = Fernet(encryption_secret.encode("ascii"))
            except (ValueError, TypeError) as exc:
                raise ValueError("CAD_CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key") from exc
        else:
            derived = hashlib.sha256(
                f"{self.database_url}|{os.environ.get('CAD_ADMIN_PASSWORD', 'ForceconAdmin!2026')}|credential-envelope".encode("utf-8")
            ).digest()
            self._credential_cipher = Fernet(urlsafe_b64encode(derived))
        metadata.create_all(self.engine)
        self._bootstrap_admin()
        self._migrate_email_identities()
        self._bootstrap_recommendation_tags()
        self.purge_expired_trash(actor_user_id=None)

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
        username = canonical_company_email(
            os.environ.get("CAD_ADMIN_EMAIL") or os.environ.get("CAD_ADMIN_USERNAME", "admin")
        )
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
                email=username,
                role="ADMIN",
                is_active=True,
                must_change_password=False,
                password_hash=hash_password(password),
                created_at=now,
                updated_at=now,
            ))
            self._insert_default_preferences(conn, user_id, now)

    def _migrate_email_identities(self) -> None:
        """Backfill legacy username accounts into the company-email identity model."""
        primary_domain = company_email_domains()[0]
        with self.engine.begin() as conn:
            rows = conn.execute(select(users)).all()
            occupied = {str(row._mapping.get("email") or "").lower() for row in rows}
            for row in rows:
                mapping = row._mapping
                current_email = str(mapping.get("email") or "").strip().lower()
                local = str(mapping.get("username") or "user").split("@", 1)[0].strip().lower()
                try:
                    canonical = canonical_company_email(current_email or local)
                except ValueError:
                    safe_local = re.sub(r"[^a-z0-9._+\-]", "-", local).strip(".-")
                    safe_local = safe_local or f"user-{mapping['id'][:8]}"
                    canonical = canonical_company_email(safe_local[:64], primary_domain)
                if canonical in occupied and canonical != current_email:
                    canonical_local = canonical.split("@", 1)[0]
                    canonical = canonical_company_email(f"{canonical_local[:56]}-{mapping['id'][:6]}", primary_domain)
                occupied.add(canonical)
                if current_email != canonical or mapping["username"] != canonical:
                    conn.execute(update(users).where(users.c.id == mapping["id"]).values(
                        username=canonical,
                        email=canonical,
                        updated_at=_utcnow(),
                    ))
            conn.execute(update(users).where(and_(
                users.c.role == "ADMIN",
                users.c.must_change_password.is_(True),
            )).values(must_change_password=False, updated_at=_utcnow()))

    def _bootstrap_recommendation_tags(self) -> None:
        defaults = (
            ("company:global", "公司共用資料庫", "COMPANY_DATABASE"),
            ("type:general", "一般機械件", "PART_TYPE"),
            ("type:shaft", "軸類零件", "PART_TYPE"),
            ("type:hole", "孔／內徑", "PART_TYPE"),
            ("type:fan", "風扇／葉輪", "PART_TYPE"),
            ("type:housing", "殼體", "PART_TYPE"),
        )
        now = _utcnow()
        with self.engine.begin() as conn:
            for key, name, dimension in defaults:
                if conn.execute(select(recommendation_tags.c.id).where(
                    recommendation_tags.c.key == key
                )).first():
                    continue
                conn.execute(insert(recommendation_tags).values(
                    id=str(uuid.uuid4()), key=key, name=name, dimension=dimension,
                    description=None, is_active=True, created_by_user_id=None,
                    created_at=now, updated_at=now,
                ))

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

    def authenticate(self, username: str, password: str, domain: Optional[str] = None) -> Optional[CurrentUser]:
        try:
            normalized = canonical_company_email(username, domain)
        except ValueError:
            return None
        with self.engine.begin() as conn:
            row = conn.execute(select(users).where(or_(
                users.c.username == normalized,
                users.c.email == normalized,
            ))).first()
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
            conn.execute(delete(initial_credentials).where(initial_credentials.c.user_id == user_id))
            conn.execute(update(credential_notifications).where(and_(
                credential_notifications.c.user_id == user_id,
                credential_notifications.c.status.in_(["PENDING", "FAILED"]),
            )).values(status="CANCELLED", updated_at=_utcnow()))

    def list_users(self, include_initial_passwords: bool = False) -> List[Dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(users).order_by(users.c.username)).all()
            credentials = conn.execute(select(initial_credentials)).all() if include_initial_passwords else []
        passwords: Dict[str, str] = {}
        for credential in credentials:
            try:
                passwords[credential._mapping["user_id"]] = self._credential_cipher.decrypt(
                    credential._mapping["encrypted_password"].encode("ascii")
                ).decode("utf-8")
            except (InvalidToken, ValueError):
                passwords[credential._mapping["user_id"]] = ""
        result = [self._public_user_row(row) for row in rows]
        for value in result:
            value["initial_password"] = passwords.get(value["id"]) if include_initial_passwords else None
        return result

    @staticmethod
    def _public_user_row(row: Any) -> Dict[str, Any]:
        value = dict(row._mapping)
        value.pop("password_hash", None)
        for key in ("created_at", "updated_at", "last_login_at"):
            if value.get(key):
                value[key] = value[key].isoformat()
        return value

    @staticmethod
    def generate_initial_password(length: int = 16) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        password = "".join(secrets.choice(alphabet) for _ in range(max(12, length - 3)))
        return f"{password}!7a"

    def _store_initial_credential_conn(
        self,
        conn: Any,
        user_id: str,
        email: str,
        password: str,
        now: Optional[datetime] = None,
    ) -> None:
        created_at = now or _utcnow()
        conn.execute(delete(initial_credentials).where(initial_credentials.c.user_id == user_id))
        conn.execute(update(credential_notifications).where(and_(
            credential_notifications.c.user_id == user_id,
            credential_notifications.c.status.in_(["PENDING", "FAILED"]),
        )).values(status="CANCELLED", updated_at=created_at))
        conn.execute(insert(initial_credentials).values(
            user_id=user_id,
            encrypted_password=self._credential_cipher.encrypt(password.encode("utf-8")).decode("ascii"),
            created_at=created_at,
            expires_at=None,
        ))
        conn.execute(insert(credential_notifications).values(
            id=str(uuid.uuid4()), user_id=user_id, recipient_email=email,
            status="PENDING", attempt_count=0, provider_message_id=None,
            last_error=None, created_at=created_at, updated_at=created_at,
        ))

    def create_user(self, payload: Dict[str, Any], actor_user_id: str) -> Dict[str, Any]:
        email_input = str(payload.get("email") or payload.get("username") or "").strip()
        email = canonical_company_email(email_input, payload.get("email_domain"))
        username = email
        role = str(payload.get("role") or "ENGINEER").upper()
        if role not in {"ADMIN", "ENGINEER"}:
            raise ValueError("Role must be ADMIN or ENGINEER")
        now = _utcnow()
        user_id = str(uuid.uuid4())
        initial_password = str(payload.get("password") or self.generate_initial_password())
        with self.engine.begin() as conn:
            conflict = conn.execute(select(users.c.id).where(or_(
                users.c.username == username,
                users.c.email == email,
            ))).first()
            if conflict:
                raise ValueError("Username or email already exists")
            conn.execute(insert(users).values(
                id=user_id,
                username=username,
                display_name=str(payload.get("display_name") or username).strip(),
                email=email,
                role=role,
                is_active=True,
                must_change_password=(role != "ADMIN"),
                password_hash=hash_password(initial_password),
                created_at=now,
                updated_at=now,
            ))
            self._insert_default_preferences(conn, user_id, now)
            self._store_initial_credential_conn(conn, user_id, email, initial_password, now)
            self._audit_conn(conn, actor_user_id, "USER_CREATED", "USER", user_id, {"username": username, "role": role})
            row = conn.execute(select(users).where(users.c.id == user_id)).first()
        result = self._public_user_row(row)
        result["initial_password"] = initial_password
        return result

    def bulk_create_users(self, rows: Iterable[Dict[str, Any]], actor_user_id: str) -> List[Dict[str, Any]]:
        prepared: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for index, row in enumerate(rows, start=2):
            email = canonical_company_email(
                str(row.get("email") or row.get("account") or ""), row.get("email_domain")
            )
            if email in seen:
                raise ValueError(f"Duplicate email in import at row {index}: {email}")
            seen.add(email)
            display_name = str(row.get("display_name") or row.get("name") or "").strip()
            if not display_name:
                raise ValueError(f"Display name is required at row {index}")
            role = str(row.get("role") or "ENGINEER").upper()
            if role not in {"ADMIN", "ENGINEER"}:
                raise ValueError(f"Invalid role at row {index}: {role}")
            prepared.append({"email": email, "display_name": display_name, "role": role})
        if not prepared:
            raise ValueError("The import file contains no account rows")
        with self.engine.connect() as conn:
            existing = set(conn.execute(select(users.c.email).where(
                users.c.email.in_([item["email"] for item in prepared])
            )).scalars().all())
        if existing:
            raise ValueError(f"Accounts already exist: {', '.join(sorted(existing))}")
        return [self.create_user(item, actor_user_id) for item in prepared]

    def pending_credential_notifications(self, limit: int = 100) -> List[Dict[str, Any]]:
        stmt = (
            select(credential_notifications, initial_credentials.c.encrypted_password)
            .select_from(credential_notifications.join(
                initial_credentials,
                credential_notifications.c.user_id == initial_credentials.c.user_id,
            ))
            .where(credential_notifications.c.status.in_(["PENDING", "FAILED"]))
            .order_by(credential_notifications.c.created_at)
            .limit(max(1, min(500, int(limit))))
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        result: List[Dict[str, Any]] = []
        for row in rows:
            value = dict(row._mapping)
            encrypted = value.pop("encrypted_password")
            try:
                value["initial_password"] = self._credential_cipher.decrypt(
                    encrypted.encode("ascii")
                ).decode("utf-8")
            except InvalidToken:
                continue
            for key in ("created_at", "updated_at"):
                value[key] = value[key].isoformat()
            result.append(value)
        return result

    def acknowledge_credential_notification(
        self,
        notification_id: str,
        status: str,
        provider_message_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        normalized = str(status).upper()
        if normalized not in {"SENT", "FAILED"}:
            raise ValueError("Notification status must be SENT or FAILED")
        with self.engine.begin() as conn:
            result = conn.execute(update(credential_notifications).where(
                credential_notifications.c.id == notification_id
            ).values(
                status=normalized,
                attempt_count=credential_notifications.c.attempt_count + 1,
                provider_message_id=provider_message_id,
                last_error=error,
                updated_at=_utcnow(),
            ))
        return result.rowcount == 1

    def update_user(self, user_id: str, payload: Dict[str, Any], actor_user_id: str) -> Dict[str, Any]:
        allowed: Dict[str, Any] = {}
        if "display_name" in payload:
            allowed["display_name"] = str(payload["display_name"]).strip()
        if "email" in payload:
            email = canonical_company_email(str(payload["email"]))
            allowed["email"] = email
            allowed["username"] = email
        if "role" in payload:
            role = str(payload["role"]).upper()
            if role not in {"ADMIN", "ENGINEER"}:
                raise ValueError("Role must be ADMIN or ENGINEER")
            allowed["role"] = role
        if "is_active" in payload:
            allowed["is_active"] = bool(payload["is_active"])
        allowed["updated_at"] = _utcnow()
        with self.engine.begin() as conn:
            if "email" in allowed:
                conflict = conn.execute(select(users.c.id).where(and_(
                    users.c.email == allowed["email"],
                    users.c.id != user_id,
                ))).first()
                if conflict:
                    raise ValueError("Email already exists")
            result = conn.execute(update(users).where(users.c.id == user_id).values(**allowed))
            if result.rowcount != 1:
                raise KeyError("User not found")
            if payload.get("is_active") is False:
                conn.execute(update(sessions).where(sessions.c.user_id == user_id).values(revoked_at=_utcnow()))
            self._audit_conn(conn, actor_user_id, "USER_UPDATED", "USER", user_id, payload)
            row = conn.execute(select(users).where(users.c.id == user_id)).first()
        return self._public_user_row(row)

    def reset_password(self, user_id: str, new_password: Optional[str], actor_user_id: str) -> str:
        replacement = str(new_password or self.generate_initial_password())
        with self.engine.begin() as conn:
            user_row = conn.execute(select(users).where(users.c.id == user_id)).first()
            if not user_row:
                raise KeyError("User not found")
            must_change_password = user_row._mapping["role"] != "ADMIN"
            result = conn.execute(update(users).where(users.c.id == user_id).values(
                password_hash=hash_password(replacement),
                must_change_password=must_change_password,
                updated_at=_utcnow(),
            ))
            if result.rowcount != 1:
                raise KeyError("User not found")
            conn.execute(update(sessions).where(sessions.c.user_id == user_id).values(revoked_at=_utcnow()))
            self._store_initial_credential_conn(
                conn, user_id, user_row._mapping["email"], replacement
            )
            self._audit_conn(conn, actor_user_id, "PASSWORD_RESET", "USER", user_id, {})
        return replacement

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

    @staticmethod
    def _tag_row(row: Any) -> Dict[str, Any]:
        value = dict(row._mapping)
        for key in ("created_at", "updated_at"):
            if value.get(key):
                value[key] = value[key].isoformat()
        return value

    def list_recommendation_tags(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        stmt = select(recommendation_tags).order_by(
            recommendation_tags.c.dimension, recommendation_tags.c.name
        )
        if not include_inactive:
            stmt = stmt.where(recommendation_tags.c.is_active.is_(True))
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [self._tag_row(row) for row in rows]

    def create_recommendation_tag(self, payload: Dict[str, Any], actor_user_id: str) -> Dict[str, Any]:
        dimension = str(payload.get("dimension") or "CUSTOM").strip().upper()
        allowed_dimensions = {
            "COMPANY_DATABASE", "CUSTOMER", "PART_TYPE", "ENGINEER",
            "PROJECT", "MATERIAL", "PROCESS", "CUSTOM",
        }
        if dimension not in allowed_dimensions:
            raise ValueError(f"Unsupported tag dimension: {dimension}")
        key = str(payload.get("key") or "").strip().lower()
        name = str(payload.get("name") or "").strip()
        if not key or not name:
            raise ValueError("Tag key and name are required")
        if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789:_-." for character in key):
            raise ValueError("Tag key may contain lowercase letters, numbers, ':', '_', '-' and '.'")
        tag_id = str(uuid.uuid4())
        now = _utcnow()
        with self.engine.begin() as conn:
            if conn.execute(select(recommendation_tags.c.id).where(
                recommendation_tags.c.key == key
            )).first():
                raise ValueError("Tag key already exists")
            conn.execute(insert(recommendation_tags).values(
                id=tag_id, key=key, name=name[:160], dimension=dimension,
                description=str(payload.get("description") or "") or None,
                is_active=True, created_by_user_id=actor_user_id,
                created_at=now, updated_at=now,
            ))
            self._audit_conn(conn, actor_user_id, "RECOMMENDATION_TAG_CREATED", "TAG", tag_id, {
                "key": key, "dimension": dimension
            })
            row = conn.execute(select(recommendation_tags).where(
                recommendation_tags.c.id == tag_id
            )).first()
        return self._tag_row(row)

    def update_recommendation_tag(self, tag_id: str, payload: Dict[str, Any], actor_user_id: str) -> Dict[str, Any]:
        values: Dict[str, Any] = {"updated_at": _utcnow()}
        for key in ("name", "description"):
            if key in payload:
                values[key] = str(payload[key]).strip() or None
        if "is_active" in payload:
            values["is_active"] = bool(payload["is_active"])
        with self.engine.begin() as conn:
            result = conn.execute(update(recommendation_tags).where(
                recommendation_tags.c.id == tag_id
            ).values(**values))
            if result.rowcount != 1:
                raise KeyError("Tag not found")
            self._audit_conn(conn, actor_user_id, "RECOMMENDATION_TAG_UPDATED", "TAG", tag_id, payload)
            row = conn.execute(select(recommendation_tags).where(
                recommendation_tags.c.id == tag_id
            )).first()
        return self._tag_row(row)

    @staticmethod
    def _validate_tag_ids_conn(conn: Any, tag_ids: Iterable[str]) -> List[str]:
        normalized = list(dict.fromkeys(str(value) for value in tag_ids if value))
        if not normalized:
            return []
        existing = set(conn.execute(select(recommendation_tags.c.id).where(and_(
            recommendation_tags.c.id.in_(normalized),
            recommendation_tags.c.is_active.is_(True),
        ))).scalars().all())
        missing = [tag_id for tag_id in normalized if tag_id not in existing]
        if missing:
            raise ValueError(f"Unknown or inactive recommendation tags: {', '.join(missing)}")
        return normalized

    def validate_recommendation_tag_ids(self, tag_ids: Iterable[str]) -> List[str]:
        with self.engine.connect() as conn:
            return self._validate_tag_ids_conn(conn, tag_ids)

    def assign_company_case_tags(self, case_id: str, tag_ids: Iterable[str], actor_user_id: str) -> List[str]:
        with self.engine.begin() as conn:
            normalized = self._validate_tag_ids_conn(conn, tag_ids)
            conn.execute(delete(company_case_tag_assignments).where(
                company_case_tag_assignments.c.case_id == case_id
            ))
            for tag_id in normalized:
                conn.execute(insert(company_case_tag_assignments).values(case_id=case_id, tag_id=tag_id))
            self._audit_conn(conn, actor_user_id, "COMPANY_CASE_TAGS_UPDATED", "COMPANY_CASE", case_id, {
                "tag_ids": normalized
            })
        return normalized

    def company_case_tags(self, case_id: Optional[str] = None) -> Dict[str, List[str]]:
        stmt = select(company_case_tag_assignments)
        if case_id:
            stmt = stmt.where(company_case_tag_assignments.c.case_id == case_id)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        result: Dict[str, List[str]] = {}
        for row in rows:
            result.setdefault(row._mapping["case_id"], []).append(row._mapping["tag_id"])
        return result

    def company_case_ids_for_tags(self, tag_ids: Iterable[str], match_mode: str = "ANY") -> Optional[set[str]]:
        selected = set(str(value) for value in tag_ids if value)
        if not selected:
            return None
        assignments = self.company_case_tags()
        use_all = str(match_mode).upper() == "ALL"
        return {
            case_id for case_id, assigned in assignments.items()
            if (selected.issubset(set(assigned)) if use_all else bool(selected & set(assigned)))
        }

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
        tag_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        artifact_id = str(uuid.uuid4())
        now = _utcnow()
        records = list(feature_records)
        learned = 0
        with self.engine.begin() as conn:
            normalized_tag_ids = self._validate_tag_ids_conn(conn, tag_ids or [])
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
            for tag_id in normalized_tag_ids:
                conn.execute(insert(artifact_tag_assignments).values(
                    artifact_id=artifact_id, tag_id=tag_id
            ))
            for record in records:
                if record.get("enabled") is False:
                    continue
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
                case_record_id = str(uuid.uuid4())
                conn.execute(insert(engineer_tolerance_cases).values(
                    id=case_record_id,
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
                for tag_id in normalized_tag_ids:
                    conn.execute(insert(engineer_case_tag_assignments).values(
                        case_record_id=case_record_id, tag_id=tag_id
                    ))
                learned += 1
            self._audit_conn(conn, user.id, "ANNOTATION_ARTIFACT_CREATED", "ARTIFACT", artifact_id, {
                "model_id": model_id, "part_id": part_id, "learned_case_count": learned
            })
        return {"artifact_id": artifact_id, "learned_case_count": learned, "tag_ids": normalized_tag_ids}

    def list_artifacts(self, user: CurrentUser, owner_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        stmt = select(annotation_artifacts).order_by(annotation_artifacts.c.created_at.desc())
        if not user.is_admin:
            stmt = stmt.where(annotation_artifacts.c.owner_user_id == user.id)
        elif owner_user_id:
            stmt = stmt.where(annotation_artifacts.c.owner_user_id == owner_user_id)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
            artifact_ids = [row._mapping["id"] for row in rows]
            tag_rows = conn.execute(select(artifact_tag_assignments).where(
                artifact_tag_assignments.c.artifact_id.in_(artifact_ids)
            )).all() if artifact_ids else []
        tags_by_artifact: Dict[str, List[str]] = {}
        for tag_row in tag_rows:
            tags_by_artifact.setdefault(tag_row._mapping["artifact_id"], []).append(
                tag_row._mapping["tag_id"]
            )
        result = []
        for row in rows:
            value = dict(row._mapping)
            value["tag_ids"] = tags_by_artifact.get(value["id"], [])
            value["input_snapshot"] = _load_json(value.pop("input_snapshot_json"), {})
            value["output_files"] = _load_json(value.pop("output_files_json"), {})
            value["created_at"] = value["created_at"].isoformat()
            value["updated_at"] = value["updated_at"].isoformat()
            result.append(value)
        return result

    def trash_retention_days(self) -> int:
        default_days = max(1, int(os.environ.get("CAD_TRASH_RETENTION_DAYS", "30")))
        with self.engine.connect() as conn:
            raw = conn.execute(select(system_settings.c.value_json).where(
                system_settings.c.key == "trash_retention_days"
            )).scalar_one_or_none()
        value = _load_json(raw, default_days)
        try:
            return max(1, min(3650, int(value)))
        except (TypeError, ValueError):
            return default_days

    def set_trash_retention_days(self, days: int, actor_user_id: str) -> int:
        normalized = max(1, min(3650, int(days)))
        now = _utcnow()
        with self.engine.begin() as conn:
            existing = conn.execute(select(system_settings.c.key).where(
                system_settings.c.key == "trash_retention_days"
            )).first()
            values = {
                "value_json": _json(normalized),
                "updated_by_user_id": actor_user_id,
                "updated_at": now,
            }
            if existing:
                conn.execute(update(system_settings).where(
                    system_settings.c.key == "trash_retention_days"
                ).values(**values))
            else:
                conn.execute(insert(system_settings).values(
                    key="trash_retention_days", **values
                ))
            existing_trash = conn.execute(select(
                trash_records.c.id, trash_records.c.deleted_at
            )).all()
            for trash_row in existing_trash:
                conn.execute(update(trash_records).where(
                    trash_records.c.id == trash_row._mapping["id"]
                ).values(
                    purge_after=trash_row._mapping["deleted_at"] + timedelta(days=normalized)
                ))
            self._audit_conn(conn, actor_user_id, "TRASH_RETENTION_UPDATED", "SYSTEM", None, {
                "days": normalized
            })
        return normalized

    def _trash_conn(
        self,
        conn: Any,
        record_type: str,
        record_id: str,
        owner_user_id: str,
        deleted_by_user_id: str,
        snapshot: Dict[str, Any],
    ) -> str:
        trash_id = str(uuid.uuid4())
        now = _utcnow()
        default_days = max(1, int(os.environ.get("CAD_TRASH_RETENTION_DAYS", "30")))
        raw = conn.execute(select(system_settings.c.value_json).where(
            system_settings.c.key == "trash_retention_days"
        )).scalar_one_or_none()
        try:
            days = max(1, min(3650, int(_load_json(raw, default_days))))
        except (TypeError, ValueError):
            days = default_days
        conn.execute(insert(trash_records).values(
            id=trash_id,
            record_type=record_type,
            record_id=record_id,
            owner_user_id=owner_user_id,
            deleted_by_user_id=deleted_by_user_id,
            snapshot_json=_json(snapshot),
            deleted_at=now,
            purge_after=now + timedelta(days=days),
        ))
        return trash_id

    def delete_artifact(self, user: CurrentUser, artifact_id: str) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(select(annotation_artifacts).where(
                annotation_artifacts.c.id == artifact_id
            )).first()
            if not row:
                return False
            if not user.is_admin and row._mapping["owner_user_id"] != user.id:
                raise PermissionError("Artifact belongs to another engineer")
            case_rows = conn.execute(select(engineer_tolerance_cases).where(
                engineer_tolerance_cases.c.artifact_id == artifact_id
            )).all()
            artifact_tag_ids = conn.execute(select(artifact_tag_assignments.c.tag_id).where(
                artifact_tag_assignments.c.artifact_id == artifact_id
            )).scalars().all()
            case_ids = [case_row._mapping["id"] for case_row in case_rows]
            case_tag_rows = conn.execute(select(engineer_case_tag_assignments).where(
                engineer_case_tag_assignments.c.case_record_id.in_(case_ids)
            )).all() if case_ids else []
            self._trash_conn(
                conn,
                "ANNOTATION_ARTIFACT",
                artifact_id,
                row._mapping["owner_user_id"],
                user.id,
                {
                    "artifact": _snapshot_row(row),
                    "cases": [_snapshot_row(case_row) for case_row in case_rows],
                    "artifact_tag_ids": list(artifact_tag_ids),
                    "case_tags": [_snapshot_row(case_tag_row) for case_tag_row in case_tag_rows],
                },
            )
            if case_ids:
                conn.execute(delete(engineer_case_tag_assignments).where(
                    engineer_case_tag_assignments.c.case_record_id.in_(case_ids)
                ))
            conn.execute(delete(artifact_tag_assignments).where(
                artifact_tag_assignments.c.artifact_id == artifact_id
            ))
            conn.execute(delete(engineer_tolerance_cases).where(
                engineer_tolerance_cases.c.artifact_id == artifact_id
            ))
            conn.execute(delete(annotation_artifacts).where(annotation_artifacts.c.id == artifact_id))
            self._audit_conn(conn, user.id, "ANNOTATION_ARTIFACT_TRASHED", "ARTIFACT", artifact_id, {})
        return True

    def personal_cases(
        self,
        user_id: str,
        tag_ids: Optional[Iterable[str]] = None,
        match_mode: str = "ANY",
    ) -> List[Dict[str, Any]]:
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
            assignment_rows = conn.execute(
                select(engineer_case_tag_assignments).select_from(
                    engineer_case_tag_assignments.join(
                        engineer_tolerance_cases,
                        engineer_case_tag_assignments.c.case_record_id == engineer_tolerance_cases.c.id,
                    )
                ).where(engineer_tolerance_cases.c.owner_user_id == user_id)
            ).all()
        tags_by_case: Dict[str, List[str]] = {}
        for assignment in assignment_rows:
            tags_by_case.setdefault(assignment._mapping["case_record_id"], []).append(
                assignment._mapping["tag_id"]
            )
        selected_tags = set(str(value) for value in (tag_ids or []) if value)
        require_all = str(match_mode).upper() == "ALL"
        result = []
        for row in rows:
            value = dict(row._mapping)
            value["tag_ids"] = tags_by_case.get(value["id"], [])
            if selected_tags:
                assigned = set(value["tag_ids"])
                matches = selected_tags.issubset(assigned) if require_all else bool(selected_tags & assigned)
                if not matches:
                    continue
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
            row = conn.execute(select(engineer_tolerance_cases).where(and_(
                engineer_tolerance_cases.c.id == case_record_id,
                engineer_tolerance_cases.c.owner_user_id == user_id,
            ))).first()
            if not row:
                return False
            assigned_tag_ids = conn.execute(select(engineer_case_tag_assignments.c.tag_id).where(
                engineer_case_tag_assignments.c.case_record_id == case_record_id
            )).scalars().all()
            self._trash_conn(
                conn,
                "ENGINEER_TOLERANCE_CASE",
                case_record_id,
                user_id,
                user_id,
                {"case": _snapshot_row(row), "tag_ids": list(assigned_tag_ids)},
            )
            conn.execute(delete(engineer_case_tag_assignments).where(
                engineer_case_tag_assignments.c.case_record_id == case_record_id
            ))
            conn.execute(delete(engineer_tolerance_cases).where(and_(
                engineer_tolerance_cases.c.id == case_record_id,
                engineer_tolerance_cases.c.owner_user_id == user_id,
            )))
            self._audit_conn(
                conn,
                user_id,
                "ENGINEER_TOLERANCE_CASE_TRASHED",
                "ENGINEER_TOLERANCE_CASE",
                case_record_id,
                {},
            )
        return True

    def clear_personal_cases(self, user_id: str) -> int:
        with self.engine.begin() as conn:
            rows = conn.execute(select(engineer_tolerance_cases).where(
                engineer_tolerance_cases.c.owner_user_id == user_id
            )).all()
            for row in rows:
                assigned_tag_ids = conn.execute(select(engineer_case_tag_assignments.c.tag_id).where(
                    engineer_case_tag_assignments.c.case_record_id == row._mapping["id"]
                )).scalars().all()
                self._trash_conn(
                    conn,
                    "ENGINEER_TOLERANCE_CASE",
                    row._mapping["id"],
                    user_id,
                    user_id,
                    {"case": _snapshot_row(row), "tag_ids": list(assigned_tag_ids)},
                )
            case_ids = [row._mapping["id"] for row in rows]
            if case_ids:
                conn.execute(delete(engineer_case_tag_assignments).where(
                    engineer_case_tag_assignments.c.case_record_id.in_(case_ids)
                ))
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

    def list_trash(self, user: CurrentUser, owner_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        self.purge_expired_trash(actor_user_id=None)
        stmt = select(trash_records).order_by(trash_records.c.deleted_at.desc())
        if not user.is_admin:
            stmt = stmt.where(trash_records.c.owner_user_id == user.id)
        elif owner_user_id:
            stmt = stmt.where(trash_records.c.owner_user_id == owner_user_id)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        result: List[Dict[str, Any]] = []
        for row in rows:
            value = dict(row._mapping)
            value.pop("snapshot_json", None)
            for key in ("deleted_at", "purge_after"):
                value[key] = value[key].isoformat()
            result.append(value)
        return result

    def restore_trash(self, user: CurrentUser, trash_id: str) -> Dict[str, Any]:
        with self.engine.begin() as conn:
            row = conn.execute(select(trash_records).where(trash_records.c.id == trash_id)).first()
            if not row:
                raise KeyError("Trash record not found")
            item = row._mapping
            if not user.is_admin and item["owner_user_id"] != user.id:
                raise PermissionError("Trash record belongs to another engineer")
            snapshot = _load_json(item["snapshot_json"], {})
            if item["record_type"] == "ANNOTATION_ARTIFACT":
                conn.execute(insert(annotation_artifacts).values(
                    **_restore_row(annotation_artifacts, snapshot["artifact"])
                ))
                for case in snapshot.get("cases", []):
                    conn.execute(insert(engineer_tolerance_cases).values(
                        **_restore_row(engineer_tolerance_cases, case)
                    ))
                for tag_id in snapshot.get("artifact_tag_ids", []):
                    conn.execute(insert(artifact_tag_assignments).values(
                        artifact_id=item["record_id"], tag_id=tag_id
                    ))
                for assignment in snapshot.get("case_tags", []):
                    conn.execute(insert(engineer_case_tag_assignments).values(
                        **_restore_row(engineer_case_tag_assignments, assignment)
                    ))
            elif item["record_type"] == "ENGINEER_TOLERANCE_CASE":
                conn.execute(insert(engineer_tolerance_cases).values(
                    **_restore_row(engineer_tolerance_cases, snapshot["case"])
                ))
                for tag_id in snapshot.get("tag_ids", []):
                    conn.execute(insert(engineer_case_tag_assignments).values(
                        case_record_id=item["record_id"], tag_id=tag_id
                    ))
            else:
                raise ValueError("Unsupported trash record type")
            conn.execute(delete(trash_records).where(trash_records.c.id == trash_id))
            self._audit_conn(conn, user.id, "TRASH_RECORD_RESTORED", item["record_type"], item["record_id"], {
                "trash_id": trash_id
            })
        return {"restored": True, "record_type": item["record_type"], "record_id": item["record_id"]}

    def purge_trash(self, user: CurrentUser, trash_id: str) -> bool:
        if not user.is_admin:
            raise PermissionError("Only administrators may permanently delete trash")
        with self.engine.begin() as conn:
            row = conn.execute(select(trash_records).where(trash_records.c.id == trash_id)).first()
            if not row:
                return False
            conn.execute(delete(trash_records).where(trash_records.c.id == trash_id))
            self._audit_conn(conn, user.id, "TRASH_RECORD_PURGED", row._mapping["record_type"], row._mapping["record_id"], {
                "trash_id": trash_id
            })
        return True

    def purge_expired_trash(self, actor_user_id: Optional[str]) -> int:
        now = _utcnow()
        with self.engine.begin() as conn:
            result = conn.execute(delete(trash_records).where(trash_records.c.purge_after <= now))
            count = int(result.rowcount or 0)
            if count and actor_user_id:
                self._audit_conn(conn, actor_user_id, "EXPIRED_TRASH_PURGED", "SYSTEM", None, {"count": count})
        return count

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
