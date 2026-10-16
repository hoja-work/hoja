"""SQLAlchemy Core storage, engine configuration, migration, and seeding."""

from __future__ import annotations

import hashlib
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    inspect,
    select,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
CURRENT_SCHEMA_REVISION = 28


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def gen_token() -> str:
    return "lin_api_" + secrets.token_urlsafe(30)


def gen_session_token() -> str:
    return secrets.token_urlsafe(48)


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
organization = Table(
    "organization",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("url_key", String, nullable=False, unique=True),
    Column("created_at", String),
    Column("updated_at", String),
)

global_identity = Table(
    "global_identity",
    metadata,
    Column("id", String, primary_key=True),
    Column("email", String, nullable=False, index=True),
    Column("created_at", String, nullable=False),
)

organization_membership = Table(
    "organization_membership",
    metadata,
    Column("id", String, primary_key=True),
    Column("identity_id", String, ForeignKey("global_identity.id"), nullable=False, index=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("profile_user_id", String, ForeignKey("user.id"), nullable=False, unique=True),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", String, nullable=False),
    UniqueConstraint("identity_id", "organization_id", name="uq_membership_identity_org"),
)

identity_credential = Table(
    "identity_credential",
    metadata,
    Column("identity_id", String, ForeignKey("global_identity.id"), primary_key=True),
    Column("password_hash", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

membership_invitation = Table(
    "membership_invitation",
    metadata,
    Column("id", String, primary_key=True),
    Column("token_hash", String, nullable=False, unique=True, index=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("profile_user_id", String, ForeignKey("user.id"), nullable=False, unique=True),
    Column("created_by_profile_id", String, ForeignKey("user.id"), nullable=False),
    Column("created_at", String, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("accepted_at", String),
    Column("accepted_identity_id", String, ForeignKey("global_identity.id")),
)

api_key = Table(
    "api_key",
    metadata,
    Column("id", String, primary_key=True),
    Column("token_hash", String, nullable=False, index=True),
    Column("label", String),
    Column("access", String, nullable=False, server_default="read_write"),
    Column("hint", String),
    Column("expires_at", String),
    Column("last_used_at", String),
    Column("user_id", String, ForeignKey("user.id")),
    Column("organization_id", String, ForeignKey("organization.id")),
    Column("created_at", String),
    Column("revoked_at", String),
)

password_credential = Table(
    "password_credential",
    metadata,
    Column("user_id", String, ForeignKey("user.id"), primary_key=True),
    Column("password_hash", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

browser_session = Table(
    "browser_session",
    metadata,
    Column("id", String, primary_key=True),
    Column("token_hash", String, nullable=False, unique=True, index=True),
    Column("user_id", String, ForeignKey("user.id"), nullable=False, index=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("created_at", String, nullable=False),
    Column("expires_at", String, nullable=False, index=True),
    Column("revoked_at", String),
)

oauth_client = Table(
    "oauth_client",
    metadata,
    Column("client_id", String, primary_key=True),
    Column("client_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
)

oauth_authorization = Table(
    "oauth_authorization",
    metadata,
    Column("code_hash", String, primary_key=True),
    Column("client_id", String, ForeignKey("oauth_client.client_id"), nullable=False, index=True),
    Column("user_id", String, ForeignKey("user.id"), nullable=False, index=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("scopes", Text, nullable=False),
    Column("code_challenge", String, nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("expires_at", String, nullable=False, index=True),
    Column("consumed_at", String),
    Column("resource", Text),
)

oauth_pending = Table(
    "oauth_pending", metadata,
    Column("pending_hash", String, primary_key=True),
    Column("client_id", String, ForeignKey("oauth_client.client_id"), nullable=False, index=True),
    Column("user_id", String, ForeignKey("user.id"), index=True),
    Column("organization_id", String, ForeignKey("organization.id"), index=True),
    Column("scopes", Text, nullable=False), Column("code_challenge", String, nullable=False),
    Column("redirect_uri", Text, nullable=False), Column("redirect_explicit", Boolean, nullable=False),
    Column("state", Text), Column("csrf_token", String, nullable=False), Column("resource", Text),
    Column("expires_at", String, nullable=False, index=True), Column("consumed_at", String),
)

oauth_access_token = Table(
    "oauth_access_token",
    metadata,
    Column("token_hash", String, primary_key=True),
    Column("client_id", String, ForeignKey("oauth_client.client_id"), nullable=False, index=True),
    Column("user_id", String, ForeignKey("user.id"), nullable=False, index=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("scopes", Text, nullable=False),
    Column("expires_at", String, nullable=False, index=True),
    Column("grant_id", String, nullable=False, index=True),
    Column("resource", Text),
    Column("revoked_at", String),
)

oauth_refresh_token = Table(
    "oauth_refresh_token",
    metadata,
    Column("token_hash", String, primary_key=True),
    Column("client_id", String, ForeignKey("oauth_client.client_id"), nullable=False, index=True),
    Column("user_id", String, ForeignKey("user.id"), nullable=False, index=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("scopes", Text, nullable=False),
    Column("expires_at", String, nullable=False, index=True),
    Column("grant_id", String, nullable=False, index=True),
    Column("resource", Text),
    Column("revoked_at", String),
)

user_preference = Table(
    "user_preference",
    metadata,
    Column("user_id", String, ForeignKey("user.id"), primary_key=True),
    Column("preference_key", String, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", String, nullable=False),
)

connector_account = Table(
    "connector_account",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, nullable=False, index=True),
    Column("owner_user_id", String, index=True),
    Column("provider", String, nullable=False),
    Column("external_account", String, nullable=False),
    Column("scopes", Text, nullable=False),
    Column("encrypted_refresh_token", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("revoked_at", String),
)

connector_action = Table(
    "connector_action",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, nullable=False, index=True),
    Column("account_id", String, ForeignKey("connector_account.id"), nullable=False, index=True),
    Column("requested_by_user_id", String, nullable=False),
    Column("tool", String, nullable=False),
    Column("args_json", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("result_summary", Text),
    Column("created_at", String, nullable=False),
    Column("resolved_at", String),
)

model_provider = Table(
    "model_provider",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("model_id", String, nullable=False),
    Column("base_url", String),
    Column("encrypted_api_key", String, nullable=False),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

agent_conversation = Table(
    "agent_conversation",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, nullable=False, index=True),
    Column("user_id", String, nullable=False, index=True),
    Column("title", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

agent_message = Table(
    "agent_message",
    metadata,
    Column("id", String, primary_key=True),
    Column("conversation_id", String, ForeignKey("agent_conversation.id"), nullable=False, index=True),
    Column("role", String, nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", String, nullable=False),
)

schema_revision = Table(
    "schema_revision",
    metadata,
    Column("revision", Integer, primary_key=True),
    Column("applied_at", String, nullable=False),
)

rate_limit_event = Table(
    "rate_limit_event",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("bucket", String, nullable=False, index=True),
    Column("key_hash", String, nullable=False, index=True),
    Column("created_at", Float, nullable=False, index=True),
)

oauth_state_nonce = Table(
    "oauth_state_nonce",
    metadata,
    Column("nonce_hash", String, primary_key=True),
    Column("expires_at", Float, nullable=False, index=True),
)

user = Table(
    "user",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), index=True),
    Column("name", String),
    Column("display_name", String),
    Column("email", String),
    Column("active", Boolean, default=True),
    Column("admin", Boolean, default=False),
    Column("avatar_url", String),
    Column("url", String),
    Column("timezone", String),
    Column("status_emoji", String),
    Column("status_label", String),
    Column("created_at", String),
    Column("updated_at", String),
    Column("archived_at", String),
)

team = Table(
    "team",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), index=True),
    Column("key", String, nullable=False),
    Column("name", String, nullable=False),
    Column("description", String),
    Column("color", String),
    Column("icon", String),
    Column("private", Boolean, default=False),
    Column("timezone", String),
    Column("cadence_enabled", Boolean, nullable=False, default=False),
    Column("cadence_weekday", Integer),
    Column("cadence_anchor_at", String),
    Column("cadence_policy_version", Integer),
    Column("cadence_duration_weeks", Integer, nullable=False, default=1),
    Column("cadence_cooldown_weeks", Integer, nullable=False, default=0),
    Column("cadence_upcoming_count", Integer, nullable=False, default=2),
    Column("issue_counter", Integer, default=0),
    Column("active_cycle_id", String),
    Column("auto_add_started", Boolean, nullable=False, default=True),
    Column("auto_add_completed", Boolean, nullable=False, default=True),
    Column("auto_add_backlog", Boolean, nullable=False, default=True),
    Column("default_state_id", String),
    Column("created_at", String),
    Column("updated_at", String),
    Column("archived_at", String),
    UniqueConstraint("organization_id", "key", name="uq_team_org_key"),
)

workflow_state = Table(
    "workflow_state",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), index=True),
    Column("team_id", String, ForeignKey("team.id"), index=True),
    Column("name", String, nullable=False),
    Column("type", String, nullable=False),
    Column("color", String),
    Column("description", String),
    Column("position", Float, default=0),
    Column("created_at", String),
    Column("updated_at", String),
    Column("archived_at", String),
)

team_member = Table(
    "team_member",
    metadata,
    Column("team_id", String, ForeignKey("team.id"), primary_key=True),
    Column("user_id", String, ForeignKey("user.id"), primary_key=True),
)

issue = Table(
    "issue",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), index=True),
    Column("team_id", String, ForeignKey("team.id"), index=True),
    Column("number", Integer),
    Column("identifier", String, index=True),
    Column("title", String, nullable=False),
    Column("description", Text),
    Column("priority", Integer, default=0),
    Column("priority_label", String),
    Column("estimate", Float),
    Column("state_id", String, ForeignKey("workflow_state.id"), index=True),
    Column("assignee_id", String, ForeignKey("user.id"), index=True),
    Column("creator_id", String, ForeignKey("user.id")),
    Column("project_id", String, index=True),
    Column("cycle_id", String),
    Column("parent_id", String),
    Column("url", String),
    Column("branch_name", String),
    Column("due_date", String),
    Column("started_at", String),
    Column("state_changed_at", String),
    Column("completed_at", String),
    Column("canceled_at", String),
    Column("snoozed_until_at", String),
    Column("created_at", String, index=True),
    Column("updated_at", String, index=True),
    Column("archived_at", String),
    UniqueConstraint("team_id", "number", name="uq_issue_team_number"),
)

issue_label = Table(
    "issue_label",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), index=True),
    Column("team_id", String, ForeignKey("team.id")),
    Column("name", String, nullable=False),
    Column("color", String),
    Column("description", String),
    Column("created_at", String),
    Column("updated_at", String),
    Column("archived_at", String),
)

issue_label_link = Table(
    "issue_label_link",
    metadata,
    Column("issue_id", String, ForeignKey("issue.id"), primary_key=True),
    Column("label_id", String, ForeignKey("issue_label.id"), primary_key=True),
)

issue_subscriber = Table(
    "issue_subscriber",
    metadata,
    Column("issue_id", String, ForeignKey("issue.id"), primary_key=True),
    Column("user_id", String, ForeignKey("user.id"), primary_key=True),
)

comment = Table(
    "comment",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), index=True),
    Column("issue_id", String, ForeignKey("issue.id"), index=True),
    Column("body", Text, nullable=False),
    Column("user_id", String, ForeignKey("user.id")),
    Column("url", String),
    Column("created_at", String),
    Column("updated_at", String),
    Column("archived_at", String),
)

change_event = Table(
    "change_event",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("entity", String, nullable=False),
    Column("entity_id", String, nullable=False),
    Column("action", String, nullable=False),
    Column("team_id", String),
    Column("created_at", String, nullable=False),
)

issue_activity = Table(
    "issue_activity",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("issue_id", String, ForeignKey("issue.id"), nullable=False, index=True),
    Column("actor_id", String, ForeignKey("user.id")),
    Column("event_type", String, nullable=False),
    Column("changes", Text),
    Column("created_at", String, nullable=False, index=True),
)

initiative = Table(
    "initiative",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("summary", Text),
    Column("status", String, nullable=False),
    Column("priority", Integer, nullable=False, default=0),
    Column("owner_id", String, ForeignKey("user.id")),
    Column("creator_id", String, ForeignKey("user.id")),
    Column("target_date", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("archived_at", String),
)

initiative_project = Table(
    "initiative_project",
    metadata,
    Column("organization_id", String, ForeignKey("organization.id"), primary_key=True),
    Column("initiative_id", String, ForeignKey("initiative.id"), primary_key=True),
    Column("project_id", String, ForeignKey("project.id"), primary_key=True),
)

inbox_notification = Table(
    "inbox_notification",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("recipient_id", String, ForeignKey("user.id"), nullable=False, index=True),
    Column("issue_id", String, ForeignKey("issue.id"), nullable=False, index=True),
    Column("actor_id", String, ForeignKey("user.id")),
    Column("kind", String, nullable=False),
    Column("created_at", String, nullable=False, index=True),
    Column("read_at", String),
    Column("archived_at", String),
)

attachment = Table(
    "attachment",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), index=True),
    Column("issue_id", String, ForeignKey("issue.id"), index=True),
    Column("creator_id", String, ForeignKey("user.id")),
    Column("url", String, nullable=False),
    Column("title", String, nullable=False),
    Column("subtitle", String),
    Column("created_at", String),
)

project = Table(
    "project",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), index=True),
    Column("name", String, nullable=False),
    Column("description", String),
    Column("slug_id", String),
    Column("icon", String),
    Column("color", String),
    Column("state", String, default="backlog"),
    Column("progress", Float, default=0),
    Column("lead_id", String, ForeignKey("user.id")),
    Column("creator_id", String, ForeignKey("user.id")),
    Column("start_date", String),
    Column("target_date", String),
    Column("url", String),
    Column("created_at", String),
    Column("updated_at", String),
    Column("archived_at", String),
)

project_update = Table(
    "project_update",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("project_id", String, ForeignKey("project.id"), nullable=False, index=True),
    Column("status", String),
    Column("body", Text, nullable=False),
    Column("created_at", String, nullable=False, index=True),
    Column("actor_id", String, ForeignKey("user.id")),
    Column("progress", Float),
    Column("health", String),
)

project_member = Table(
    "project_member",
    metadata,
    Column("project_id", String, ForeignKey("project.id"), primary_key=True),
    Column("user_id", String, ForeignKey("user.id"), primary_key=True),
)

project_team = Table(
    "project_team",
    metadata,
    Column("project_id", String, ForeignKey("project.id"), primary_key=True),
    Column("team_id", String, ForeignKey("team.id"), primary_key=True),
)

cycle = Table(
    "cycle",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), index=True),
    Column("team_id", String, ForeignKey("team.id"), index=True),
    Column("number", Integer),
    Column("name", String),
    Column("description", String),
    Column("starts_at", String),
    Column("ends_at", String),
    Column("cadence_boundary_at", String),
    Column("completed_at", String),
    Column("progress", Float, default=0),
    Column("created_at", String),
    Column("updated_at", String),
    Column("archived_at", String),
    UniqueConstraint("team_id", "number", name="uq_cycle_team_number"),
    UniqueConstraint("team_id", "cadence_boundary_at", name="uq_cycle_team_cadence_boundary"),
)


# --------------------------------------------------------------------------
# Seed workflow states (name, type, position)
# --------------------------------------------------------------------------
SEED_STATES = [
    ("Backlog", "backlog", 0, "#bec2c8"),
    ("Todo", "unstarted", 1, "#e2e2e2"),
    ("In Progress", "started", 2, "#f2c94c"),
    ("In Review", "started", 3, "#5e6ad2"),
    ("Done", "completed", 4, "#5e6ad2"),
    ("Canceled", "canceled", 5, "#95a2b3"),
]

PRIORITY_LABELS = {0: "No priority", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}


def default_db_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    d = Path(base) / "cliniar"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path_for(tenant: str) -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share"))
    canonical = base / "cliniar" / f"{tenant}.db"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    return canonical


def resolve_database_target(
    *,
    database_url: str | None = None,
    db_path: str | Path | None = None,
    tenant: str = "default",
) -> str:
    """Resolve explicit URL, environment URL, then the SQLite path fallback."""
    if database_url:
        return database_url
    if env_url := os.environ.get("CLINIAR_DATABASE_URL"):
        return env_url
    return str(db_path if db_path is not None else db_path_for(tenant))


def _as_sqlalchemy_url(target: str | Path) -> str:
    value = os.fspath(target)
    if value.startswith("postgres://"):
        return f"postgresql+psycopg://{value.removeprefix('postgres://')}"
    if value.startswith("postgresql://"):
        return f"postgresql+psycopg://{value.removeprefix('postgresql://')}"
    if "://" in value or value.startswith("sqlite:"):
        return value
    return f"sqlite:///{value}"


def display_database_target(target: str | Path) -> str:
    """Return a target safe for logs, hiding URL passwords."""
    value = os.fspath(target)
    if "://" not in value and not value.startswith("sqlite:"):
        return value
    return make_url(value).render_as_string(hide_password=True)


def make_engine(target: str | Path) -> Engine:
    url = _as_sqlalchemy_url(target)
    backend = make_url(url).get_backend_name()
    kwargs = {"future": True}
    if backend == "postgresql":
        kwargs["pool_pre_ping"] = True
    eng = create_engine(url, **kwargs)

    if backend == "sqlite":

        @event.listens_for(eng, "connect")
        def _set_pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return eng


def _apply_token_hash_uniqueness(conn) -> None:
    duplicate = conn.execute(
        select(api_key.c.token_hash, func.count())
        .group_by(api_key.c.token_hash)
        .having(func.count() > 1)
        .limit(1)
    ).first()
    if duplicate:
        raise RuntimeError(
            "Cannot apply schema revision 2: duplicate API token hashes exist. "
            "Revoke duplicate keys before migrating."
        )
    indexes = inspect(conn).get_indexes(api_key.name)
    if not any(index["name"] == "uq_api_key_token_hash" for index in indexes):
        conn.exec_driver_sql("CREATE UNIQUE INDEX uq_api_key_token_hash ON api_key (token_hash)")


def _apply_identity_memberships(conn) -> None:
    """Backfill a distinct identity and membership for every legacy profile."""
    global_identity.create(conn, checkfirst=True)
    organization_membership.create(conn, checkfirst=True)
    identity_credential.create(conn, checkfirst=True)
    profiles = conn.execute(select(user)).mappings().all()
    for profile in profiles:
        identity_id = new_id()
        created_at = profile.get("created_at") or now_iso()
        conn.execute(
            global_identity.insert().values(
                id=identity_id,
                email=(profile.get("email") or "").strip().lower(),
                created_at=created_at,
            )
        )
        conn.execute(
            organization_membership.insert().values(
                id=new_id(),
                identity_id=identity_id,
                organization_id=profile["organization_id"],
                profile_user_id=profile["id"],
                active=bool(profile.get("active", True)) and profile.get("archived_at") is None,
                created_at=created_at,
            )
        )
        old_credential = (
            conn.execute(
                select(password_credential).where(password_credential.c.user_id == profile["id"])
            )
            .mappings()
            .first()
        )
        if old_credential:
            conn.execute(
                identity_credential.insert().values(
                    identity_id=identity_id,
                    password_hash=old_credential["password_hash"],
                    created_at=old_credential["created_at"],
                    updated_at=old_credential["updated_at"],
                )
            )


def _apply_team_cadence(conn) -> None:
    """Add opt-in cadence policy and an idempotent cycle-boundary key."""
    inspector = inspect(conn)
    existing = {column["name"] for column in inspector.get_columns("team")}
    additions = {
        "cadence_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "cadence_weekday": "INTEGER",
        "cadence_anchor_at": "VARCHAR",
        "cadence_policy_version": "INTEGER",
        "cadence_duration_weeks": "INTEGER NOT NULL DEFAULT 1",
        "cadence_cooldown_weeks": "INTEGER NOT NULL DEFAULT 0",
        "cadence_upcoming_count": "INTEGER NOT NULL DEFAULT 2",
    }
    for name, sql_type in additions.items():
        if name not in existing:
            conn.exec_driver_sql(f"ALTER TABLE team ADD COLUMN {name} {sql_type}")
    issue_columns = {column["name"] for column in inspect(conn).get_columns("issue")}
    if "state_changed_at" not in issue_columns:
        conn.exec_driver_sql("ALTER TABLE issue ADD COLUMN state_changed_at VARCHAR")
        conn.exec_driver_sql("UPDATE issue SET state_changed_at = COALESCE(updated_at, created_at)")
    cycle_columns = {column["name"] for column in inspect(conn).get_columns("cycle")}
    if "cadence_boundary_at" not in cycle_columns:
        conn.exec_driver_sql("ALTER TABLE cycle ADD COLUMN cadence_boundary_at VARCHAR")
    indexes = inspect(conn).get_indexes("cycle")
    constraints = inspect(conn).get_unique_constraints("cycle")
    names = {item.get("name") for item in indexes + constraints}
    if "uq_cycle_team_cadence_boundary" not in names:
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_cycle_team_cadence_boundary "
            "ON cycle (team_id, cadence_boundary_at)"
        )


def _apply_team_auto_add_backlog(conn) -> None:
    """Add the backlog auto-add-to-cycle toggle on the team table.

    Requested by the workspace owner: backlog issues should join the
    current cycle automatically, like started/completed ones. Defaults on.
    """
    inspector = inspect(conn)
    existing = {column["name"] for column in inspector.get_columns("team")}
    if "auto_add_backlog" not in existing:
        conn.exec_driver_sql("ALTER TABLE team ADD COLUMN auto_add_backlog BOOLEAN NOT NULL DEFAULT TRUE")


def _apply_oauth_security(conn):
    oauth_pending.create(conn, checkfirst=True)
    for table in (oauth_authorization, oauth_access_token, oauth_refresh_token):
        if "resource" not in {c["name"] for c in inspect(conn).get_columns(table.name)}:
            conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN resource TEXT")


def _apply_api_key_access(conn):
    if "access" not in {c["name"] for c in inspect(conn).get_columns("api_key")}:
        conn.exec_driver_sql(
            "ALTER TABLE api_key ADD COLUMN access VARCHAR NOT NULL DEFAULT 'read_write'"
        )


# Non-secret, org-scoped tables whose every write is published as a change event.
# Secret tables (api_key, sessions, credentials, OAuth, invitations, global identity)
# are deliberately absent so they can never produce an event.
_CHANGE_CAPTURE = (
    # (table, entity, primary key, team_id expression, organization_id expression)
    # Expressions use {row} for NEW/OLD. None means NULL (no team scope).
    ("issue", "issue", "id", "{row}.team_id", "{row}.organization_id"),
    ("comment", "comment", "id", None, "{row}.organization_id"),
    ("attachment", "attachment", "id", None, "{row}.organization_id"),
    ("project", "project", "id", None, "{row}.organization_id"),
    ("project_update", "projectUpdate", "id", None, "{row}.organization_id"),
    ("initiative", "initiative", "id", None, "{row}.organization_id"),
    ("cycle", "cycle", "id", "{row}.team_id", "{row}.organization_id"),
    ("issue_label", "label", "id", "{row}.team_id", "{row}.organization_id"),
    ("workflow_state", "workflowState", "id", "{row}.team_id", "{row}.organization_id"),
    ("team", "team", "id", "{row}.id", "{row}.organization_id"),
    ("team_member", "teamMember", "team_id", "{row}.team_id",
     "(SELECT organization_id FROM team WHERE id = {row}.team_id)"),
    ("inbox_notification", "notification", "id", None, "{row}.organization_id"),
    ("user", "user", "id", None, "{row}.organization_id"),
    ("organization_membership", "member", "id", None, "{row}.organization_id"),
)


def _change_trigger_sql(dialect: str, table_name: str, entity: str, pk: str,
                        team_expr: str | None, org_expr: str):
    """Return one CREATE TRIGGER statement per insert/update/delete for a table."""
    statements = []
    for op, label in (("insert", "create"), ("update", "update"), ("delete", "delete")):
        row = "OLD" if op == "delete" else "NEW"
        team_value = team_expr.format(row=row) if team_expr else "NULL"
        org = org_expr.format(row=row)
        pk_value = f"{row}.{pk}"
        body = (
            "INSERT INTO change_event (organization_id, entity, entity_id, action, team_id, created_at) "
            f"VALUES ({org}, '{entity}', CAST({pk_value} AS TEXT), '{label}', "
            f"{team_value}, strftime('%Y-%m-%dT%H:%M:%fZ','now'));"
        )
        statements.append(
            f"CREATE TRIGGER IF NOT EXISTS trg_chg_{table_name}_{label} "
            f"AFTER {op.upper()} ON {table_name} FOR EACH ROW BEGIN {body} END;"
        )
    return statements


def _postgres_change_trigger_sql(table_name: str, entity: str, pk: str,
                                 team_expr: str | None, org_expr: str) -> list[str]:
    """One PL/pgSQL function per table (shared body shape) and one trigger per operation."""
    function = f"chg_{table_name}"
    team_new = team_expr.format(row="NEW") if team_expr else "NULL"
    team_old = team_expr.format(row="OLD") if team_expr else "NULL"
    org_new = org_expr.format(row="NEW")
    org_old = org_expr.format(row="OLD")
    pk_new = f"NEW.{pk}"
    pk_old = f"OLD.{pk}"
    statements = [
        f"""CREATE OR REPLACE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
  IF TG_OP = 'DELETE' THEN
    INSERT INTO change_event (organization_id, entity, entity_id, action, team_id, created_at)
    VALUES ({org_old}, '{entity}', CAST({pk_old} AS TEXT), 'delete', {team_old},
            to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'));
    RETURN OLD;
  END IF;
  INSERT INTO change_event (organization_id, entity, entity_id, action, team_id, created_at)
  VALUES ({org_new}, '{entity}', CAST({pk_new} AS TEXT),
          CASE WHEN TG_OP = 'INSERT' THEN 'create' ELSE 'update' END, {team_new},
          to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'));
  RETURN NEW;
END;
$fn$;""",
        f'DROP TRIGGER IF EXISTS trg_chg_{table_name} ON "{table_name}";',
        f'CREATE TRIGGER trg_chg_{table_name} AFTER INSERT OR UPDATE OR DELETE ON "{table_name}" '
        f"FOR EACH ROW EXECUTE FUNCTION {function}();",
    ]
    return statements


def _apply_realtime_capture(conn):
    dialect = conn.dialect.name
    for table_name, entity, pk, team_expr, org_expr in _CHANGE_CAPTURE:
        if dialect == "sqlite":
            for statement in _change_trigger_sql(dialect, table_name, entity, pk, team_expr, org_expr):
                conn.exec_driver_sql(statement)
        elif dialect == "postgresql":
            for statement in _postgres_change_trigger_sql(table_name, entity, pk, team_expr, org_expr):
                conn.exec_driver_sql(statement)
        else:
            raise RuntimeError(f"Realtime capture is not implemented for {dialect}")


def _apply_change_event_org_seq_index(conn):
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_change_event_org_seq "
        "ON change_event (organization_id, seq)"
    )


def _apply_api_key_lifecycle(conn):
    existing = {c["name"] for c in inspect(conn).get_columns("api_key")}
    for name, sql_type in (("hint", "VARCHAR"), ("expires_at", "VARCHAR"), ("last_used_at", "VARCHAR")):
        if name not in existing:
            conn.exec_driver_sql(f"ALTER TABLE api_key ADD COLUMN {name} {sql_type}")

def _apply_agent_conversation_title(conn) -> None:
    if "title" not in {c["name"] for c in inspect(conn).get_columns("agent_conversation")}:
        conn.exec_driver_sql("ALTER TABLE agent_conversation ADD COLUMN title VARCHAR")


def rate_limit_key_hash(key: str) -> str:
    """Rate-limit keys (emails, IPs) are stored only as SHA-256 digests."""
    return hashlib.sha256(key.encode()).hexdigest()


def rate_limit_blocked(
    engine: Engine, bucket: str, keys: list[str], limit: int, window_seconds: float
) -> bool:
    """True when any key has `limit` or more events inside the sliding window."""
    hashes = [rate_limit_key_hash(key) for key in keys]
    cutoff = time.time() - window_seconds
    with engine.connect() as conn:
        counts = conn.execute(
            select(rate_limit_event.c.key_hash, func.count())
            .where(
                rate_limit_event.c.bucket == bucket,
                rate_limit_event.c.key_hash.in_(hashes),
                rate_limit_event.c.created_at > cutoff,
            )
            .group_by(rate_limit_event.c.key_hash)
        ).all()
    return any(count >= limit for _, count in counts)


def rate_limit_record(engine: Engine, bucket: str, keys: list[str], window_seconds: float) -> None:
    """Record one event per key and prune this bucket's expired events."""
    now = time.time()
    with engine.begin() as conn:
        conn.execute(
            rate_limit_event.delete().where(
                rate_limit_event.c.bucket == bucket,
                rate_limit_event.c.created_at <= now - window_seconds,
            )
        )
        conn.execute(
            rate_limit_event.insert(),
            [
                {"bucket": bucket, "key_hash": rate_limit_key_hash(key), "created_at": now}
                for key in keys
            ],
        )


def _apply_team_cycle_automations(conn) -> None:
    """Add Linear-style auto-add-to-cycle toggles on the team table.

    Defaults match Linear (both on). Structured so a future cooldown
    attribution rule can read the same flags.
    """
    inspector = inspect(conn)
    existing = {column["name"] for column in inspector.get_columns("team")}
    additions = {
        "auto_add_started": "BOOLEAN NOT NULL DEFAULT TRUE",
        "auto_add_completed": "BOOLEAN NOT NULL DEFAULT TRUE",
    }
    for name, sql_type in additions.items():
        if name not in existing:
            conn.exec_driver_sql(f"ALTER TABLE team ADD COLUMN {name} {sql_type}")


_MIGRATIONS = {
    1: lambda _conn: None,
    2: _apply_token_hash_uniqueness,
    3: lambda conn: project_team.create(conn, checkfirst=True),
    4: lambda conn: attachment.create(conn, checkfirst=True),
    5: lambda conn: (
        password_credential.create(conn, checkfirst=True),
        browser_session.create(conn, checkfirst=True),
    ),
    6: _apply_identity_memberships,
    7: lambda conn: membership_invitation.create(conn, checkfirst=True),
    8: lambda conn: issue_activity.create(conn, checkfirst=True),
    9: lambda conn: project_update.create(conn, checkfirst=True),
    10: lambda conn: inbox_notification.create(conn, checkfirst=True),
    11: lambda conn: (
        initiative.create(conn, checkfirst=True),
        initiative_project.create(conn, checkfirst=True),
    ),
    12: _apply_team_cadence,
    13: lambda conn: user_preference.create(conn, checkfirst=True),
    14: lambda conn: (
        oauth_client.create(conn, checkfirst=True),
        oauth_authorization.create(conn, checkfirst=True),
        oauth_access_token.create(conn, checkfirst=True),
        oauth_refresh_token.create(conn, checkfirst=True),
    ),
    15: _apply_oauth_security,
    16: _apply_api_key_access,
    17: _apply_api_key_lifecycle,
    18: lambda conn: change_event.create(conn, checkfirst=True),
    19: _apply_realtime_capture,
    20: lambda conn: model_provider.create(conn, checkfirst=True),
    21: lambda conn: (
        agent_conversation.create(conn, checkfirst=True),
        agent_message.create(conn, checkfirst=True),
    ),
    22: _apply_agent_conversation_title,
    23: lambda conn: (
        connector_account.create(conn, checkfirst=True),
        connector_action.create(conn, checkfirst=True),
    ),
    24: lambda conn: rate_limit_event.create(conn, checkfirst=True),
    25: lambda conn: oauth_state_nonce.create(conn, checkfirst=True),
    26: _apply_team_cycle_automations,
    27: _apply_team_auto_add_backlog,
    28: _apply_change_event_org_seq_index,
}


def applied_schema_revision(engine: Engine) -> int:
    """Return the latest recorded revision, or zero for an unmigrated database."""
    if not inspect(engine).has_table(schema_revision.name):
        return 0
    with engine.connect() as conn:
        revisions = list(
            conn.execute(
                select(schema_revision.c.revision).order_by(schema_revision.c.revision)
            ).scalars()
        )
    if revisions != list(range(1, len(revisions) + 1)):
        raise RuntimeError("Schema revision ledger is not a contiguous prefix.")
    return revisions[-1] if revisions else 0


def schema_is_current(engine: Engine) -> bool:
    return applied_schema_revision(engine) == CURRENT_SCHEMA_REVISION


def migrate(engine: Engine) -> None:
    """Create a fresh schema and apply every outstanding ordered revision."""
    current = applied_schema_revision(engine)
    if current == 0:
        metadata.create_all(engine)
    if current > CURRENT_SCHEMA_REVISION:
        raise RuntimeError(
            f"Database revision {current} is newer than this server supports "
            f"({CURRENT_SCHEMA_REVISION})."
        )
    for revision in range(current + 1, CURRENT_SCHEMA_REVISION + 1):
        with engine.begin() as conn:
            _MIGRATIONS[revision](conn)
            conn.execute(schema_revision.insert().values(revision=revision, applied_at=now_iso()))


def seed_states_for_team(conn, org_id: str, team_id: str) -> str:
    """Insert seed workflow states; return the default (first unstarted) state id."""
    default_state_id = None
    ts = now_iso()
    for name, stype, pos, color in SEED_STATES:
        sid = new_id()
        conn.execute(
            workflow_state.insert().values(
                id=sid,
                organization_id=org_id,
                team_id=team_id,
                name=name,
                type=stype,
                color=color,
                position=float(pos),
                created_at=ts,
                updated_at=ts,
            )
        )
        if stype == "unstarted" and default_state_id is None:
            default_state_id = sid
    return default_state_id


def app_url() -> str:
    """Browser-facing application base URL for generated entity links."""
    return os.environ.get("CLINIAR_APP_URL", "http://localhost:8787").rstrip("/")


def entity_url(org_url_key: str, kind: str, entity_key: str) -> str:
    return f"{app_url()}/{org_url_key}/{kind}/{entity_key}"


def _default_org_url_key(org_name: str) -> str:
    key = "-".join(org_name.lower().split())
    if not key:
        raise ValueError("Organization name must produce a non-empty URL key.")
    return key


def seed_tenant(
    engine: Engine,
    *,
    org_name: str = "Local",
    org_url_key: str | None = None,
    team_key: str = "ENG",
    team_name: str = "Engineering",
    user_name: str = "Local User",
    user_email: str = "you@local",
    token: str | None = None,
    demo_issues: bool = True,
) -> dict:
    """Create one org, one admin user, one team (seeded states), an API token.
    Idempotent-ish: if the org already exists, returns existing token info is NOT
    possible (hash is one-way), so this is intended for fresh DBs.
    """
    token = token or gen_token()
    org_url_key = org_url_key or _default_org_url_key(org_name)
    ts = now_iso()
    try:
        with engine.begin() as conn:
            existing = conn.execute(
                select(organization.c.id).where(organization.c.url_key == org_url_key)
            ).first()
            if existing:
                raise ValueError(
                    f"Organization URL key '{org_url_key}' already exists; "
                    "choose a unique --org-key."
                )
            org_id = new_id()
            conn.execute(
                organization.insert().values(
                    id=org_id,
                    name=org_name,
                    url_key=org_url_key,
                    created_at=ts,
                    updated_at=ts,
                )
            )
            uid = new_id()
            conn.execute(
                user.insert().values(
                    id=uid,
                    organization_id=org_id,
                    name=user_name,
                    display_name=user_name,
                    email=user_email,
                    active=True,
                    admin=True,
                    url=entity_url(org_url_key, "profiles", uid),
                    timezone="UTC",
                    created_at=ts,
                    updated_at=ts,
                )
            )
            if inspect(conn).has_table(global_identity.name):
                identity_id = new_id()
                conn.execute(
                    global_identity.insert().values(
                        id=identity_id, email=user_email.strip().lower(), created_at=ts
                    )
                )
                conn.execute(
                    organization_membership.insert().values(
                        id=new_id(),
                        identity_id=identity_id,
                        organization_id=org_id,
                        profile_user_id=uid,
                        active=True,
                        created_at=ts,
                    )
                )
            conn.execute(
                api_key.insert().values(
                    id=new_id(),
                    token_hash=token_hash(token),
                    label="seed",
                    user_id=uid,
                    organization_id=org_id,
                    created_at=ts,
                )
            )
            tid = new_id()
            conn.execute(
                team.insert().values(
                    id=tid,
                    organization_id=org_id,
                    key=team_key.upper(),
                    name=team_name,
                    description=f"{team_name} team",
                    color="#5e6ad2",
                    private=False,
                    timezone="UTC",
                    issue_counter=0,
                    created_at=ts,
                    updated_at=ts,
                )
            )
            conn.execute(team_member.insert().values(team_id=tid, user_id=uid))
            default_state_id = seed_states_for_team(conn, org_id, tid)
            conn.execute(
                team.update().where(team.c.id == tid).values(default_state_id=default_state_id)
            )

            if demo_issues:
                _seed_demo_issues(
                    conn, org_id, tid, team_key.upper(), uid, default_state_id, org_url_key
                )
    except IntegrityError as exc:
        if "token_hash" in str(exc).lower() or "uq_api_key_token_hash" in str(exc):
            raise ValueError("API token already exists; choose a different token.") from exc
        raise ValueError(
            f"Organization URL key '{org_url_key}' already exists; choose a unique --org-key."
        ) from exc

    return {
        "token": token,
        "org_id": org_id,
        "user_id": uid,
        "team_id": tid,
        "team_key": team_key.upper(),
    }


def _seed_demo_issues(conn, org_id, team_id, team_key, uid, state_id, org_url_key):
    ts = now_iso()
    # bump counter to 2, create ENG-1, ENG-2
    samples = [
        ("Set up local backend", "First seeded issue", 2),
        ("Wire Cliniar base_url", "Point the CLI at the local server", 1),
    ]
    n = 0
    for title, desc, prio in samples:
        n += 1
        iid = new_id()
        ident = f"{team_key}-{n}"
        conn.execute(
            issue.insert().values(
                id=iid,
                organization_id=org_id,
                team_id=team_id,
                number=n,
                identifier=ident,
                title=title,
                description=desc,
                priority=prio,
                priority_label=PRIORITY_LABELS[prio],
                state_id=state_id,
                creator_id=uid,
                assignee_id=uid,
                url=entity_url(org_url_key, "issue", ident),
                branch_name=f"{uid[:8]}/{ident.lower()}-{title.lower().replace(' ', '-')[:20]}",
                created_at=ts,
                updated_at=ts,
            )
        )
    conn.execute(team.update().where(team.c.id == team_id).values(issue_counter=n))


def resolve_token_identity(
    engine: Engine,
    *,
    organization_ref: str | None = None,
    user_ref: str | None = None,
    email: str | None = None,
) -> tuple[str, str]:
    """Resolve the exact (user, organization) for a newly minted token."""
    if user_ref and email:
        raise ValueError("Choose either --user or --email, not both.")
    shared_database = engine.dialect.name != "sqlite"
    if shared_database and not organization_ref:
        raise ValueError("--organization is required for a shared database.")
    if shared_database and not (user_ref or email):
        raise ValueError("--user or --email is required for a shared database.")

    with engine.connect() as conn:
        if organization_ref:
            org_rows = conn.execute(
                select(organization.c.id)
                .where(
                    (organization.c.id == organization_ref)
                    | (organization.c.url_key == organization_ref)
                )
                .limit(2)
            ).all()
        else:
            org_rows = conn.execute(select(organization.c.id).limit(2)).all()
        if not org_rows:
            raise ValueError("No matching organization found; run 'cliniar-serve seed' first.")
        if len(org_rows) != 1:
            raise ValueError("Multiple organizations found; specify --organization.")
        org_id = org_rows[0][0]

        query = select(user.c.id).where(user.c.organization_id == org_id)
        if user_ref:
            query = query.where(user.c.id == user_ref)
        elif email:
            query = query.where(user.c.email == email)
        user_rows = conn.execute(query.limit(2)).all()
        if not user_rows:
            raise ValueError("No matching user found in the selected organization.")
        if len(user_rows) != 1:
            raise ValueError("Multiple users found; specify --user or --email.")
        return user_rows[0][0], org_id
