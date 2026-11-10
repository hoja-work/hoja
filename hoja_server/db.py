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
    BigInteger,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
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
    text,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
CURRENT_SCHEMA_REVISION = 49


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def gen_token() -> str:
    # Presentation only: lookups hash the whole token, so a prefixed key from
    # before this rename keeps authenticating. The 40-character body is fixed
    # by token_urlsafe(30); do not shorten it.
    return "hoja_" + secrets.token_urlsafe(30)


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

# --------------------------------------------------------------------------
# Hosted sign-up: Google-only identity, the pre-tenant session, and the gate
# rows the provisioner picks up. None of these tables is a workspace: an
# eligibility row is a *request*, not a database, container, route or DNS name.
# --------------------------------------------------------------------------

signup_identity = Table(
    "signup_identity",
    metadata,
    Column("id", String, primary_key=True),
    # `subject` is the Google `sub` claim: the stable identity key. `email` is
    # display/policy only, because a Workspace address can be reassigned.
    Column("provider", String, nullable=False, server_default="google"),
    Column("subject", String, nullable=False),
    Column("email", String, nullable=False),
    Column("email_verified", Boolean, nullable=False, server_default="0"),
    Column("name", String),
    Column("picture", Text),
    Column("hosted_domain", String),  # the `hd` claim, when Google returns one
    Column("google_verified_at", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("provider", "subject", name="uq_signup_identity_provider_subject"),
)

signup_flow = Table(
    "signup_flow",
    metadata,
    # One row per in-flight authorization-code flow. The browser holds only an
    # opaque binding id in a short-lived HttpOnly cookie; the state hash, nonce
    # and PKCE verifier stay server-side and are single use (consumed_at).
    Column("id", String, primary_key=True),
    Column("state_hash", String, nullable=False, unique=True, index=True),
    Column("binding_hash", String, nullable=False, index=True),
    Column("nonce", String, nullable=False),
    Column("code_verifier", String, nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("return_to", Text),
    Column("created_at", String, nullable=False),
    Column("expires_at", String, nullable=False, index=True),
    Column("consumed_at", String),
)

signup_session = Table(
    "signup_session",
    metadata,
    # Opaque server-side session for a google_verified person who has no
    # workspace yet, so browser_session (user_id/organization_id NOT NULL) cannot
    # hold it. Same machinery: a random token, only its hash stored, rotation on
    # login, revocation by row update.
    Column("id", String, primary_key=True),
    Column("token_hash", String, nullable=False, unique=True, index=True),
    Column("identity_id", String, ForeignKey("signup_identity.id"), nullable=False, index=True),
    Column("created_at", String, nullable=False),
    Column("expires_at", String, nullable=False, index=True),
    Column("revoked_at", String),
    Column("rotated_from", String),
    Column("client_ip", String),
    Column("user_agent", Text),
)

signup_invite = Table(
    "signup_invite",
    metadata,
    Column("id", String, primary_key=True),
    Column("email", String, nullable=False, unique=True, index=True),
    Column("note", Text),
    Column("created_by", String),
    Column("created_at", String, nullable=False),
    Column("expires_at", String),
    Column("consumed_at", String),
    Column("consumed_by_identity_id", String, ForeignKey("signup_identity.id")),
)

# The application email outbox. One row is one logical message; the unique
# idempotency_key is what makes a retried request send nothing twice.
mail_outbox = Table(
    "mail_outbox",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, index=True),
    Column("template", String, nullable=False),
    Column("recipient", String, nullable=False, index=True),
    Column("payload", Text, nullable=False),
    Column("idempotency_key", String, nullable=False, unique=True, index=True),
    Column("status", String, nullable=False, default="queued", index=True),
    Column("attempts", Integer, nullable=False, default=0),
    Column("created_at", String, nullable=False),
    Column("last_attempt_at", String),
    Column("sent_at", String),
    Column("provider_message_id", String),
    Column("last_error", Text),
)

#: What a tenant told the control plane about its own members.
#:
#: The control plane cannot see a tenant's membership rows, so the sign-in
#: directory was blind to everyone who had been invited rather than signed up.
#: A tenant announces each invitation it issues, signed with the key derived for
#: its own host; this table is the control plane's memory of that, and it is the
#: only reason `/signin` can route an invited person without guessing.
tenant_membership_hint = Table(
    "tenant_membership_hint",
    metadata,
    Column("id", String, primary_key=True),
    Column("slug", String, nullable=False, index=True),
    Column("email", String, nullable=False, index=True),
    Column("role", String, nullable=False, default="member"),
    Column("expires_at", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("slug", "email", name="uq_tenant_membership_hint"),
)

signup_eligibility = Table(
    "signup_eligibility",
    metadata,
    # The outbox the provisioner reads. status='eligible' + claimed_at IS NULL
    # is the queue; (subject, slug) is unique so a retried signup cannot enqueue
    # two workspaces, and idempotency_key makes a repeated POST return the same row.
    Column("id", String, primary_key=True),
    Column("idempotency_key", String, nullable=False, unique=True, index=True),
    Column("identity_id", String, ForeignKey("signup_identity.id"), nullable=False, index=True),
    Column("google_sub", String, nullable=False, index=True),
    Column("email", String, nullable=False),
    Column("slug", String, nullable=False, unique=True, index=True),
    Column("status", String, nullable=False, server_default="eligible"),
    Column("client_ip", String, nullable=False, index=True),
    Column("turnstile_hostname", String),
    Column("created_at", String, nullable=False),
    Column("claimed_at", String),
    # Provisioning lease/audit fields. ``attempts`` is incremented when a worker
    # claims the row; ``provisioned_at`` is written only after the tenant is
    # healthy through the edge. ``last_error`` holds a stable code, never a
    # secret or a raw subprocess error.
    Column("attempts", Integer, nullable=False, server_default="0", default=0),
    Column("provisioned_at", String),
    Column("last_error", String),
    UniqueConstraint("google_sub", "slug", name="uq_signup_eligibility_subject_slug"),
)

turnstile_use = Table(
    "turnstile_use",
    metadata,
    # A Turnstile token is single use: the primary key IS the token hash, so a
    # second attempt to spend it fails on insert instead of passing the gate.
    Column("token_hash", String, primary_key=True),
    Column("identity_id", String, ForeignKey("signup_identity.id"), index=True),
    Column("client_ip", String),
    Column("hostname", String),
    Column("consumed_at", String, nullable=False),
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
    # Calendly (and any provider with short-lived access tokens) stores both halves of the grant.
    # The access token is cached with its expiry so a call does not rotate the refresh chain;
    # providers whose token is long-lived leave these empty.
    Column("encrypted_access_token", Text),
    Column("access_token_expires_at", String),
    # The provider's own id for the installation when the grant is per-installation rather than
    # per-account. Sentry needs it on every exchange and refresh; providers without one leave it
    # NULL, and `external_account` keeps carrying the human-readable label.
    Column("external_id", String),
    # The customer-supplied origin for connectors that address an instance rather than a hosted
    # account (Grafana). It is not a secret, but it is per-account state, so it lives on the row
    # rather than in deployment configuration.
    Column("external_url", String),
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
    # Advertised context window of the provider's model, in tokens. Optional: a
    # workspace admin's own provider may not declare one, and it is not needed to
    # call the model - it lets the UI state the limit and lets a managed offering
    # register the model it seeds (see infra/tenants/default-provider.json).
    Column("context_window", Integer),
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
    # "human" or "bot" (an extension's identity: no login, no membership).
    Column("kind", String, nullable=False, server_default="human"),
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
    # Departments are teams with children. Not an access boundary: visibility is.
    Column("parent_team_id", String, ForeignKey("team.id"), index=True),
    Column("visibility", String, nullable=False, default="public"),
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
    Column("role", String, nullable=False, default="member"),
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
    # Maintained by issue_decision writes: true while one of its decisions is open, so a
    # board or list reads "waiting on me" without a subquery per row (docs/decisions.md).
    Column("decision_needed", Boolean, nullable=False, default=False),
    UniqueConstraint("team_id", "number", name="uq_issue_team_number"),
)


# A question an agent asks while working on an issue, answered by a human (docs/decisions.md).
# The row outlives the asking process: an agent polls it, a board flags the issue it blocks.
issue_decision = Table(
    "issue_decision",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("issue_id", String, ForeignKey("issue.id"), nullable=False, index=True),
    Column("question", Text, nullable=False),
    # JSON [{key,label,description}]; "[]" means a free-form answer is expected.
    Column("options", Text, nullable=False, default="[]"),
    Column("context", Text),
    Column("status", String, nullable=False, default="open", index=True),
    Column("asked_by_id", String, ForeignKey("user.id")),
    Column("asked_by_agent", String),
    Column("answer", Text),
    Column("chosen_option", String),
    Column("answered_by_id", String, ForeignKey("user.id")),
    Column("answered_at", String),
    Column("canceled_at", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
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
    # Set for per-user events (notifications): only this user is told about them.
    Column("recipient_id", String),
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

# Outgoing webhooks (Linear-compatible). A webhook only receives change events
# committed after start_seq. The dispatcher turns change_event rows into frozen
# delivery payloads; the sender posts them with retries.
webhook = Table(
    "webhook",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("url", String, nullable=False),
    Column("label", String),
    Column("enabled", Boolean, nullable=False, default=True),
    # AES-GCM sealed with HOJA_SECRET_KEY: the HMAC signing key must be recoverable.
    Column("secret_sealed", Text, nullable=False),
    Column("resource_types", Text, nullable=False),
    Column("team_id", String, ForeignKey("team.id")),
    Column("all_public_teams", Boolean, nullable=False, default=False),
    Column("creator_id", String, ForeignKey("user.id")),
    Column("start_seq", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("archived_at", String),
    # Set when an extension owns this webhook: managed only through the extension.
    Column("extension_id", String),
    Column("event_key", String),
)

# One per organization: change_event rows up to seq are already dispatched.
webhook_cursor = Table(
    "webhook_cursor",
    metadata,
    Column("organization_id", String, ForeignKey("organization.id"), primary_key=True),
    Column("seq", Integer, nullable=False),
    Column("updated_at", String, nullable=False),
)

# Last dispatched state of each issue: the source of updatedFrom, because
# change_event records that a row changed but not its previous values.
webhook_issue_snapshot = Table(
    "webhook_issue_snapshot",
    metadata,
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("issue_id", String, primary_key=True),
    Column("data", Text, nullable=False),
)

webhook_delivery = Table(
    "webhook_delivery",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("webhook_id", String, ForeignKey("webhook.id", ondelete="CASCADE"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("event", String, nullable=False),
    Column("payload", Text, nullable=False),
    # pending -> sending -> delivered | pending (retry) | failed | canceled
    Column("status", String, nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("next_attempt_at", String, nullable=False),
    Column("lease_until", String),
    Column("last_status", Integer),
    Column("last_error", Text),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Index("ix_webhook_delivery_queue", "webhook_id", "status", "seq"),
)

# Extensions: an install is a set of registrations, each recorded with its
# inverse in extension_effect so uninstall can replay them in reverse order.
extension = Table(
    "extension",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("key", String, nullable=False),
    Column("name", String, nullable=False),
    Column("version", String, nullable=False),
    Column("url", String, nullable=False),
    Column("access", String, nullable=False),
    Column("manifest", Text, nullable=False),
    Column("secret_sealed", Text, nullable=False),
    Column("bot_user_id", String, ForeignKey("user.id")),
    Column("installed_by", String, ForeignKey("user.id")),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    # Tombstone: uninstalled rows stay for the action-run audit trail.
    Column("uninstalled_at", String),
    Index("uq_extension_live_key", "organization_id", "key", unique=True,
          sqlite_where=text("uninstalled_at IS NULL"), postgresql_where=text("uninstalled_at IS NULL")),
)

extension_effect = Table(
    "extension_effect",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("extension_id", String, ForeignKey("extension.id"), nullable=False, index=True),
    Column("kind", String, nullable=False),
    Column("ref", String, nullable=False),
    Column("created_at", String, nullable=False),
)

extension_hook = Table(
    "extension_hook",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("extension_id", String, ForeignKey("extension.id"), nullable=False, index=True),
    Column("key", String, nullable=False),
    Column("on_event", String, nullable=False),
    Column("path", String, nullable=False),
    Column("failure_policy", String, nullable=False),
    Column("position", Integer, nullable=False),
)

extension_action = Table(
    "extension_action",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("extension_id", String, ForeignKey("extension.id"), nullable=False, index=True),
    Column("key", String, nullable=False),
    Column("title", String, nullable=False),
    Column("entity", String, nullable=False),
    Column("path", String, nullable=False),
)

extension_action_run = Table(
    "extension_action_run",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("extension_id", String, ForeignKey("extension.id"), nullable=False, index=True),
    Column("action_key", String, nullable=False),
    Column("entity_id", String, nullable=False),
    Column("actor_id", String, ForeignKey("user.id")),
    Column("status", String, nullable=False),
    Column("http_status", Integer),
    Column("message", Text),
    Column("created_at", String, nullable=False),
)

# Hook calls that failed (timeouts, errors, vetoes are not failures).
extension_failure = Table(
    "extension_failure",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("extension_id", String, ForeignKey("extension.id"), nullable=False, index=True),
    Column("kind", String, nullable=False),
    Column("key", String),
    Column("http_status", Integer),
    Column("message", Text),
    Column("created_at", String, nullable=False),
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
    # Resolved media type of an uploaded blob (NULL for external links), recorded at
    # create time so a client can choose a renderer without probing the URL.
    Column("content_type", String),
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


# A view is a saved query (docs/views.md): scope + filter tree + display options, owned by
# one member and shared through ``visibility``. ``filters`` and ``display`` hold JSON
# documents validated by hoja_server.views before they are written.
saved_view = Table(
    "saved_view",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("kind", String, nullable=False),
    Column("name", String, nullable=False),
    Column("description", Text),
    Column("icon", String),
    Column("color", String),
    Column("slug_id", String, nullable=False),
    Column("owner_id", String, ForeignKey("user.id"), nullable=False, index=True),
    Column("creator_id", String, ForeignKey("user.id")),
    Column("updated_by_id", String, ForeignKey("user.id")),
    Column("scope", String, nullable=False, default="workspace"),
    Column("team_id", String, ForeignKey("team.id"), index=True),
    Column("project_id", String, ForeignKey("project.id"), index=True),
    Column("initiative_id", String, ForeignKey("initiative.id"), index=True),
    Column("visibility", String, nullable=False, default="private"),
    Column("filters", Text, nullable=False, default="{}"),
    Column("display", Text, nullable=False, default="{}"),
    Column("archived_at", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("organization_id", "slug_id", name="uq_saved_view_org_slug"),
)

# Favorites are per user, so two members can favorite the same shared view independently.
saved_view_favorite = Table(
    "saved_view_favorite",
    metadata,
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("view_id", String, ForeignKey("saved_view.id"), nullable=False, index=True),
    Column("user_id", String, ForeignKey("user.id"), nullable=False, index=True),
    Column("created_at", String, nullable=False),
    UniqueConstraint("view_id", "user_id", name="uq_saved_view_favorite"),
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
    d = Path(base) / "hoja"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path_for(tenant: str) -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share"))
    canonical = base / "hoja" / f"{tenant}.db"
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
    if env_url := os.environ.get("HOJA_DATABASE_URL"):
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


def _pool_setting(name: str, default: int, *, minimum: int) -> int:
    """A bounded integer from the environment, refusing nonsense loudly."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}.") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}.")
    return value


def make_engine(target: str | Path) -> Engine:
    url = _as_sqlalchemy_url(target)
    backend = make_url(url).get_backend_name()
    kwargs = {"future": True}
    if backend == "postgresql":
        kwargs["pool_pre_ping"] = True
        # Pool sizing is a deployment decision, not a constant: instances are
        # per tenant and they all point at one server, so N * (size + overflow)
        # must stay under the server's max_connections with room for psql and
        # migrations. The old 40 + 20 let two instances alone exceed 100.
        kwargs["pool_size"] = _pool_setting("HOJA_DB_POOL_SIZE", 5, minimum=1)
        kwargs["max_overflow"] = _pool_setting("HOJA_DB_MAX_OVERFLOW", 5, minimum=0)
        kwargs["pool_timeout"] = _pool_setting("HOJA_DB_POOL_TIMEOUT", 30, minimum=1)
    eng = create_engine(url, **kwargs)
    if backend == "postgresql":
        from hoja_server.tenant_context import install

        install(eng)

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
    # Explicit columns: later revisions add user columns this revision predates.
    profiles = conn.execute(select(
        user.c.id, user.c.organization_id, user.c.email, user.c.active,
        user.c.archived_at, user.c.created_at)).mappings().all()
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
        # FALSE, not 0: Postgres rejects an integer default on a boolean column
        # ("column ... is of type boolean but default expression is of type integer").
        "cadence_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
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


# Link tables publish as an update of their parent issue. The event row is
# selected from the issue itself, so a link removed alongside (or after) its
# issue emits nothing instead of violating change_event's NOT NULL columns.
_LINK_CAPTURE = ("issue_label_link", "issue_subscriber")


def _apply_issue_link_capture(conn):
    dialect = conn.dialect.name
    for table_name in _LINK_CAPTURE:
        if dialect == "sqlite":
            for op in ("insert", "delete"):
                row = "OLD" if op == "delete" else "NEW"
                conn.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS trg_chg_{table_name}_{op} "
                    f"AFTER {op.upper()} ON {table_name} FOR EACH ROW BEGIN "
                    "INSERT INTO change_event (organization_id, entity, entity_id, action, team_id, created_at) "
                    "SELECT organization_id, 'issue', id, 'update', team_id, "
                    f"strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM issue WHERE id = {row}.issue_id; END;"
                )
        elif dialect == "postgresql":
            conn.exec_driver_sql(f"""CREATE OR REPLACE FUNCTION chg_{table_name}() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE v_issue_id TEXT;
BEGIN
  v_issue_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.issue_id ELSE NEW.issue_id END;
  INSERT INTO change_event (organization_id, entity, entity_id, action, team_id, created_at)
  SELECT i.organization_id, 'issue', i.id, 'update', i.team_id,
         to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
  FROM issue i WHERE i.id = v_issue_id;
  RETURN NULL;
END;
$fn$;""")
            conn.exec_driver_sql(f'DROP TRIGGER IF EXISTS trg_chg_{table_name} ON "{table_name}";')
            conn.exec_driver_sql(
                f'CREATE TRIGGER trg_chg_{table_name} AFTER INSERT OR UPDATE OR DELETE ON "{table_name}" '
                f"FOR EACH ROW EXECUTE FUNCTION chg_{table_name}();")
        else:
            raise RuntimeError(f"Realtime capture is not implemented for {dialect}")


CHANGE_NOTIFY_CHANNEL = "hoja_change"
_PG_CHANGE_TS = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')"


def _pg_publish_body(org: str, entity: str, entity_id: str, action: str, team: str,
                     recipient: str = "NULL") -> str:
    """PL/pgSQL that records one change event and notifies listeners, commit-ordered per org.

    It runs from a DEFERRED constraint trigger, i.e. at COMMIT after every
    statement of the transaction, and takes a per-org transaction advisory lock
    before allocating the seq. Concurrent writers of one org therefore get seqs in
    commit order, so a reader holding "everything up to seq N" never misses a
    lower seq that commits later. The lock is held only while committing, after
    all row locks were taken, so it cannot deadlock with them.
    """
    return f"""
  v_org := {org};
  -- Evaluated at commit: the org (or a link's issue) may be gone by then.
  IF v_org IS NULL OR NOT EXISTS (SELECT 1 FROM organization WHERE id = v_org) THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtext('hoja.change:' || v_org));
  INSERT INTO change_event (organization_id, entity, entity_id, action, team_id, created_at, recipient_id)
  VALUES (v_org, {entity}, {entity_id}, {action}, {team}, {_PG_CHANGE_TS}, {recipient})
  RETURNING seq INTO v_seq;
  PERFORM pg_notify('{CHANGE_NOTIFY_CHANNEL}',
                    json_build_array(v_seq, v_org, {entity}, {entity_id}, {action}, {team}, {recipient})::text);"""


# Updates that are not changes clients care about (issue number allocation).
_PG_SKIP = {"team": "  IF TG_OP = 'UPDATE' AND OLD.issue_counter IS DISTINCT FROM NEW.issue_counter "
                    "THEN RETURN NULL; END IF;"}
# Per-user entities: the event carries the recipient and only that user receives it.
_RECIPIENT_CAPTURE = {"inbox_notification": "{row}.recipient_id"}


def _apply_realtime_v2_capture(conn):
    """Recipient-scoped notification events; on PostgreSQL also deferred,
    commit-ordered capture with LISTEN/NOTIFY wakeups."""
    if "recipient_id" not in {c["name"] for c in inspect(conn).get_columns("change_event")}:
        conn.exec_driver_sql("ALTER TABLE change_event ADD COLUMN recipient_id VARCHAR")
    if conn.dialect.name == "sqlite":
        # SQLite serializes writers, so its seqs are already commit-ordered; only
        # notification events change, to carry their recipient.
        for op, label in (("insert", "create"), ("update", "update"), ("delete", "delete")):
            row = "OLD" if op == "delete" else "NEW"
            conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_chg_inbox_notification_{label}")
            conn.exec_driver_sql(
                f"CREATE TRIGGER trg_chg_inbox_notification_{label} AFTER {op.upper()} ON inbox_notification "
                "FOR EACH ROW BEGIN INSERT INTO change_event "
                "(organization_id, entity, entity_id, action, team_id, created_at, recipient_id) "
                f"VALUES ({row}.organization_id, 'notification', CAST({row}.id AS TEXT), '{label}', NULL, "
                f"strftime('%Y-%m-%dT%H:%M:%fZ','now'), {row}.recipient_id); END;")
        # Allocating an issue number bumps team.issue_counter; that is not a team
        # change, and publishing it made every client re-bootstrap on issue create.
        conn.exec_driver_sql("DROP TRIGGER IF EXISTS trg_chg_team_update")
        conn.exec_driver_sql(
            "CREATE TRIGGER trg_chg_team_update AFTER UPDATE ON team FOR EACH ROW "
            "WHEN OLD.issue_counter IS NEW.issue_counter BEGIN INSERT INTO change_event "
            "(organization_id, entity, entity_id, action, team_id, created_at) "
            "VALUES (NEW.organization_id, 'team', CAST(NEW.id AS TEXT), 'update', NEW.id, "
            "strftime('%Y-%m-%dT%H:%M:%fZ','now')); END;")
        return
    if conn.dialect.name != "postgresql":
        raise RuntimeError(f"Realtime capture is not implemented for {conn.dialect.name}")
    for table_name, entity, pk, team_expr, org_expr in _CHANGE_CAPTURE:
        team_new = team_expr.format(row="NEW") if team_expr else "NULL"
        team_old = team_expr.format(row="OLD") if team_expr else "NULL"
        rcpt = _RECIPIENT_CAPTURE.get(table_name)
        rcpt_new = rcpt.format(row="NEW") if rcpt else "NULL"
        rcpt_old = rcpt.format(row="OLD") if rcpt else "NULL"
        conn.exec_driver_sql(f"""CREATE OR REPLACE FUNCTION chg_{table_name}() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE v_org TEXT; v_seq BIGINT;
BEGIN
{_PG_SKIP.get(table_name, "")}
  IF TG_OP = 'DELETE' THEN
{_pg_publish_body(org_expr.format(row="OLD"), f"'{entity}'", f"CAST(OLD.{pk} AS TEXT)", "'delete'",
                  team_old, rcpt_old)}
  ELSE
{_pg_publish_body(org_expr.format(row="NEW"), f"'{entity}'", f"CAST(NEW.{pk} AS TEXT)",
                  "CASE WHEN TG_OP = 'INSERT' THEN 'create' ELSE 'update' END", team_new, rcpt_new)}
  END IF;
  RETURN NULL;
END;
$fn$;""")
        _pg_deferred_trigger(conn, table_name)
    for table_name in _LINK_CAPTURE:
        # A link publishes as an update of its issue, read at commit time; a link
        # removed together with its issue emits nothing (the issue delete does).
        conn.exec_driver_sql(f"""CREATE OR REPLACE FUNCTION chg_{table_name}() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE v_org TEXT; v_seq BIGINT; v_issue_id TEXT; v_team TEXT;
BEGIN
  v_issue_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.issue_id ELSE NEW.issue_id END;
  SELECT i.organization_id, i.team_id INTO v_org, v_team FROM issue i WHERE i.id = v_issue_id;
{_pg_publish_body("v_org", "'issue'", "v_issue_id", "'update'", "v_team")}
  RETURN NULL;
END;
$fn$;""")
        _pg_deferred_trigger(conn, table_name)


def _pg_deferred_trigger(conn, table_name: str) -> None:
    conn.exec_driver_sql(f'DROP TRIGGER IF EXISTS trg_chg_{table_name} ON "{table_name}";')
    conn.exec_driver_sql(
        f'CREATE CONSTRAINT TRIGGER trg_chg_{table_name} AFTER INSERT OR UPDATE OR DELETE ON "{table_name}" '
        f"DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION chg_{table_name}();")


def _apply_change_event_org_seq_index(conn):
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_change_event_org_seq "
        "ON change_event (organization_id, seq)"
    )


def _apply_issue_scale_indexes(conn):
    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_issue_cycle_id ON issue (cycle_id)",
        "CREATE INDEX IF NOT EXISTS ix_issue_parent_id ON issue (parent_id)",
        "CREATE INDEX IF NOT EXISTS ix_issue_creator_id ON issue (creator_id)",
        "CREATE INDEX IF NOT EXISTS ix_issue_org_created ON issue (organization_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_issue_org_updated ON issue (organization_id, updated_at)",
        "CREATE INDEX IF NOT EXISTS ix_issue_archived_partial ON issue (organization_id, archived_at) "
        "WHERE archived_at IS NULL",
        "CREATE INDEX IF NOT EXISTS ix_inbox_notification_recipient_read ON inbox_notification (recipient_id, read_at)",
        "CREATE INDEX IF NOT EXISTS ix_issue_activity_issue_created ON issue_activity (issue_id, created_at)",
    ):
        conn.exec_driver_sql(stmt)


def _apply_api_key_lifecycle(conn):
    existing = {c["name"] for c in inspect(conn).get_columns("api_key")}
    for name, sql_type in (("hint", "VARCHAR"), ("expires_at", "VARCHAR"), ("last_used_at", "VARCHAR")):
        if name not in existing:
            conn.exec_driver_sql(f"ALTER TABLE api_key ADD COLUMN {name} {sql_type}")

def _apply_agent_conversation_title(conn) -> None:
    if "title" not in {c["name"] for c in inspect(conn).get_columns("agent_conversation")}:
        conn.exec_driver_sql("ALTER TABLE agent_conversation ADD COLUMN title VARCHAR")


def _apply_model_provider_context_window(conn) -> None:
    """Record the model's advertised context window on a provider row.

    Nullable: providers added by workspace admins need no window, and existing rows
    keep working unchanged. The included offering (infra/tenants/default-provider.json)
    sets it so the workspace the seed touches has the model described in full.
    """
    if "context_window" not in {c["name"] for c in inspect(conn).get_columns("model_provider")}:
        conn.exec_driver_sql("ALTER TABLE model_provider ADD COLUMN context_window INTEGER")


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


meeting = Table(
    "meeting",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("code", String, nullable=False, unique=True),
    Column("title", String, nullable=False),
    Column("description", Text),
    Column("host_id", String, ForeignKey("user.id"), nullable=False, index=True),
    Column("team_id", String, ForeignKey("team.id")),
    Column("issue_id", String),
    Column("project_id", String),
    Column("access", String, nullable=False, default="invited"),
    Column("passcode_hash", String),
    Column("link_enabled", Boolean, nullable=False, default=False),
    Column("waiting_room", Boolean, nullable=False, default=True),
    Column("join_before_host", Boolean, nullable=False, default=False),
    Column("mute_on_entry", Boolean, nullable=False, default=True),
    Column("allow_unmute", Boolean, nullable=False, default=True),
    Column("screen_share", String, nullable=False, default="all"),
    Column("chat_enabled", Boolean, nullable=False, default=True),
    Column("locked", Boolean, nullable=False, default=False),
    Column("recording", String, nullable=False, default="off"),
    Column("max_participants", Integer, nullable=False, default=25),
    Column("status", String, nullable=False, default="scheduled", index=True),
    Column("scheduled_start", String),
    Column("duration_minutes", Integer),
    Column("started_at", String),
    Column("ended_at", String),
    Column("reminded_at", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

meeting_invitee = Table(
    "meeting_invitee",
    metadata,
    Column("id", String, primary_key=True),
    Column("meeting_id", String, ForeignKey("meeting.id"), nullable=False, index=True),
    Column("user_id", String, ForeignKey("user.id"), index=True),
    Column("email", String, index=True),
    Column("role", String, nullable=False, default="attendee"),
    Column("response", String, nullable=False, default="needsAction", server_default="needsAction"),
    Column("invited_by", String, ForeignKey("user.id")),
    Column("created_at", String, nullable=False),
)

meeting_participant = Table(
    "meeting_participant",
    metadata,
    Column("id", String, primary_key=True),
    Column("meeting_id", String, ForeignKey("meeting.id"), nullable=False, index=True),
    Column("user_id", String, ForeignKey("user.id"), index=True),
    Column("kind", String, nullable=False, default="member"),
    Column("display_name", String, nullable=False),
    Column("role", String, nullable=False, default="attendee"),
    Column("state", String, nullable=False, default="waiting"),
    Column("token_hash", String, unique=True, index=True),
    Column("sfu_session_id", String),
    Column("sfu_sub_session_id", String),
    Column("device_id", String),
    Column("device_of", String),
    Column("device_label", String),
    Column("tracks", Text),
    Column("muted_by_host", Boolean, nullable=False, default=False),
    Column("hand_raised", Boolean, nullable=False, default=False),
    Column("audio_on", Boolean, nullable=False, default=False),
    Column("video_on", Boolean, nullable=False, default=False),
    Column("screen_on", Boolean, nullable=False, default=False),
    Column("bytes_in", BigInteger, nullable=False, default=0),
    Column("bytes_out", BigInteger, nullable=False, default=0),
    Column("joined_at", String),
    Column("left_at", String),
    Column("created_at", String, nullable=False),
)

meeting_ring = Table(
    "meeting_ring",
    metadata,
    Column("id", String, primary_key=True),
    Column("meeting_id", String, ForeignKey("meeting.id"), nullable=False, index=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False),
    Column("from_id", String, ForeignKey("user.id"), nullable=False),
    Column("to_id", String, ForeignKey("user.id"), nullable=False, index=True),
    Column("state", String, nullable=False, default="ringing"),
    Column("created_at", String, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("responded_at", String),
)

meeting_chat = Table(
    "meeting_chat",
    metadata,
    Column("id", String, primary_key=True),
    Column("meeting_id", String, ForeignKey("meeting.id"), nullable=False, index=True),
    Column("sender_id", String, nullable=False),
    Column("recipient_id", String),
    Column("body", Text, nullable=False),
    Column("created_at", String, nullable=False),
)


def _apply_meeting_extras(conn) -> None:
    """RSVPs, ringing, reminders, separate publisher/subscriber SFU sessions, media flags, usage."""
    meeting_ring.create(conn, checkfirst=True)
    inspector = inspect(conn)
    additions = {
        "meeting": {"reminded_at": "VARCHAR"},
        "meeting_invitee": {"response": "VARCHAR NOT NULL DEFAULT 'needsAction'"},
        "meeting_participant": {
            "sfu_sub_session_id": "VARCHAR",
            "audio_on": "BOOLEAN NOT NULL DEFAULT FALSE",
            "video_on": "BOOLEAN NOT NULL DEFAULT FALSE",
            "screen_on": "BOOLEAN NOT NULL DEFAULT FALSE",
            "bytes_in": "BIGINT NOT NULL DEFAULT 0",
            "bytes_out": "BIGINT NOT NULL DEFAULT 0",
        },
    }
    for table_name, columns in additions.items():
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for name, sql_type in columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {name} {sql_type}")


def _apply_meeting_devices(conn) -> None:
    """Per-device seats: a person may hold a main seat plus companion seats (phone as mic, etc.)."""
    existing = {column["name"] for column in inspect(conn).get_columns("meeting_participant")}
    for name in ("device_id", "device_of", "device_label"):
        if name not in existing:
            conn.exec_driver_sql(f"ALTER TABLE meeting_participant ADD COLUMN {name} VARCHAR")


def _apply_signup_provisioning(conn) -> None:
    """Add the worker lease fields to an existing sign-up outbox."""
    existing = {column["name"] for column in inspect(conn).get_columns("signup_eligibility")}
    additions = {
        "attempts": "INTEGER NOT NULL DEFAULT 0",
        "provisioned_at": "VARCHAR",
        "last_error": "VARCHAR",
    }
    for name, sql_type in additions.items():
        if name not in existing:
            conn.exec_driver_sql(
                f"ALTER TABLE signup_eligibility ADD COLUMN {name} {sql_type}"
            )


def _apply_team_hierarchy(conn) -> None:
    """Departments (team.parent_team_id), team.visibility and team_member.role.

    ``private`` becomes ``visibility``: the two-state boolean could not express the
    'restricted' middle ground (parent-team members may see and join). The old column
    is left in place because SQLite cannot drop columns in place and nothing reads it
    once the data has been copied; a fresh database never gets it.
    """
    columns = {column["name"] for column in inspect(conn).get_columns("team")}
    if "parent_team_id" not in columns:
        conn.exec_driver_sql("ALTER TABLE team ADD COLUMN parent_team_id VARCHAR")
    # DDL must come before the data copy below: Postgres refuses to run DDL on a table
    # that has pending trigger events, and the copy fires the change_event triggers.
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_team_parent_team_id ON team (parent_team_id)")
    copy_private = False
    if "visibility" not in columns:
        conn.exec_driver_sql(
            "ALTER TABLE team ADD COLUMN visibility VARCHAR NOT NULL DEFAULT 'public'")
        copy_private = "private" in columns
    member_columns = {column["name"] for column in inspect(conn).get_columns("team_member")}
    if "role" not in member_columns:
        conn.exec_driver_sql(
            "ALTER TABLE team_member ADD COLUMN role VARCHAR NOT NULL DEFAULT 'member'")
    if copy_private:
        # Last statement on purpose. Postgres types `private` BOOLEAN so an integer
        # literal is a type error there; SQLite stores 0/1 and accepts either. This
        # branch only runs when upgrading a database created before `visibility`
        # existed, which is why a fresh-Postgres test never exercised it.
        truthy = "TRUE" if conn.dialect.name == "postgresql" else "1"
        conn.exec_driver_sql(
            f"UPDATE team SET visibility = 'private' WHERE private = {truthy}")



def _apply_extensions(conn):
    def columns(table_name):
        return {c["name"] for c in inspect(conn).get_columns(table_name)}

    if "kind" not in columns("user"):
        conn.exec_driver_sql("ALTER TABLE \"user\" ADD COLUMN kind VARCHAR NOT NULL DEFAULT 'human'")
    for name in ("extension_id", "event_key"):
        if name not in columns("webhook"):
            conn.exec_driver_sql(f"ALTER TABLE webhook ADD COLUMN {name} VARCHAR")
    for table in (extension, extension_effect, extension_hook, extension_action,
                  extension_action_run, extension_failure):
        table.create(conn, checkfirst=True)


def _apply_issue_decisions(conn):
    """Decisions (docs/decisions.md): the row, the issue's maintained flag, and its capture.

    A decision publishes as its own ``issueDecision`` event and, through the issue row's
    ``decision_needed`` update, as an ``issue`` update: a client watching either learns that
    the round trip started or finished.
    """
    if "decision_needed" not in {c["name"] for c in inspect(conn).get_columns("issue")}:
        conn.exec_driver_sql(
            'ALTER TABLE issue ADD COLUMN decision_needed BOOLEAN NOT NULL DEFAULT FALSE')
    issue_decision.create(conn, checkfirst=True)
    # Team scope is read from the issue so the event honours team visibility; the org comes
    # from the decision row itself and is what change_event requires.
    team_expr = "(SELECT team_id FROM issue WHERE id = {row}.issue_id)"
    if conn.dialect.name == "sqlite":
        for statement in _change_trigger_sql(
                "sqlite", "issue_decision", "issueDecision", "id", team_expr,
                "{row}.organization_id"):
            conn.exec_driver_sql(statement)
    elif conn.dialect.name == "postgresql":
        conn.exec_driver_sql(f"""CREATE OR REPLACE FUNCTION chg_issue_decision() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE v_org TEXT; v_seq BIGINT; v_team TEXT; v_issue_id TEXT;
BEGIN
  v_issue_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.issue_id ELSE NEW.issue_id END;
  SELECT i.team_id INTO v_team FROM issue i WHERE i.id = v_issue_id;
  IF TG_OP = 'DELETE' THEN
{_pg_publish_body("OLD.organization_id", "'issueDecision'", "CAST(OLD.id AS TEXT)", "'delete'", "v_team")}
  ELSE
{_pg_publish_body("NEW.organization_id", "'issueDecision'", "CAST(NEW.id AS TEXT)",
                  "CASE WHEN TG_OP = 'INSERT' THEN 'create' ELSE 'update' END", "v_team")}
  END IF;
  RETURN NULL;
END;
$fn$;""")
        _pg_deferred_trigger(conn, "issue_decision")
    else:
        raise RuntimeError(f"Realtime capture is not implemented for {conn.dialect.name}")


def _apply_attachment_content_type(conn) -> None:
    """Record the media type of an uploaded blob on its attachment row.

    Existing rows stay NULL: an external link has no type we could know without
    fetching it, and the client falls back to the URL extension for those.
    """
    columns = {c["name"] for c in inspect(conn).get_columns("attachment")}
    if "content_type" not in columns:
        conn.exec_driver_sql("ALTER TABLE attachment ADD COLUMN content_type VARCHAR")


def _apply_connector_access_token(conn) -> None:
    """Store the access half of a grant whose token expires.

    Google refreshes on every call from the refresh token alone. Calendly rotates its refresh
    token, so refreshing per call would burn the chain; the access token and its expiry are kept
    so a call can reuse a still-valid token. Existing rows keep NULL in both columns.
    """
    existing = {column["name"] for column in inspect(conn).get_columns("connector_account")}
    for name in ("encrypted_access_token", "access_token_expires_at"):
        if name not in existing:
            conn.exec_driver_sql(f"ALTER TABLE connector_account ADD COLUMN {name} VARCHAR")


def _apply_connector_external_url(conn) -> None:
    """Store the customer-supplied instance origin for instance-addressed connectors.

    Grafana's token is only meaningful together with its instance URL, and the URL is per
    connection. Existing rows keep NULL.
    """
    existing = {column["name"] for column in inspect(conn).get_columns("connector_account")}
    if "external_url" not in existing:
        conn.exec_driver_sql("ALTER TABLE connector_account ADD COLUMN external_url VARCHAR")


def _apply_connector_external_id(conn) -> None:
    """Store the provider-side installation id for per-installation grants.

    Sentry's token exchange and refresh both address
    `/api/0/sentry-app-installations/{id}/authorizations/`, so the id has to survive a restart.
    Keeping it in `external_account` would replace the organization label the UI shows. Existing
    rows (Google, Notion, Slack, Calendly) keep NULL.
    """
    existing = {column["name"] for column in inspect(conn).get_columns("connector_account")}
    if "external_id" not in existing:
        conn.exec_driver_sql("ALTER TABLE connector_account ADD COLUMN external_id VARCHAR")


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
    29: _apply_issue_scale_indexes,
    30: _apply_issue_link_capture,
    31: _apply_realtime_v2_capture,
    32: lambda conn: (
        meeting.create(conn, checkfirst=True),
        meeting_invitee.create(conn, checkfirst=True),
        meeting_participant.create(conn, checkfirst=True),
        meeting_chat.create(conn, checkfirst=True),
    ),
    33: _apply_meeting_extras,
    34: lambda conn: _apply_meeting_devices(conn),
    35: _apply_team_hierarchy,
    36: lambda conn: (
        signup_identity.create(conn, checkfirst=True),
        signup_flow.create(conn, checkfirst=True),
        signup_session.create(conn, checkfirst=True),
        signup_invite.create(conn, checkfirst=True),
        signup_eligibility.create(conn, checkfirst=True),
        turnstile_use.create(conn, checkfirst=True),
    ),
    37: _apply_signup_provisioning,
    38: lambda conn: mail_outbox.create(conn, checkfirst=True),
    # Cloud numbering: OSS revisions 38-40 (webhooks, extensions, live-key index)
    # land here as 39-41 because Cloud's 38 is the mail outbox.
    39: lambda conn: (
        webhook.create(conn, checkfirst=True),
        webhook_cursor.create(conn, checkfirst=True),
        webhook_issue_snapshot.create(conn, checkfirst=True),
        webhook_delivery.create(conn, checkfirst=True),
    ),
    40: _apply_extensions,
    41: lambda conn: next(i for i in extension.indexes if i.name == "uq_extension_live_key").create(
        conn, checkfirst=True),
    42: lambda conn: tenant_membership_hint.create(conn, checkfirst=True),
    43: lambda conn: (
        saved_view.create(conn, checkfirst=True),
        saved_view_favorite.create(conn, checkfirst=True),
    ),
    44: _apply_attachment_content_type,
    # Renumbered 43 -> 44 -> 45 across rebases: main shipped revision 43 (saved views)
    # and 44 (attachment content_type), so the context window takes the next number
    # after the merge queue agreed #53 lands second.
    45: _apply_model_provider_context_window,
    # Renumbered 44 -> 46 on merge: main already shipped revisions 44 (attachment
    # content_type) and 45 (model_provider.context_window), so decisions takes 46.
    46: _apply_issue_decisions,
    # 47: a provider whose access token expires (Calendly) stores both halves of the grant.
    47: _apply_connector_access_token,
    48: _apply_connector_external_id,
    # 49: an instance-addressed connector (Grafana) stores its origin on the account row.
    49: _apply_connector_external_url,
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
    return os.environ.get("HOJA_APP_URL", "http://localhost:8787").rstrip("/")


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
                    visibility="public",
                    timezone="UTC",
                    issue_counter=0,
                    created_at=ts,
                    updated_at=ts,
                )
            )
            conn.execute(team_member.insert().values(team_id=tid, user_id=uid, role="owner"))
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
        ("Wire Hoja base_url", "Point the CLI at the local server", 1),
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
            raise ValueError("No matching organization found; run 'hoja-serve seed' first.")
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
