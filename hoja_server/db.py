"""SQLAlchemy Core storage, engine configuration, migration, and seeding."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

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
    and_,
    create_engine,
    event,
    func,
    inspect,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
CURRENT_SCHEMA_REVISION = 83

log = logging.getLogger(__name__)


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
    Column("vanity_host", String, unique=True),
    Column("created_at", String),
    Column("updated_at", String),
    # Revision 76: whether this workspace shows the Agents screens. NULL (every workspace
    # before 76) means off; an admin turns it on in Settings -> Workspace.
    Column("agents_enabled", Boolean),
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
    # Mandatory: a key with no organization authenticates nobody (resolve_token joins
    # `user` on organization equality) and is invisible to every bound tenant, so a NULL
    # here is only ever an inert row that a later feature would mistake for an org-wide key.
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False),
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
    # HOJA-490: last request this session was seen on, for the workspace's idle
    # timeout. NULL on a pre-59 row reads as created_at (see store.session_idle_ok).
    Column("last_seen_at", String),
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

# OAuth replay protection for connector flows, scoped to an organization.
#
# The platform table `oauth_state_nonce` is deliberately global (an MCP OAuth
# code is consumed before a tenant is known). A connector flow is different: it
# always starts inside one workspace, so its nonce belongs to that workspace and
# stays under row-level security. That makes "org A consumed org B's state" a
# database-level impossibility rather than an application promise, and it keeps
# connector data out of the global allowlist.
connector_oauth_state = Table(
    "connector_oauth_state",
    metadata,
    Column("nonce_hash", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), primary_key=True, index=True),
    Column("expires_at", Float, nullable=False, index=True),
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

# One GitHub object linked to one issue. Detection happens outside the database
# (hoja_server.github_linking); this table records the result. The unique key is
# the idempotency guarantee: a re-delivered webhook upserts the same row instead
# of duplicating the link. organization_id is what puts this table under the
# shared-tenancy RLS policy.
github_link = Table(
    "github_link",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("issue_id", String, ForeignKey("issue.id"), nullable=False, index=True),
    Column("kind", String, nullable=False),
    Column("external_id", String, nullable=False),
    Column("url", String, nullable=False),
    Column("title", String, nullable=False),
    Column("state", String),
    Column("author", String),
    Column("sha", String),
    Column("environment", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String),
    # Pull-request detail the Reviews pane renders (revision 54). Nullable and only
    # filled by scripts/enrich-github-link-pulls.sh: the webhook body carries none of
    # this, so a link stays readable with these empty until the enrichment runs.
    # github_updated_at is GitHub's own timestamp; the varchar updated_at above stays
    # what it always was — a row-touch stamp written by this server.
    Column("additions", Integer),
    Column("deletions", Integer),
    Column("base_ref", String),
    Column("head_ref", String),
    Column("behind", Integer),
    Column("ahead", Integer),
    Column("mergeable", Boolean),
    Column("merge_state", String),
    Column("review_decision", String),
    Column("reviewers", Text),
    Column("checks_state", String),
    Column("checks_total", Integer),
    Column("checks_passing", Integer),
    Column("checks_failing", Integer),
    Column("body", Text),
    Column("merged_at", String),
    Column("github_updated_at", String),
    # Per-file diff of the pull request (revision 55), for the Reviews pane's file
    # list. JSON text rather than a child table on purpose: the pane reads up to 100
    # issues in one query and each issue can carry several links, so a child table
    # would either fan the join out or cost one query per link. A pull request's file
    # list is small and bounded (the enrichment caps it), and nothing ever filters or
    # sorts by file, so it is opaque display data that belongs on the row it describes
    # — the same treatment reviewers already gets. NULL means "not fetched yet"; the
    # serializer serves that as an empty list, never as a missing field.
    Column("files", Text),
    UniqueConstraint(
        "organization_id", "kind", "external_id", name="uq_github_link_external"
    ),
)

# Delivery-id ledger for the GitHub webhook. Global, not tenant data: the id is GitHub's and
# the row only proves we already acted on it. Listed in tests/rls_mode.py GLOBALS.
github_delivery = Table(
    "github_delivery",
    metadata,
    Column("delivery_id", String, primary_key=True),
    Column("event", String, nullable=False),
    Column("received_at", String, nullable=False),
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

# An agent's principal (HOJA-602-A-3, finding F4). The identity is the user row:
# kind='agent', so every existing actor_id/assignee_id/delegate_id points at one
# user table. This row carries only what an agent needs beyond an identity:
# runtime capacity and the budget/working-hours policy it runs under. It is not a
# seat: seat counting reads organization_membership, which an agent never gets.
agent_member = Table(
    "agent_member",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    # One agent per user row, and only that row may be delegated work.
    Column("user_id", String, ForeignKey("user.id"), nullable=False, unique=True),
    # How many turns/delegations may run concurrently for this agent.
    Column("capacity", Integer, nullable=False, default=1),
    # Credits per calendar month; NULL means the workspace's default, not unlimited.
    Column("monthly_budget_credits", Integer),
    # JSON document: [{start, end, days}] in the member's timezone. Opaque to the
    # server; the runner that honors it lands with WP06/WP07.
    Column("working_hours", Text),
    Column("timezone", String),
    # When true, a delegation waits in the queue until a human answers the
    # approval decision it opens, instead of running on the agent's own judgement.
    Column("require_approval", Boolean, nullable=False, default=False),
    Column("model", String),
    # Which runner pool may execute this agent's work, e.g. "default".
    Column("runner_pool", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("archived_at", String),
)

# One run of an agent against one task (HOJA-602-A-7). Linear separates three
# ideas that are easy to conflate: the delegate (who the agent works for, on the
# issue), the session (one run, with a status), and the activities (the visible
# transcript). ``state`` is our ten-state assignment machine;
# ``status`` is Linear's seven-value AgentSessionStatus, derived from it, stored
# so a client written against Linear reads the same row.
agent_session = Table(
    "agent_session",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("issue_id", String, ForeignKey("issue.id"), nullable=False, index=True),
    # The agent's user row; agent_member is the schedule it runs under.
    Column("agent_user_id", String, ForeignKey("user.id"), nullable=False, index=True),
    # Who delegated: the human the agent acts on behalf of.
    Column("creator_id", String, ForeignKey("user.id")),
    # pending|active|awaitingInput|complete|error|stale|stopping — Linear's names.
    Column("status", String, nullable=False),
    # delegated|queued|leased|running|awaiting_input|verifying|in_review|done|failed|cancelled
    Column("state", String, nullable=False),
    # JSON array of reason codes, in the fixed order the schedule check reports
    # them; NULL once the session is admitted (nothing left to wait for).
    Column("queue_reason", Text),
    # delegation|prompt|approval — what opened this session.
    Column("trigger", String, nullable=False, default="delegation"),
    # Set when the schedule check found every fact true; the lease service (WP07)
    # consumes admitted sessions. NULL means queued.
    Column("admitted_at", String),
    # The WP07 lease this session is waiting on. The lease is WP07's row; this is
    # only the join, so the session and the runner's queue never disagree.
    Column("lease_id", String, ForeignKey("agent_lease.id")),
    Column("acked_at", String),
    Column("ended_at", String),
    Column("dismissed_at", String),
    Column("dismissed_by", String, ForeignKey("user.id")),
    # The decision a member with require_approval waits on; NULL when the agent
    # may run on its own judgement.
    Column("approval_decision_id", String, ForeignKey("issue_decision.id")),
    Column("error_message", Text),
    # The claim a runner submits at finish, and the verdict a separate principal
    # records on it (revision 68). ``claim`` is written once: the finish route
    # refuses a second claim rather than editing the first, so the evidence a
    # verifier reads is the evidence the runner submitted.
    Column("claim", Text),
    Column("claim_at", String),
    # Which model produced the work and which judged it. Independence is decided by
    # comparing these two strings and the two principal ids, so both are stored
    # rather than inferred from the agent member's current configuration.
    Column("worker_model", String),
    Column("verifier_model", String),
    # met | not_met | uncertain. NULL until somebody signs; never set by the runner.
    Column("verdict", String),
    Column("verdict_at", String),
    Column("verifier_id", String, ForeignKey("user.id")),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    # Capacity, budget and the queue all read "live sessions for this agent".
    Index("ix_agent_session_live", "organization_id", "agent_user_id", "state"),
    Index("ix_agent_session_issue", "organization_id", "issue_id", "created_at"),
)

# One verdict row per check a verifier signs (revision 68, HOJA-602-A-13 / WP10).
# Append-only for the same reason the transcript is: a verdict that can be edited
# is not evidence. The session carries the latest verdict as a column so a reader
# needs no second query; the ledger is what proves who said what, when, with which
# model, and on which re-read evidence.
agent_verification = Table(
    "agent_verification",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("session_id", String, ForeignKey("agent_session.id"), nullable=False, index=True),
    Column("issue_id", String, ForeignKey("issue.id"), nullable=False, index=True),
    # met | not_met | uncertain — what was recorded.
    Column("verdict", String, nullable=False),
    # What the verifier asked for. It differs from ``verdict`` when a verifier
    # claimed ``met`` on missing evidence: that records as uncertain instead, and
    # the difference is kept rather than hidden.
    Column("requested_verdict", String, nullable=False),
    Column("verifier_id", String, ForeignKey("user.id"), nullable=False),
    # human | agent | bot — a bot may not verify its own agent's work.
    Column("verifier_kind", String, nullable=False),
    Column("verifier_model", String),
    Column("worker_principal_id", String, ForeignKey("user.id"), nullable=False),
    Column("worker_model", String),
    # JSON: one entry per check (name, outcome, detail), which is the evidence the
    # verifier re-read — never a CI conclusion, which this project does not have.
    Column("checks", Text, nullable=False),
    Column("rationale", Text),
    Column("created_at", String, nullable=False),
    Index("ix_agent_verification_session", "organization_id", "session_id", "created_at"),
)

# What one agent may touch (revision 69, HOJA-602-A-14 / WP12). One row per agent
# member, and its absence is a refusal rather than an inheritance of the human's
# whole surface (finding F10): every grant is a column here, and an empty column
# with no row at all are the same answer.
#
# Every list column is JSON because a grant set is read whole and replaced whole;
# normalizing eight little join tables would buy nothing and cost a join at the
# one place that must stay fast — the MCP dispatcher. Secret VALUES are not here:
# this row names them, ``agent_secret`` holds them sealed, and a name that no
# longer resolves to a sealed value is a refusal at injection time.
agent_policy = Table(
    "agent_policy",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("agent_member_id", String, ForeignKey("agent_member.id"), nullable=False, index=True),
    # Monotonic per member. A lease records the version it was granted under, so
    # a policy edited mid-run is detectable instead of silently in force (T053).
    Column("version", Integer, nullable=False, default=1),
    # MCP tool names from mcp_tools.READ_TOOLS / WRITE_TOOLS. Anything absent is
    # denied; a name the registry does not know is refused at write time.
    Column("read_tools", Text),
    Column("write_tools", Text),
    # Tools that additionally need a human's approval (a floor: irreversible
    # operations are always in it, whatever this column says).
    Column("approval_tools", Text),
    # Scope: team and project ids. Empty means "no team, no project".
    Column("teams", Text),
    Column("projects", Text),
    # False only when an operator explicitly grants the whole workspace; true is
    # the conservative default, so a task the agent was not delegated is refused.
    Column("tasks_delegated_only", Boolean, nullable=False, default=True),
    # JSON [{repo, branches}]. The repo's default branch is never grantable.
    Column("repos", Text),
    # Connector account ids the runner may mint a short-lived token for.
    Column("connectors", Text),
    # Secret NAMES injected into the runner environment per lease.
    Column("secrets", Text),
    # Network egress allowlist on the runner: host names, addresses or CIDRs.
    Column("egress", Text),
    # JSON [{host, port}]: the Hoja Connect endpoints that may be exposed.
    Column("connect", Text),
    # Compute and cost.
    Column("max_concurrency", Integer, nullable=False, default=1),
    # Run-minutes per calendar month; NULL means "the member's, unmetered".
    Column("run_minute_limit", Integer),
    # Credits per calendar month; NULL falls back to agent_member's value.
    Column("monthly_credit_budget", Integer),
    # queue | stop — what happens when a limit is reached.
    Column("budget_action", String, nullable=False, default="queue"),
    # People: who may delegate to this agent, and who may approve its work.
    Column("delegators", Text),
    Column("approvers", Text),
    # The harness profile this agent's runs execute under (HOJA-602-A-22, revision
    # 72). NULL means "nobody chose": the queue falls back to ``pi``, which is what
    # every delegation did before this column existed. The name is one of the
    # profiles the server knows (``agent_policy.KNOWN_HARNESSES``); a name it does
    # not know is refused at write time and read as absent, because a lease for a
    # harness no runner can advertise is a run that never starts.
    Column("default_harness", String),
    # JSON list of the harnesses this agent may run on. NULL or empty means "no
    # allowlist", i.e. any known profile - the same shape every other list column
    # here uses, except that an empty list is a wildcard here rather than "nothing":
    # the allowlist restricts the choice, it does not make one, and treating "not
    # configured" as "no harness at all" would stop every delegation on an existing
    # workspace.
    Column("harness_allowlist", Text),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("updated_by", String, ForeignKey("user.id")),
    UniqueConstraint("organization_id", "agent_member_id", name="uq_agent_policy_member"),
)

# A named secret an agent policy may grant (revision 69). The value is sealed
# with the organization's key (``secrets_box``), so a row copied into another
# workspace cannot be opened there. The policy row names the secret; only the
# lease payload carries the value, and only for the length of the lease.
agent_secret = Table(
    "agent_secret",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("secret_sealed", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("updated_by", String, ForeignKey("user.id")),
    UniqueConstraint("organization_id", "name", name="uq_agent_secret_name"),
)

# What a run cost (revision 70, HOJA-602-A-12 / WP14). Append-only: there is no
# UPDATE and no DELETE path anywhere, because a ledger a later write can rewrite is
# not a ledger. A correction is a new row that says so in ``source``, and every read
# sums rows rather than trusting a mutable total.
#
# One row is a *delta* for one run, not a running total: a runner reports cumulative
# counters (events as it goes, then a finish), and the writer stores the difference
# from what the ledger already holds for that run. Summing the rows of a run
# therefore returns the runner's own last cumulative report, which is what makes the
# reconciliation test in ``tests/agent/test_usage_ledger.py`` meaningful rather than
# self-referential.
#
# Money is a decimal STRING, never a float: ``NUMERIC`` in PostgreSQL and a
# canonical decimal in SQLite, parsed with ``Decimal`` at both ends. The token and
# credit columns are integers because they are counts.
usage_event = Table(
    "usage_event",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    # The agent's user row. Kept even when the member is later archived, so a month's
    # spend never disappears from the total because somebody left.
    Column("agent_user_id", String, ForeignKey("user.id"), nullable=False, index=True),
    Column("session_id", String, ForeignKey("agent_session.id"), index=True),
    Column("lease_id", String, ForeignKey("agent_lease.id"), index=True),
    # The WP07 run identity: it is what the delta is computed against, so it is a
    # column rather than something inferred from the lease.
    Column("run_id", String, index=True),
    Column("model", String, nullable=False, default="unknown"),
    Column("input_tokens", BigInteger, nullable=False, default=0),
    Column("output_tokens", BigInteger, nullable=False, default=0),
    # Fresh input + cache read + output is the "processed tokens" the credit is
    # defined against; cache reads are counted, and priced differently.
    Column("cache_read_tokens", BigInteger, nullable=False, default=0),
    # 1 credit = 1,000 processed tokens (the pricing decision's unit). Not money.
    Column("credits", BigInteger, nullable=False, default=0),
    # Wall minutes the run held compute, already floored for managed compute.
    Column("run_minutes", Integer, nullable=False, default=0),
    Column("cost_usd", String, nullable=False, default="0"),
    Column("compute_cost_usd", String, nullable=False, default="0"),
    # finish | events | correction — where the number came from. A correction is a
    # new row, never an edit of the row it corrects.
    Column("source", String, nullable=False, default="finish"),
    # byo-key | managed-key. A bring-your-own key records its credits and costs nothing.
    Column("auth_mode", String, nullable=False, default="byo-key"),
    # customer | managed — whose machine paid for the minutes.
    Column("compute", String, nullable=False, default="customer"),
    # The version of the price card the money was computed with, so a later card
    # reprice cannot silently rewrite what an old row was charged.
    Column("price_card", String, nullable=False),
    # The run's cumulative counter this row leaves the ledger at, so a reader can
    # tell a delta row from a total and an out-of-order report is detectable:
    # one cumulative counter across the three token columns.
    Column("cumulative_tokens", BigInteger, nullable=False, default=0),
    Column("recorded_at", String, nullable=False),
    Index("ix_usage_event_month", "organization_id", "recorded_at"),
    Index("ix_usage_event_agent_month", "organization_id", "agent_user_id", "recorded_at"),
    Index("ix_usage_event_run", "organization_id", "run_id", "recorded_at"),
)

# One published port of one Hoja Connect exposure (revision 71, HOJA-602-A-10 / WP09).
#
# WP09 shipped the runner half in PR #215: `runner/hoja_runnerd/connect.py` opens one `tailcat
# serve` per exposure and keeps the address in memory only. Nothing recorded that an exposure
# existed, so the Connect screen (WP13) drew a fixture and an org admin had no way to see what
# their workspace had exposed. This table is that record, and it holds no secret: the tailcat
# address is a bearer secret and never reaches the database (WP09's grep test), the only identity
# stored is the *pin* (``nodekey:<64 hex>``), which is public key material and is the whole ACL.
#
# A row is one *port*, not one process: an exposure publishes an explicit port allowlist, and a
# per-port row is what lets the screen show "port 3000, port 9229" from the database without a JSON
# column nothing can index. ``exposure_id`` is the runner's own identifier for the process, so the
# rows of one exposure are grouped by it and closed together.
#
# ``state`` is ``open`` or ``closed`` and ``close_reason`` says why, because "it is not in the list"
# is not an audit trail: a lease that ended, a TTL that elapsed and an admin who revoked are three
# different sentences about the same port.
connect_endpoint = Table(
    "connect_endpoint",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    # The runner's identifier for the serve process. Opaque to this server beyond grouping.
    Column("exposure_id", String, nullable=False),
    Column("runner_id", String, ForeignKey("runner.id"), nullable=False, index=True),
    # The lease or the session the exposure belongs to. The lease is the authority (the fence
    # decides whether this is still the runner's work); the session is what the run screen groups
    # by, and is null for a task nobody delegated — the same rule ``usage_event`` follows.
    Column("lease_id", String, ForeignKey("agent_lease.id"), index=True),
    Column("session_id", String, ForeignKey("agent_session.id"), index=True),
    Column("agent_user_id", String, ForeignKey("user.id"), index=True),
    Column("port", Integer, nullable=False),
    # The host or label the exposure is published under. Nullable: a runner that only reports the
    # port is not asked to invent a hostname.
    Column("host", String),
    # `nodekey:<64 hex>` — the pinned worker identity (``--allow=``). Public key material, and the
    # reason a tunnel is reachable by exactly one peer.
    Column("pinned_peer", String, nullable=False),
    # open | closed
    Column("state", String, nullable=False, default="open"),
    # lease_finished | lease_canceled | lease_expired | runner_close | revoked
    Column("close_reason", String),
    Column("opened_at", String, nullable=False),
    # The TTL the serve process was given: the lease's TTL at open time, capped at 1h by the
    # supervisor. Stored rather than re-derived, because it is the deadline this row was opened
    # with.
    Column("expires_at", String),
    Column("closed_at", String),
    # Set when an admin asked for the close: the runner learns it on its next heartbeat and kills
    # the process. The row is already closed here, so the screen never shows a tunnel the control
    # plane has already withdrawn.
    Column("revoke_requested_at", String),
    # The policy version the exposure was granted under, so a later edit is detectable.
    Column("policy_version", Integer),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("organization_id", "exposure_id", "port",
                     name="uq_connect_endpoint_exposure_port"),
    Index("ix_connect_endpoint_open", "organization_id", "state", "opened_at"),
)

# The transcript. Append-only: an activity is never rewritten, because a
# transcript that can be edited is not evidence.
agent_activity = Table(
    "agent_activity",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("session_id", String, ForeignKey("agent_session.id"), nullable=False, index=True),
    # thought|action|elicitation|response|error, plus prompt, which only a human
    # may author (Linear: "An agent cannot generate a prompt type activity").
    Column("kind", String, nullable=False),
    Column("author_kind", String, nullable=False, default="agent"),
    Column("author_id", String, ForeignKey("user.id")),
    Column("body", Text),
    # JSON: the structured payload (queue reasons, elapsed seconds, signal).
    Column("payload", Text),
    # continue|select|stop|auth — Linear's signal vocabulary.
    Column("signal", String),
    # An ephemeral thought keeps a waiting session observably alive without
    # pretending work happened; it is not part of the durable conversation.
    Column("ephemeral", Boolean, nullable=False, default=False),
    Column("created_at", String, nullable=False),
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
    # Children allocate from a disjoint range (CHILD_NUMBER_BASE + this), so a
    # sub-issue never burns a top-level key number. The increment shares the
    # team-row update that serialises concurrent issue creates.
    Column("child_counter", Integer, default=0),
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
    # Recursive identifiers (HOJA-472): the slot this node contributes to its
    # children's prefix, and its number of parent hops. Roots have slot "A",
    # depth 0. See hoja_server/identifiers.py for the grammar.
    Column("slot", String),
    Column("depth", Integer),
    Column("title", String, nullable=False),
    Column("description", Text),
    Column("priority", Integer, default=0),
    Column("priority_label", String),
    Column("estimate", Float),
    Column("state_id", String, ForeignKey("workflow_state.id"), index=True),
    Column("assignee_id", String, ForeignKey("user.id"), index=True),
    # The agent this task is delegated to (Linear's Issue.delegate), distinct from
    # the assignee: the assignee is the person accountable, the delegate is the
    # agent that may work it. Only a user of kind 'agent' in the same organization,
    # holding an agent_member row, is accepted (revision 64).
    Column("delegate_id", String, ForeignKey("user.id", name="fk_issue_delegate_id"), index=True),
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


# Every retired flat identifier resolves to its recursive replacement, so old
# URLs, commit messages and GitHub links keep working. Rows are written only by
# scripts/migrate_recursive_identifiers.py and are never used to rewrite text a
# person wrote: reads fall back through this table, writes never touch it.
issue_identifier_alias = Table(
    "issue_identifier_alias",
    metadata,
    Column("organization_id", String, ForeignKey("organization.id"), primary_key=True),
    Column("old_identifier", String, primary_key=True),
    Column("issue_id", String, ForeignKey("issue.id"), nullable=False, index=True),
    # Enough to run the migration backwards: the number the row held before it
    # was moved to the child range, and the identifier it was given.
    Column("old_number", Integer),
    Column("new_identifier", String, nullable=False),
    Column("created_at", String, nullable=False),
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
    # Pulse: a comment may hang off a project update instead of an issue. Both
    # parents are nullable and the writer sets exactly one; see revision 75.
    Column("project_update_id", String, ForeignKey("project_update.id"), index=True),
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
    # Who caused the change (revision 64): 'human', 'bot' or 'agent'. Capture runs inside
    # a database trigger, which cannot see a Python caller, so these are read from
    # the transaction's actor context (hoja.actor_kind / hoja.actor_id /
    # hoja.on_behalf_of), set by hoja_server.tenant_context.actor(). NULL on
    # SQLite, which has no session settings and no shared-tenancy attribution.
    Column("actor_kind", String),
    Column("actor_id", String),
    # For an agent turn: the human whose request the agent is acting on.
    Column("on_behalf_of", String),
)

# Append-only audit trail (revision 65, HOJA-602-A-8 / WP05). Deliberately
# separate from change_event: change_event is the realtime "what changed" feed,
# this is the repudiation ledger of "who asked for what", and it must survive a
# row rewrite. ``prev_hash``/``row_hash`` chain each row to its predecessor in
# the same organization (see hoja_server.audit); the trigger created by
# _apply_audit_log refuses every UPDATE and DELETE.
audit_event = Table(
    "audit_event",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False),
    Column("action", String, nullable=False),
    Column("actor_kind", String, nullable=False),
    Column("actor_id", String, nullable=False),
    # For an agent turn: the human whose request the agent is acting on.
    Column("on_behalf_of", String),
    Column("target_kind", String),
    Column("target_id", String),
    Column("ip", String),
    Column("runner", String),
    Column("request_id", String),
    Column("detail", Text),
    Column("prev_hash", String(64), nullable=False),
    Column("row_hash", String(64), nullable=False),
    Column("created_at", String, nullable=False),
    Index("ix_audit_event_organization_seq", "organization_id", "seq"),
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

# ---------------------------------------------------------------------------
# Runner lease (HOJA-602-A-6, WP07). A runner is a machine the workspace has
# enrolled; a lease is one grant of one task to one runner under a monotonic
# fence; an effect is one external write that must happen exactly once.
#
# Runner credentials live here and never in ``api_key``: ``api_key.access``
# accepts only read|read_write (store.py, create_api_key), and a runner token is
# neither a person's key nor tenant-readable data. Only the hash is stored, and
# the plaintext is returned exactly once, at registration.
# ---------------------------------------------------------------------------
runner = Table(
    "runner",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("name", String, nullable=False),
    # JSON: the operator's free-form labels (pool, host, region).
    Column("labels", Text),
    # JSON list: the harnesses this runner declared at registration. The lease
    # route refuses to hand out work this runner did not say it can run, so the
    # server is the allowlist authority and a modified runner cannot exceed it.
    Column("harnesses", Text),
    # sha256 of the bearer token. Never the token itself.
    Column("lease_token_hash", String(64), nullable=False, unique=True),
    Column("token_hint", String(8)),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("last_seen", String),
    Column("created_at", String, nullable=False),
    Column("revoked_at", String),
    UniqueConstraint("organization_id", "name", name="uq_runner_org_name"),
)

runner_enrollment = Table(
    "runner_enrollment",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("hint", String(8)),
    Column("label", String),
    Column("created_by", String, ForeignKey("user.id")),
    Column("expires_at", String),
    # Single use: the registration that spends it writes ``used_at`` in the same
    # transaction as the runner insert, so a replay finds it spent (T029).
    Column("used_at", String),
    Column("used_by_runner", String),
    Column("created_at", String, nullable=False),
)

agent_lease = Table(
    "agent_lease",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("task_id", String, ForeignKey("issue.id"), nullable=False, index=True),
    Column("runner_id", String, ForeignKey("runner.id"), index=True),
    # Monotonic per task. A stale fence is refused with 409 on every route
    # (FR-018): a workflow id is an idempotency key, not a fence.
    Column("fence", Integer, nullable=False, default=1),
    # The run identity the daemon journals and keys its effects by.
    Column("run_id", String, nullable=False, unique=True),
    # queued | active | finished | released | canceled | lost
    Column("state", String, nullable=False, index=True),
    Column("attempt", Integer, nullable=False, default=1),
    Column("harness", String, nullable=False),
    Column("prompt", Text, nullable=False),
    # Per-lease capability token, hashed at rest and re-issued on every grant.
    Column("lease_token_hash", String(64)),
    Column("ttl_seconds", Integer, nullable=False),
    # Hoja's clock, not the runner's: the reaper compares this to the server's now.
    Column("lease_until", String),
    Column("granted_at", String),
    Column("last_heartbeat_at", String),
    Column("cancel_requested_at", String),
    Column("cancel_reason", String),
    Column("finish_state", String),
    # The effect key the finish was applied under; the idempotency witness.
    Column("finish_key", String),
    # JSON: the claim's summary/evidence, kept verbatim for the verifier (WP10).
    Column("result", Text),
    # Highest activity seq accepted, so a re-delivered batch is a no-op.
    Column("last_event_seq", Integer, nullable=False, default=0),
    # The policy version this lease was granted under (revision 69). The runner
    # re-checks it on every heartbeat: a policy edited mid-run cancels the lease
    # instead of leaving the old grants in force until the work ends.
    Column("policy_version", Integer),
    # Revision 81: the policy refusal this queued attempt last received at grant time.
    # A refused attempt is skipped (it no longer blocks the queue behind it), and the
    # refusal is audited only when this value changes, not on every poll.
    Column("refusal_reason", String),
    Column("finished_at", String),
    Column("lost_at", String),
    Column("lost_reason", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("task_id", "fence", name="uq_agent_lease_task_fence"),
)

# Revision 82: one timeline per agent run, for debugging through GraphQL (HOJA-602). What the
# runner reported (its events, its warnings and errors, a traceback when a run broke) and what
# the server noted about the run (a refused transition, a policy refusal), in one place, so
# "why did this run fail" is a query rather than a trip to the machine's terminal.
agent_run_log = Table(
    "agent_run_log",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("runner_id", String, ForeignKey("runner.id")),
    # NULL for a line the daemon wrote while it held no lease (start-up, polling).
    Column("lease_id", String, ForeignKey("agent_lease.id")),
    # runner | server
    Column("source", String, nullable=False),
    # event | debug | info | warning | error
    Column("level", String, nullable=False),
    # The event kind, or the logger name for a log line.
    Column("kind", String),
    Column("message", Text, nullable=False),
    # JSON: the event payload, or {traceback, ...} for a log line.
    Column("detail", Text),
    Column("created_at", String, nullable=False),
    Index("ix_agent_run_log_lease", "organization_id", "lease_id", "created_at"),
    Index("ix_agent_run_log_runner", "organization_id", "runner_id", "created_at"),
)

agent_side_effect = Table(
    "agent_side_effect",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("lease_id", String, ForeignKey("agent_lease.id"), index=True),
    Column("run_id", String, nullable=False, index=True),
    Column("seq", Integer, nullable=False),
    # run_id + ":" + seq (FR-019). Unique per organization, which is what makes a
    # duplicate finish record the effect once.
    Column("key", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("payload", Text),
    Column("external_ref", String),
    Column("created_at", String, nullable=False),
    UniqueConstraint("organization_id", "key", name="uq_agent_side_effect_key"),
)

# Outgoing webhooks (wire-compatible). A webhook only receives change events
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
    # Exactly one subject column is set (revision 78): the task, project, initiative
    # or cycle the row is about. issue_id stayed NOT NULL until then.
    Column("issue_id", String, ForeignKey("issue.id"), index=True),
    Column("actor_id", String, ForeignKey("user.id")),
    Column("kind", String, nullable=False),
    Column("created_at", String, nullable=False, index=True),
    Column("read_at", String),
    Column("archived_at", String),
    # What happened, for the row to say it: a state move's from/to, a comment's
    # snippet, the changed fields. JSON text; NULL on rows written before rev 77.
    Column("data", Text),
    Column("comment_id", String),
    # Hidden from the inbox (and its unread count) until this instant passes.
    Column("snoozed_until", String),
    # Non-task subjects and the post a row points at (revision 78). Plain columns,
    # not foreign keys: a deleted update or cycle takes its rows with it in the
    # writer, and an FK here would make that order matter on PostgreSQL.
    Column("project_id", String, index=True),
    Column("initiative_id", String, index=True),
    Column("cycle_id", String, index=True),
    Column("project_update_id", String, index=True),
    Column("initiative_update_id", String, index=True),
)

# Following a project, an initiative or a cycle (revision 78). Same shape as
# issue_subscriber; row-level security derives each one from its parent's FK.
project_subscriber = Table(
    "project_subscriber",
    metadata,
    Column("project_id", String, ForeignKey("project.id"), primary_key=True),
    Column("user_id", String, ForeignKey("user.id"), primary_key=True),
)

initiative_subscriber = Table(
    "initiative_subscriber",
    metadata,
    Column("initiative_id", String, ForeignKey("initiative.id"), primary_key=True),
    Column("user_id", String, ForeignKey("user.id"), primary_key=True),
)

cycle_subscriber = Table(
    "cycle_subscriber",
    metadata,
    Column("cycle_id", String, ForeignKey("cycle.id"), primary_key=True),
    Column("user_id", String, ForeignKey("user.id"), primary_key=True),
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
    # Revision 83: the repository this project's tasks are worked on in (owner/name, a git URL
    # or a path on the machine). An agent delegated a task in the project works in it, on the
    # task's own branch; a project without one leaves the task in a scratch folder.
    Column("repository", String),
)

project_status = Table(
    "project_status",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("name", String, nullable=False),
    # One of backlog | started | completed | canceled — the same four kinds a
    # workflow_state carries, so project status and issue state stay comparable.
    Column("category", String, nullable=False),
    Column("color", String),
    Column("position", Integer, nullable=False, default=0),
    Column("created_at", String),
    Column("updated_at", String),
    UniqueConstraint("organization_id", "name", name="uq_project_status_org_name"),
)

project_template = Table(
    "project_template",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("description", String),
    Column("lead_id", String, ForeignKey("user.id")),
    # A configured project-status NAME, validated at apply time (HOJA-489):
    # stored as text so a later status rename stays a settings edit,
    # not a cascade.
    Column("state", String),
    Column("color", String),
    Column("icon", String),
    # JSON array of task titles created in the project when it is applied.
    Column("task_set", Text),
    Column("archived_at", String),
    Column("created_at", String),
    Column("updated_at", String),
)

project_template_team = Table(
    "project_template_team",
    metadata,
    Column("template_id", String, ForeignKey("project_template.id"), primary_key=True),
    Column("team_id", String, ForeignKey("team.id"), primary_key=True),
)

# HOJA-492: task templates. A NULL team_id is a workspace-level template; a
# team id is the reference's per-team list. Defaults are plain columns (not a
# JSON blob) so the pane edits them one field at a time and issue_create reads
# them without decoding anything but the label list. default_label_ids holds a
# JSON array of issue_label ids, validated at write time with the same
# ancestor rule issue_create applies — a template can never grant a label the
# creator could not have chosen by hand.
task_template = Table(
    "task_template",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("team_id", String, ForeignKey("team.id"), index=True),
    Column("name", String, nullable=False),
    Column("description", String),
    Column("default_title", String, nullable=False),
    Column("default_description", Text),
    Column("default_priority", Integer),
    Column("default_assignee_id", String, ForeignKey("user.id")),
    Column("default_project_id", String, ForeignKey("project.id")),
    Column("default_label_ids", Text),
    Column("archived_at", String),
    Column("created_at", String),
    Column("updated_at", String),
    # SQL treats NULL team_id as distinct, so this constraint only enforces
    # uniqueness among a team's templates; the workspace-level name collision
    # is refused in task_templates.py, where the NULL case can be selected for.
    UniqueConstraint("organization_id", "team_id", "name", name="uq_task_template_org_team_name"),
)

# HOJA-491: SLA policies and the per-task clock they start. A NULL team_id is a
# workspace-wide policy; a team id scopes it, and a team's policy wins over the
# workspace one for the same priority. The clock is a row of its own so a task
# can be read without computing state, and so the pause arithmetic is auditable
# (paused_ms is accumulated, not recomputed from a guessed event log).
sla_policy = Table(
    "sla_policy",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("team_id", String, ForeignKey("team.id"), index=True),
    Column("name", String, nullable=False),
    # JSON array of priorities this policy covers; NULL means every priority.
    Column("applies_priorities", Text),
    Column("first_response_minutes", Integer),
    Column("resolution_minutes", Integer),
    # JSON array of workflow_state ids that pause the clock (waiting on the
    # customer). Stored as ids, not names: a rename must not silently unpause.
    Column("pause_state_ids", Text),
    # HOJA-704. All three are nullable so the previous image keeps writing and
    # reading this table; NULL reads as the value this policy had before the
    # column existed (calendar minutes, stop on completed, every day counts).
    # ``cycle_bound`` makes the resolution deadline the end of the task's cycle;
    # ``stops_on`` is ``completed`` | ``started`` | ``prioritized``;
    # ``business_days_only`` skips Saturday and Sunday when adding minutes.
    Column("cycle_bound", Boolean),
    Column("stops_on", String),
    Column("business_days_only", Boolean),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("archived_at", String),
    Column("created_at", String),
    Column("updated_at", String),
    UniqueConstraint("organization_id", "team_id", "name", name="uq_sla_policy_org_team_name"),
)

sla_clock = Table(
    "sla_clock",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("issue_id", String, ForeignKey("issue.id"), nullable=False, index=True),
    Column("policy_id", String, ForeignKey("sla_policy.id"), nullable=False),
    Column("started_at", String),
    Column("first_response_due_at", String),
    Column("resolution_due_at", String),
    Column("first_responded_at", String),
    Column("resolved_at", String),
    Column("paused_since", String),
    Column("paused_ms", Integer, nullable=False, default=0),
    # Set once, when a read first observes the breach. That is the event
    # Pulse/inbox can show, and it is recorded once rather than per read.
    Column("breached_at", String),
    # HOJA-704: when the clock last moved to another policy or another cycle's
    # end, and when it stopped following a policy at all (the task left every
    # policy's scope). Additive and nullable: an existing clock reads as "never
    # re-targeted, never detached", which is exactly what it is.
    Column("retargeted_at", String),
    Column("detached_at", String),
    Column("created_at", String),
    Column("updated_at", String),
    UniqueConstraint("issue_id", name="uq_sla_clock_issue"),
)

# HOJA-490: per-capability grants for the member role. A missing row means the
# capability's default (see permissions.CAPABILITIES), and every default is
# False — today's behavior, where these actions are admin-only. Only the
# organization that opens the pane stores rows, so nothing changes for a
# workspace that never does.
workspace_permission = Table(
    "workspace_permission",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("capability", String, nullable=False),
    Column("member_allowed", Boolean, nullable=False, default=False),
    Column("updated_by", String, ForeignKey("user.id")),
    Column("updated_at", String),
    UniqueConstraint("organization_id", "capability", name="uq_workspace_permission_org_cap"),
)

# HOJA-490: the workspace security policy. One row per workspace, created on
# first save; a missing row is the conservative default (no idle timeout, any
# sign-in method, no IP allowlist), which is exactly today's auth behavior.
workspace_security_policy = Table(
    "workspace_security_policy",
    metadata,
    Column("organization_id", String, ForeignKey("organization.id"), primary_key=True),
    # Minutes of inactivity after which a browser session is refused. NULL = no
    # idle timeout (only the absolute 14-day expiry applies).
    Column("idle_timeout_minutes", Integer),
    # JSON array of "password" | "google". NULL = every method the server offers.
    Column("allowed_signin_methods", Text),
    # JSON array of CIDR strings. NULL or empty = no restriction.
    Column("ip_allowlist", Text),
    # The workspace-wide Hoja Connect kill switch (revision 71, WP09). NULL reads as enabled, so a
    # workspace that never made the choice keeps the behavior it had.
    Column("connect_enabled", Boolean),
    Column("updated_by", String, ForeignKey("user.id")),
    Column("updated_at", String),
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
    # NULL means "never edited" and reads as created_at. Declared nullable so the
    # migration is a pure ADD COLUMN: a backfill UPDATE would queue deferred
    # realtime-capture trigger events, and PostgreSQL then refuses ALTER TABLE
    # ("cannot ALTER TABLE ... because it has pending trigger events").
    Column("updated_at", String),
    Column("actor_id", String, ForeignKey("user.id")),
    Column("progress", Float),
    Column("health", String),
)

ix_project_update_feed = Index(
    "ix_project_update_feed",
    func.coalesce(project_update.c.updated_at, project_update.c.created_at),
    project_update.c.id)

initiative_update = Table(
    "initiative_update",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", String, ForeignKey("organization.id"), nullable=False, index=True),
    Column("initiative_id", String, ForeignKey("initiative.id"), nullable=False, index=True),
    Column("status", String),
    Column("health", String),
    Column("body", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("actor_id", String, ForeignKey("user.id")),
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
    # When "cycle started" / "cycle ended" went out (revision 80). Claimed with a
    # conditional UPDATE, so the cadence rollover and the scheduled sweep can both
    # try and exactly one of them notifies.
    Column("start_notified_at", String),
    Column("end_notified_at", String),
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
        if os.environ.get("HOJA_DB_POOLER", "").strip().lower() == "transaction":
            # Behind a transaction pooler a server-side prepared statement may land on another backend.
            kwargs["connect_args"] = {"prepare_threshold": None}
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


# Actor attribution (revision 64). A capture trigger cannot see the Python caller,
# so the actor comes from the transaction's session settings — the same seam that
# carries hoja.org_id for row-level security. An unset setting is '' and becomes
# NULL. SQLite has no session settings, so it records no actor: it is the
# single-tenant local mode, where the actor is always the local user.
_ACTOR_PG = ("NULLIF(current_setting('hoja.actor_kind', true), ''), "
             "NULLIF(current_setting('hoja.actor_id', true), ''), "
             "NULLIF(current_setting('hoja.on_behalf_of', true), '')")
_ACTOR_SQLITE = "NULL, NULL, NULL"


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
            "INSERT INTO change_event (organization_id, entity, entity_id, action, team_id, created_at, "
            "actor_kind, actor_id, on_behalf_of) "
            f"VALUES ({org}, '{entity}', CAST({pk_value} AS TEXT), '{label}', "
            f"{team_value}, strftime('%Y-%m-%dT%H:%M:%fZ','now'), {_ACTOR_SQLITE});"
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
    INSERT INTO change_event (organization_id, entity, entity_id, action, team_id, created_at,
                              actor_kind, actor_id, on_behalf_of)
    VALUES ({org_old}, '{entity}', CAST({pk_old} AS TEXT), 'delete', {team_old},
            to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'), {_ACTOR_PG});
    RETURN OLD;
  END IF;
  INSERT INTO change_event (organization_id, entity, entity_id, action, team_id, created_at,
                            actor_kind, actor_id, on_behalf_of)
  VALUES ({org_new}, '{entity}', CAST({pk_new} AS TEXT),
          CASE WHEN TG_OP = 'INSERT' THEN 'create' ELSE 'update' END, {team_new},
          to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'), {_ACTOR_PG});
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
                    "INSERT INTO change_event (organization_id, entity, entity_id, action, team_id, created_at, "
                    "actor_kind, actor_id, on_behalf_of) "
                    "SELECT organization_id, 'issue', id, 'update', team_id, "
                    f"strftime('%Y-%m-%dT%H:%M:%fZ','now'), {_ACTOR_SQLITE} "
                    f"FROM issue WHERE id = {row}.issue_id; END;"
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
  INSERT INTO change_event (organization_id, entity, entity_id, action, team_id, created_at, recipient_id,
                            actor_kind, actor_id, on_behalf_of)
  VALUES (v_org, {entity}, {entity_id}, {action}, {team}, {_PG_CHANGE_TS}, {recipient}, {_ACTOR_PG})
  RETURNING seq INTO v_seq;
  PERFORM pg_notify('{CHANGE_NOTIFY_CHANNEL}',
                    json_build_array(v_seq, v_org, {entity}, {entity_id}, {action}, {team}, {recipient})::text);"""


# Updates that are not changes clients care about (issue number allocation).
_PG_SKIP = {"team": "  IF TG_OP = 'UPDATE' AND OLD.issue_counter IS DISTINCT FROM NEW.issue_counter "
                    "THEN RETURN NULL; END IF;"}
# Per-user entities: the event carries the recipient and only that user receives it.
_RECIPIENT_CAPTURE = {"inbox_notification": "{row}.recipient_id"}


def _install_sqlite_inbox_triggers(conn) -> None:
    """SQLite's recipient-scoped notification triggers. Rebuilding the table drops
    them, so revision 78 reinstalls them through this same definition."""
    for op, label in (("insert", "create"), ("update", "update"), ("delete", "delete")):
        row = "OLD" if op == "delete" else "NEW"
        conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_chg_inbox_notification_{label}")
        conn.exec_driver_sql(
            f"CREATE TRIGGER trg_chg_inbox_notification_{label} AFTER {op.upper()} ON inbox_notification "
            "FOR EACH ROW BEGIN INSERT INTO change_event "
            "(organization_id, entity, entity_id, action, team_id, created_at, recipient_id, "
            "actor_kind, actor_id, on_behalf_of) "
            f"VALUES ({row}.organization_id, 'notification', CAST({row}.id AS TEXT), '{label}', NULL, "
            f"strftime('%Y-%m-%dT%H:%M:%fZ','now'), {row}.recipient_id, {_ACTOR_SQLITE}); END;")


def _apply_realtime_v2_capture(conn):
    """Recipient-scoped notification events; on PostgreSQL also deferred,
    commit-ordered capture with LISTEN/NOTIFY wakeups."""
    if "recipient_id" not in {c["name"] for c in inspect(conn).get_columns("change_event")}:
        conn.exec_driver_sql("ALTER TABLE change_event ADD COLUMN recipient_id VARCHAR")
    if conn.dialect.name == "sqlite":
        # SQLite serializes writers, so its seqs are already commit-ordered; only
        # notification events change, to carry their recipient.
        _install_sqlite_inbox_triggers(conn)
        # Allocating an issue number bumps team.issue_counter; that is not a team
        # change, and publishing it made every client re-bootstrap on issue create.
        conn.exec_driver_sql("DROP TRIGGER IF EXISTS trg_chg_team_update")
        conn.exec_driver_sql(
            "CREATE TRIGGER trg_chg_team_update AFTER UPDATE ON team FOR EACH ROW "
            "WHEN OLD.issue_counter IS NEW.issue_counter BEGIN INSERT INTO change_event "
            "(organization_id, entity, entity_id, action, team_id, created_at, actor_kind, actor_id, on_behalf_of) "
            "VALUES (NEW.organization_id, 'team', CAST(NEW.id AS TEXT), 'update', NEW.id, "
            "strftime('%Y-%m-%dT%H:%M:%fZ','now'), NULL, NULL, NULL); END;")
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
    """Add carry-over-style auto-add-to-cycle toggles on the team table.

    Defaults match the upstream API (both on). Structured so a future cooldown
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


def _retired_meetings(conn) -> None:
    """Meetings shipped at revisions 32-34 and were removed; revision 79 drops their tables.

    Kept as a no-op so the revision chain replays the same way on old and fresh databases."""


def _apply_remove_meetings(conn) -> None:
    """Drop the Meetings tables. Children first, so no foreign key blocks a drop."""
    for table_name in ("meeting_chat", "meeting_ring", "meeting_participant",
                       "meeting_invitee", "meeting"):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table_name}")


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


def _apply_organization_vanity_host(conn):
    """Enterprise workspaces answer on their own host in the shared tier (tenant_hosts.py)."""
    if "vanity_host" in {column["name"] for column in inspect(conn).get_columns("organization")}:
        return
    conn.exec_driver_sql("ALTER TABLE organization ADD COLUMN vanity_host VARCHAR")
    conn.exec_driver_sql("CREATE UNIQUE INDEX uq_organization_vanity_host ON organization (vanity_host)")


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


def _apply_pulse_updates(conn) -> None:
    """Pulse: keep edit history honest and give the initiative feed its own rows.

    Additive. ``project_update`` gains ``updated_at`` so an edit is observable and
    the feed can order by when a post last changed; existing rows stay NULL and
    read as ``created_at``, which reproduces today's ordering exactly without
    writing to a table whose deferred capture triggers forbid in-transaction DDL. ``initiative_update``
    mirrors ``project_update`` for initiatives (Linear's InitiativeUpdate), which
    until now only existed as the field-editing ``initiativeUpdate`` mutation.
    """
    columns = {c["name"] for c in inspect(conn).get_columns("project_update")}
    if "updated_at" not in columns:
        conn.exec_driver_sql("ALTER TABLE project_update ADD COLUMN updated_at VARCHAR")
    initiative_update.create(conn, checkfirst=True)
    # IF NOT EXISTS: SQLite's reflection does not report expression indexes, so an
    # inspect() guard cannot see the one create_all made on a fresh database.
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_project_update_feed ON project_update "
        "(COALESCE(updated_at, created_at), id)")


def _down_pulse_updates(conn) -> None:
    """Reverse of revision 57: drop what it added, data included.

    The forward-only ledger cannot record a down, so this helper exists for the
    up -> down -> up equivalence proof: after down and a second up, the schema
    must be identical. Rows are not preserved across a down (a dropped column
    cannot be reconstituted); that is the standard cost of an additive reversal.
    """
    conn.exec_driver_sql("DROP INDEX IF EXISTS ix_project_update_feed")
    initiative_update.drop(conn, checkfirst=True)
    if "updated_at" in {c["name"] for c in inspect(conn).get_columns("project_update")}:
        conn.exec_driver_sql("ALTER TABLE project_update DROP COLUMN updated_at")
    conn.execute(schema_revision.delete().where(schema_revision.c.revision == 57))


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


def _apply_connector_oauth_state(conn) -> None:
    """Create the organization-scoped OAuth nonce ledger for connector flows.

    Existing deployments have no connector state to migrate: a state value lives
    for ten minutes and only inside a browser redirect, so any in-flight flow is
    already expired by the time this revision runs.
    """
    connector_oauth_state.create(conn, checkfirst=True)


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


_GITHUB_LINK_PULL_COLUMNS = {
    "additions": "INTEGER",
    "deletions": "INTEGER",
    "base_ref": "VARCHAR",
    "head_ref": "VARCHAR",
    "behind": "INTEGER",
    "ahead": "INTEGER",
    "mergeable": "BOOLEAN",
    "merge_state": "VARCHAR",
    "review_decision": "VARCHAR",
    "reviewers": "TEXT",
    "checks_state": "VARCHAR",
    "checks_total": "INTEGER",
    "checks_passing": "INTEGER",
    "checks_failing": "INTEGER",
    "body": "TEXT",
    "merged_at": "VARCHAR",
    "github_updated_at": "VARCHAR",
}


def _apply_github_link_pull_meta(conn):
    """The Reviews pane's pull-request detail on the link row (scripts/enrich-github-link-pulls.sh).

    Every column is nullable and nothing here is authoritative for the issue's own state: a
    pull request's diffstat, refs, mergeability, review decision, check summary and body are
    display data. The webhook does not carry them, so leaving them NULL is a supported state
    and the pane must render an em dash rather than a zero.

    ``updated_at`` on this table is a varchar row-touch stamp this server writes; GitHub's own
    timestamp lands in ``github_updated_at`` so the two are never confused.

    The column probe keeps this a no-op on a fresh database (create_all already made the
    columns) and on one already altered by hand, and it is the portable form: SQLite, which
    the test suite migrates, has no ADD COLUMN IF NOT EXISTS.
    """
    existing = {column["name"] for column in inspect(conn).get_columns("github_link")}
    for name, sql_type in _GITHUB_LINK_PULL_COLUMNS.items():
        if name in existing:
            continue
        conn.exec_driver_sql(f"ALTER TABLE github_link ADD COLUMN {name} {sql_type}")


def _apply_github_link_files(conn):
    """The pull request's per-file diff (scripts/enrich-github-link-files.sh).

    One nullable TEXT column holding a JSON array, not a child table: the Reviews pane
    asks for up to 100 issues in a single query and each issue can carry several links,
    so a child table would make that either a fan-out join or a query per link. Nothing
    filters on a file, so the payload is display data on the link row, exactly like
    ``reviewers``. NULL is a supported state (a link the enrichment has not reached,
    or a real PR whose file list GitHub refused) and the serializer turns it into [].

    The column probe keeps this a no-op on a fresh database (create_all already made
    the column) and on a database where it was added by hand, and it is the portable
    form: SQLite has no ADD COLUMN IF NOT EXISTS.
    """
    existing = {column["name"] for column in inspect(conn).get_columns("github_link")}
    if "files" not in existing:
        conn.exec_driver_sql("ALTER TABLE github_link ADD COLUMN files TEXT")


def _apply_recursive_identifiers(conn):
    """Schema half of HOJA-472: alias ledger and the recursive-identifier columns.

    The data half lives in scripts/migrate_recursive_identifiers.py so that a
    deploy never renumbers a live database as a side effect of starting up.
    Column probes keep this a no-op on a fresh database (create_all already
    made them) and on a database where they were added by hand.
    """
    issue_identifier_alias.create(conn, checkfirst=True)
    issue_columns = {column["name"] for column in inspect(conn).get_columns("issue")}
    if "slot" not in issue_columns:
        conn.exec_driver_sql("ALTER TABLE issue ADD COLUMN slot VARCHAR")
    if "depth" not in issue_columns:
        conn.exec_driver_sql("ALTER TABLE issue ADD COLUMN depth INTEGER")
    team_columns = {column["name"] for column in inspect(conn).get_columns("team")}
    if "child_counter" not in team_columns:
        conn.exec_driver_sql(
            "ALTER TABLE team ADD COLUMN child_counter INTEGER DEFAULT 0")


def _apply_project_settings(conn):
    """HOJA-489: workspace-managed project statuses and project templates.

    Tables only. The default status set (the six state names the frontend
    already offers as free strings) is lazily seeded per workspace on first
    read by project_settings.ensure_default_statuses, so an existing database,
    a fresh database, and a workspace created after this migration all
    converge without touching organization creation.
    """
    project_status.create(conn, checkfirst=True)
    project_template.create(conn, checkfirst=True)
    project_template_team.create(conn, checkfirst=True)


def _apply_workspace_permissions(conn):
    """HOJA-490: capability grants for members, and the workspace security policy.

    Additive. Both tables are empty after this runs: an empty
    ``workspace_permission`` means every capability keeps its default (False,
    which is exactly the admin-only behavior of revision 58), and an absent
    ``workspace_security_policy`` row means no idle timeout, no sign-in-method
    restriction and no IP allowlist. ``browser_session.last_seen_at`` is added
    NULL, and NULL reads as ``created_at`` in ``store.session_idle_ok``, so a
    session opened before this revision keeps the lifetime it was created with.
    """
    workspace_permission.create(conn, checkfirst=True)
    workspace_security_policy.create(conn, checkfirst=True)
    columns = {c["name"] for c in inspect(conn).get_columns("browser_session")}
    if "last_seen_at" not in columns:
        conn.exec_driver_sql("ALTER TABLE browser_session ADD COLUMN last_seen_at VARCHAR")


def _apply_sla(conn):
    """HOJA-491: SLA policies and the per-task clock.

    Additive and inert. Nothing reads either table until a workspace creates a
    policy; with no policy, ``issue_create`` writes exactly the row it wrote
    before this revision, and a rollback target that predates 61 still reads
    every task it knows about.
    """
    sla_policy.create(conn, checkfirst=True)
    sla_clock.create(conn, checkfirst=True)


def _down_sla(conn) -> None:
    """Reverse of revision 61, for the up -> down -> up equivalence proof.

    The clock rows are derived state (recomputable from the issues and their
    activity), so dropping them loses nothing a live policy cannot re-earn;
    the policies themselves are configuration and are lost, as with any
    additive reversal.
    """
    sla_clock.drop(conn, checkfirst=True)
    sla_policy.drop(conn, checkfirst=True)
    conn.execute(schema_revision.delete().where(schema_revision.c.revision == 61))


def _apply_sla_options(conn) -> None:
    """Make a policy configurable and cycle-aware (revision 74, HOJA-704).

    Five additive, nullable columns and nothing else: three per-policy settings
    on ``sla_policy`` and two timestamps on ``sla_clock``. No table is created,
    so ``rls_policy`` classifies exactly what it classified before and the
    readiness probe is unaffected.

    Every NULL means "the behaviour this policy already had": ``cycle_bound``
    NULL is off, ``stops_on`` NULL is ``completed``, ``business_days_only``
    NULL is off. A previous image writing to this table omits them and the
    defaults still hold, and a rollback target that predates this revision
    reads every policy and clock row it wrote.

    Idempotent by construction: each column is added only when the catalog does
    not already have it, so a second ``hoja-serve migrate`` is a no-op.
    """
    policy_columns = {c["name"] for c in inspect(conn).get_columns("sla_policy")}
    for name, ddl in (
        ("cycle_bound", "BOOLEAN"),
        ("stops_on", "VARCHAR"),
        ("business_days_only", "BOOLEAN"),
    ):
        if name not in policy_columns:
            conn.exec_driver_sql(f"ALTER TABLE sla_policy ADD COLUMN {name} {ddl}")
    clock_columns = {c["name"] for c in inspect(conn).get_columns("sla_clock")}
    for name, ddl in (("retargeted_at", "VARCHAR"), ("detached_at", "VARCHAR")):
        if name not in clock_columns:
            conn.exec_driver_sql(f"ALTER TABLE sla_clock ADD COLUMN {name} {ddl}")


def _apply_task_templates(conn):
    """HOJA-492: the task-template store.

    Additive and inert: no query or mutation reads this table until the pane
    lands, and ``issue_create`` behaves exactly as before for a caller that
    does not send ``templateId``. No existing column changes type or
    nullability, so a rollback target that predates this revision still reads
    every row it knows about.
    """
    task_template.create(conn, checkfirst=True)


def _down_task_templates(conn) -> None:
    """Reverse of revision 60, for the up -> down -> up equivalence proof.

    Rows are not preserved across a down: a template is configuration authored
    through the pane, and a dropped table cannot hand them back. The forward
    ledger records nothing for a down, so this helper removes the ledger row
    itself, exactly like ``_down_pulse_updates``.
    """
    task_template.drop(conn, checkfirst=True)
    conn.execute(schema_revision.delete().where(schema_revision.c.revision == 60))


def _set_not_null(conn, table: Table, name: str) -> None:
    """Make ``table.name`` NOT NULL on both backends.

    PostgreSQL alters the column in place. SQLite has no ALTER COLUMN, so the table
    is rebuilt from its declared definition — which already says NOT NULL — with the
    rows, indexes and unique constraints carried over, all inside the revision's
    transaction.
    """
    if conn.dialect.name != "sqlite":
        conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ALTER COLUMN "{name}" SET NOT NULL')
        return
    inspector = inspect(conn)
    indexes = inspector.get_indexes(table.name)
    # Inline UNIQUE constraints travel with the declared table; standalone unique
    # indexes (api_key's uq_api_key_token_hash was created by raw SQL) do not.
    # The schema is cloned into a throwaway MetaData first so the staged table's
    # foreign keys resolve; only the staged table is ever created.
    scratch = MetaData()
    for other in metadata.sorted_tables:
        other.to_metadata(scratch)
    staging = table.to_metadata(scratch, name=f"{table.name}__rebuild")
    staging.indexes.clear()
    staging.create(conn)
    columns = ", ".join(f'"{column.name}"' for column in table.columns)
    conn.exec_driver_sql(f'INSERT INTO "{staging.name}" ({columns}) SELECT {columns} FROM "{table.name}"')
    table.drop(conn)
    conn.exec_driver_sql(f'ALTER TABLE "{staging.name}" RENAME TO "{table.name}"')
    for index in indexes:
        cols = ", ".join(f'"{c}"' for c in index["column_names"] if c is not None)
        unique = "UNIQUE " if index.get("unique") else ""
        conn.exec_driver_sql(f'CREATE {unique}INDEX "{index["name"]}" ON "{table.name}" ({cols})')


def _apply_api_key_organization(conn) -> None:
    """Backfill ``api_key.organization_id`` from the owner and make it mandatory (F8).

    The backfill is the only defensible source of truth: a key belongs to the
    organization its user belongs to, which is exactly the equality ``resolve_token``
    already joins on. A key that cannot be backfilled (no owner, or an owner with no
    organization) authenticates nothing today and would authenticate nothing after the
    constraint; the revision refuses to invent an organization for it and names the
    rows instead, unless the operator opts into dropping those dead rows.
    """
    owner_org = select(user.c.organization_id).where(user.c.id == api_key.c.user_id).scalar_subquery()
    conn.execute(api_key.update().where(api_key.c.organization_id.is_(None))
                 .values(organization_id=owner_org))
    orphaned = conn.execute(select(api_key.c.id, api_key.c.label)
                            .where(api_key.c.organization_id.is_(None))).all()
    if orphaned:
        listing = ", ".join(f"{row[0]} ({row[1] or 'no label'})" for row in orphaned[:20])
        if os.environ.get("HOJA_MIGRATION_PRUNE_ORPHAN_API_KEYS") != "1":
            raise RuntimeError(
                f"api_key.organization_id cannot become NOT NULL: {len(orphaned)} key(s) have "
                f"no organization and no owner to take one from: {listing}. They authenticate "
                "nobody (resolve_token joins the owner on organization equality). Re-point them "
                "with an UPDATE, or set HOJA_MIGRATION_PRUNE_ORPHAN_API_KEYS=1 to delete them."
            )
        conn.execute(api_key.delete().where(api_key.c.organization_id.is_(None)))
        log.warning("revision 62 pruned %s api_key rows with no organization: %s",
                    len(orphaned), listing)
    _set_not_null(conn, api_key, "organization_id")


def _apply_org_bound_secrets(conn) -> None:
    """Re-seal every credential written before per-organization keys (F5).

    A pre-``v2`` ciphertext was sealed with the master key and no associated data, so
    the same blob opens inside every organization. Nothing in the server will open one
    any more; this revision rewrites the stored ones under their own organization's key
    in the transaction that records the revision, so a failure leaves the ledger and the
    rows exactly where they were. A database with nothing to rewrite needs no key.
    """
    from hoja_server import secrets_box

    legacy = secrets_box.count_legacy(conn)
    remaining = sum(legacy.values())
    if not remaining:
        return
    if not os.environ.get(secrets_box.KEY_ENV):
        raise RuntimeError(
            f"revision 63 must re-seal {remaining} credential(s) but {secrets_box.KEY_ENV} "
            "is not set; run the migration with the same key the server was sealed with."
        )
    report = secrets_box.reseal_legacy(
        conn, allow_unopenable=os.environ.get("HOJA_RESEAL_SKIP_UNOPENABLE") == "1")
    log.warning("revision 63 resealed %s credential(s) per organization: %s",
                report.total, report.resealed)
    for table_name, row_id, error in report.unopenable:
        log.error("revision 63 left %s row %s unsealed: %s", table_name, row_id, error)


def _apply_agent_identity(conn) -> None:
    """Agent identity: agent_member, issue.delegate_id, and an actor on every change (F4).

    All three are additive, so a deployment that creates no agent member, delegates
    nothing and runs no agent turn behaves exactly as it does today. The capture
    triggers are rebuilt on PostgreSQL because a trigger — not Python — writes
    change_event: the actor reaches the row only through the transaction's session
    settings, so the function bodies have to read them. SQLite has no session
    settings, so its triggers insert NULL actor columns and local mode records no
    attribution; that is the same single-tenant boundary row-level security stops at.
    """
    if "delegate_id" not in {c["name"] for c in inspect(conn).get_columns("issue")}:
        conn.exec_driver_sql("ALTER TABLE issue ADD COLUMN delegate_id VARCHAR")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_issue_delegate_id ON issue (delegate_id)")
    captured = {c["name"] for c in inspect(conn).get_columns("change_event")}
    for name in ("actor_kind", "actor_id", "on_behalf_of"):
        if name not in captured:
            conn.exec_driver_sql(f"ALTER TABLE change_event ADD COLUMN {name} VARCHAR")
    agent_member.create(conn, checkfirst=True)
    if conn.dialect.name == "postgresql":
        # The column-only ALTER above carries no constraint; add the one a fresh
        # create_all gets, under the same name so both paths converge.
        conn.exec_driver_sql("""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_issue_delegate_id') THEN
    ALTER TABLE issue ADD CONSTRAINT fk_issue_delegate_id
      FOREIGN KEY (delegate_id) REFERENCES "user" (id);
  END IF;
END $$;""")
        # Rebuild every capture function so it reads the actor settings.
        _apply_realtime_v2_capture(conn)


def _apply_audit_log(conn) -> None:
    """Append-only audit trail (HOJA-602-A-8, WP05).

    Additive: a deployment that audits nothing keeps every behavior it has, and
    the trigger only exists on the new table.

    Immutability is a trigger, not a grant. REVOKE UPDATE, DELETE would be the
    smaller statement, but any deployment (or test harness) that re-grants on
    all tables silently re-opens the ledger, so the refusal has to be attached
    to the table itself. The trigger fires for the table owner too — which is
    why the chain test disables it explicitly to prove ``verify_chain`` notices
    a rewrite.
    """
    audit_event.create(conn, checkfirst=True)
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_audit_event_organization_seq "
        "ON audit_event (organization_id, seq)")
    if conn.dialect.name == "postgresql":
        # No `%` anywhere in this function body: psycopg treats it as a pyformat
        # placeholder even when SQLAlchemy sends no parameters, and the CREATE
        # FUNCTION then fails at migration time rather than at the refusal.
        conn.exec_driver_sql("""
CREATE OR REPLACE FUNCTION audit_event_append_only() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
  RAISE EXCEPTION 'audit_event is append-only: updates and deletes are refused'
    USING ERRCODE = 'insufficient_privilege';
END;
$fn$;""")
        conn.exec_driver_sql(
            "DROP TRIGGER IF EXISTS trg_audit_event_append_only ON audit_event")
        conn.exec_driver_sql(
            "CREATE TRIGGER trg_audit_event_append_only BEFORE UPDATE OR DELETE "
            "ON audit_event FOR EACH ROW EXECUTE FUNCTION audit_event_append_only()")
        # Also close the grant, for deployments that do not re-grant broadly.
        conn.exec_driver_sql("REVOKE UPDATE, DELETE ON audit_event FROM PUBLIC")
    elif conn.dialect.name == "sqlite":
        for op in ("UPDATE", "DELETE"):
            conn.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS trg_audit_event_no_{op.lower()} "
                f"BEFORE {op} ON audit_event BEGIN "
                f"SELECT RAISE(ABORT, 'audit_event is append-only: {op} refused'); END")


def _apply_runner_lease(conn) -> None:
    """Runner lease: runner, runner_enrollment, agent_lease, agent_side_effect (WP07).

    Additive only. Nothing existing changes shape, so the previous image keeps
    serving a database that has already run this revision, and a workspace with
    no runner enrolled pays one empty table each.

    ``agent_lease`` (task, fence) is unique rather than merely indexed: the fence
    is the only proof that a write comes from the runner that currently owns the
    task, so two leases may never share one. The reaper allocates ``max(fence)+1``
    per task, which is why the constraint can be a plain unique index.
    """
    runner.create(conn, checkfirst=True)
    runner_enrollment.create(conn, checkfirst=True)
    agent_lease.create(conn, checkfirst=True)
def _apply_agent_delegation(conn) -> None:
    """Delegation, the schedule check and the Linear session (HOJA-602-A-7, WP06).

    Additive: three new tables plus one nullable-defaulted column on agent_member.
    A deployment that delegates nothing creates no session and every existing
    query is unchanged. The ten assignment states live in ``agent_session.state``
    and Linear's seven statuses in ``agent_session.status``: the status is derived
    from the state, never the other way round, because ``complete`` is a session
    status while ``in_review`` and ``done`` are issue states.
    """
    if "require_approval" not in {c["name"] for c in inspect(conn).get_columns("agent_member")}:
        conn.exec_driver_sql(
            "ALTER TABLE agent_member ADD COLUMN require_approval BOOLEAN NOT NULL DEFAULT FALSE"
        )
    agent_session.create(conn, checkfirst=True)
    agent_activity.create(conn, checkfirst=True)
    agent_side_effect.create(conn, checkfirst=True)


def _apply_agent_verification(conn) -> None:
    """The claim and its verdict (HOJA-602-A-13, WP10).

    Additive: one new table plus seven nullable columns on ``agent_session``. A
    deployment whose runners never submit a claim has no verification row and
    reads every session exactly as revision 67 left it — the columns are NULL,
    not defaulted, so "nobody has signed" is distinguishable from "signed as
    uncertain".

    The verifier's identity is a foreign key to ``user`` on PostgreSQL as well as
    on a fresh create, because a verdict that names a principal which does not
    exist cannot be separation-checked later.
    """
    columns = {c["name"]: c for c in inspect(conn).get_columns("agent_session")}
    additive = {
        "claim": "TEXT",
        "claim_at": "VARCHAR",
        "worker_model": "VARCHAR",
        "verifier_model": "VARCHAR",
        "verdict": "VARCHAR",
        "verdict_at": "VARCHAR",
        "verifier_id": "VARCHAR",
    }
    for name, sql_type in additive.items():
        if name not in columns:
            conn.exec_driver_sql(f"ALTER TABLE agent_session ADD COLUMN {name} {sql_type}")
    agent_verification.create(conn, checkfirst=True)
    if conn.dialect.name == "postgresql":
        # The column-only ALTER carries no constraint; add the one a fresh
        # create_all gets, under the same name so both paths converge.
        conn.exec_driver_sql("""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_agent_session_verifier_id') THEN
    ALTER TABLE agent_session ADD CONSTRAINT fk_agent_session_verifier_id
      FOREIGN KEY (verifier_id) REFERENCES "user" (id);
  END IF;
END $$;""")


def _apply_agent_policy(conn) -> None:
    """What an agent may touch (revision 69, HOJA-602-A-14 / WP12).

    Additive: two new tables plus one nullable column on ``agent_lease``. A
    workspace that never writes a policy row creates nothing and reads exactly
    what revision 68 left, and an existing lease keeps ``policy_version`` NULL —
    which is the honest answer ("granted before this revision") rather than a
    version nobody recorded.

    ``agent_secret.secret_sealed`` is sealed per organization by ``secrets_box``
    and is listed in ``SEALED_COLUMNS``, so the reseal path and the tests both
    see it; a ciphertext copied into another workspace's row cannot be opened.
    """
    agent_policy.create(conn, checkfirst=True)
    agent_secret.create(conn, checkfirst=True)
    columns = {c["name"] for c in inspect(conn).get_columns("agent_lease")}
    if "policy_version" not in columns:
        # Nullable, so a lease awarded by an older image keeps working and says
        # "no version recorded" instead of claiming a policy it never checked.
        conn.exec_driver_sql("ALTER TABLE agent_lease ADD COLUMN policy_version INTEGER")


def _apply_usage_ledger(conn) -> None:
    """The append-only usage ledger (revision 70, HOJA-602-A-12 / WP14).

    Additive: one new table, no column on an existing table changes, so the
    previous image keeps serving a database that has already run this revision.
    A workspace that has never delegated a run creates no row here, and the
    budget check reads an empty ledger as "nothing spent yet" — which is the
    truth for that workspace, not a permissive default.

    The table carries ``organization_id`` directly, so ``rls_policy`` classifies
    it ``direct`` from the catalog and ``db.migrate`` binds it with the same
    ``hoja_tenant`` policy as every other tenant table. A table with no policy
    makes ``/ready`` answer 503, so nothing here is left to an operator step.
    """
    usage_event.create(conn, checkfirst=True)


def _apply_connect_endpoints(conn) -> None:
    """The Hoja Connect exposure record (revision 71, HOJA-602-A-10 / WP09).

    Additive: one new table plus one nullable column on ``workspace_security_policy``, so the
    previous image keeps serving a database that has already run this revision. A workspace that
    never opened a tunnel creates no row here, and the Connect screen reads an empty list — which
    is the truth for that workspace, not a permissive default.

    ``connect_endpoint`` carries ``organization_id`` directly, so ``rls_policy`` classifies it
    ``direct`` from the catalog and ``db.migrate`` binds it with the same ``hoja_tenant`` policy as
    every other tenant table (``bind_rls`` runs after this revision). A table with no policy makes
    ``/ready`` answer 503, so nothing here is left to an operator step.

    ``workspace_security_policy.connect_enabled`` is the org kill switch WP09 requires ("an org
    admin can turn Connect off"). It is NULL for every existing workspace, and NULL reads as
    *enabled*: a workspace that never made the choice keeps the behaviour it had, which is what
    keeps this migration from changing anyone's answer.
    """
    connect_endpoint.create(conn, checkfirst=True)
    columns = {c["name"] for c in inspect(conn).get_columns("workspace_security_policy")}
    if "connect_enabled" not in columns:
        # Nullable with no server default: NULL means "never decided", i.e. enabled. A NOT NULL
        # default would write a row nobody asked for on every workspace that has a policy row.
        conn.exec_driver_sql("ALTER TABLE workspace_security_policy ADD COLUMN connect_enabled BOOLEAN")


def _apply_agent_policy_harness(conn) -> None:
    """Which harness an agent's runs execute under (revision 72, HOJA-602-A-22).

    Additive and nullable, so the previous image keeps serving a database that has
    already run this revision and every existing policy keeps its behaviour:
    ``default_harness`` NULL means "nobody chose", which the queue reads as ``pi``
    (what every delegation was queued for before this column existed), and
    ``harness_allowlist`` NULL/empty means "no allowlist", i.e. any known profile.

    No table is created here, so there is nothing new for ``rls_policy`` to
    classify: both columns land on ``agent_policy``, which revision 69 already
    created with ``organization_id`` and which the catalog therefore classifies
    ``direct`` before ``bind_rls`` installs its ``hoja_tenant`` policy. A table with
    no policy makes ``/ready`` answer 503; a column cannot, so this revision leaves
    the readiness probe exactly as it found it.

    Idempotent by construction: each column is added only when the catalog does not
    already have it, so ``hoja-serve migrate`` run twice is a no-op the second time
    (and so is a migrate that raced another owner to the same revision).
    """
    columns = {c["name"] for c in inspect(conn).get_columns("agent_policy")}
    if "default_harness" not in columns:
        conn.exec_driver_sql("ALTER TABLE agent_policy ADD COLUMN default_harness VARCHAR")
    if "harness_allowlist" not in columns:
        conn.exec_driver_sql("ALTER TABLE agent_policy ADD COLUMN harness_allowlist TEXT")


def _apply_entity_url_hosts(conn) -> None:
    """Put every stored entity link on the host its workspace is served from (revision 73).

    One database serves several workspaces, but every link was rendered from the
    process-wide ``HOJA_APP_URL``, so a workspace with its own host
    (``organization.vanity_host``, e.g. ``core.hoja.work``) stores links that open
    the canonical host and therefore the wrong workspace: production Core had
    99/99 issues and 7/7 projects pointing at ``https://hoja.hoja.work/core/…``.
    The host on the organization row is the source of truth, so the fix is to say
    so once (``tenant_base_url``) and move the rows written under the old rule.

    Only a link whose path is still the one ``entity_url`` writes
    (``<origin>/<url_key>/<kind>/<key>``) is rewritten — see
    ``_hosted_entity_url`` — so an attachment's object-store URL, a webhook
    endpoint or a connector's external link is left exactly as it is. Writes go
    row by row through the ORM-updatable tables, which is portable to SQLite and
    PostgreSQL alike; the migration role owns those tables, so the
    ``hoja_tenant`` policies (ENABLE, never FORCE) do not hide their rows.

    Idempotent: a row already on its host does not move, so a second run is a
    no-op and a migrate that raced another owner to this revision is harmless.
    """
    org_ids = list(conn.execute(
        select(organization.c.id).where(organization.c.vanity_host.isnot(None))
    ).scalars())
    moved = 0
    for org_id in org_ids:
        moved += repair_entity_urls(conn, org_id)
    if moved:
        log.warning("revision 73 moved %s entity link(s) onto their workspace host", moved)


def _apply_project_update_comments(conn):
    """Schema half of Pulse comments: a comment may hang off a project update.

    One nullable column rather than a second table: the body, the author, the
    archive flag and the comment policy are identical for both parents, and a
    second table would duplicate ``comment_policy``'s matrix. ``issue_id`` is
    equally nullable, so a row carries exactly one parent and the writer refuses
    a call that names both or neither.

    No CHECK constraint: SQLite has no ADD CONSTRAINT, so it would need a table
    rebuild, and the writer already enforces the invariant at the only entry
    point. No backfill either — an existing comment simply keeps ``issue_id``.

    The column probe keeps this a no-op on a fresh database (create_all already
    made the column) and on one altered by hand.
    """
    existing = {column["name"] for column in inspect(conn).get_columns("comment")}
    if "project_update_id" not in existing:
        conn.exec_driver_sql("ALTER TABLE comment ADD COLUMN project_update_id VARCHAR")
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_comment_project_update_id ON comment (project_update_id)")


def _apply_project_repository(conn):
    """``project.repository``: the repo a project's agent tasks are worked on in (HOJA-602)."""
    existing = {column["name"] for column in inspect(conn).get_columns("project")}
    if "repository" not in existing:
        conn.exec_driver_sql("ALTER TABLE project ADD COLUMN repository VARCHAR")


def _apply_agent_run_log(conn):
    """``agent_run_log``: the per-run timeline GraphQL serves (HOJA-602)."""
    agent_run_log.create(conn, checkfirst=True)


def _apply_lease_refusal_reason(conn):
    """``agent_lease.refusal_reason``: the last policy refusal a queued attempt got.

    Nullable, no backfill: an attempt that was never refused has nothing to record. The
    column probe keeps a fresh database (create_all made it) and a replay a no-op.
    """
    existing = {column["name"] for column in inspect(conn).get_columns("agent_lease")}
    if "refusal_reason" not in existing:
        conn.exec_driver_sql("ALTER TABLE agent_lease ADD COLUMN refusal_reason VARCHAR")


def _apply_organization_agents_enabled(conn):
    """``organization.agents_enabled``: the workspace switch for the Agents screens.

    Nullable with no backfill, so every existing workspace reads as off until an admin turns
    it on. The column probe keeps a fresh database (create_all made it) and a replay no-ops.
    """
    existing = {column["name"] for column in inspect(conn).get_columns("organization")}
    if "agents_enabled" not in existing:
        conn.exec_driver_sql("ALTER TABLE organization ADD COLUMN agents_enabled BOOLEAN")


def _apply_inbox_notification_detail(conn):
    """``inbox_notification.data`` / ``comment_id`` / ``snoozed_until``.

    Three nullable columns, no backfill: a row written before this revision has no
    payload and reads as its bare kind. The recipient change-event triggers are
    row-level and name no columns beyond ids, so they need no rebuild.
    """
    existing = {column["name"] for column in inspect(conn).get_columns("inbox_notification")}
    for name, sql_type in (("data", "TEXT"), ("comment_id", "VARCHAR"), ("snoozed_until", "VARCHAR")):
        if name not in existing:
            conn.exec_driver_sql(f"ALTER TABLE inbox_notification ADD COLUMN {name} {sql_type}")


_INBOX_SUBJECT_COLUMNS = ("project_id", "initiative_id", "cycle_id",
                          "project_update_id", "initiative_update_id")


def _apply_cycle_event_stamps(conn):
    """``cycle.start_notified_at`` / ``end_notified_at``: one notification per edge.

    The scheduled sweep (``cycle_events``) announces a cycle when its start or end
    passes. Every cycle whose edge is already in the past is stamped here, so the
    first sweep after this revision does not announce history. The column probe
    keeps a fresh database (create_all made them) and a replay no-ops.
    """
    existing = {column["name"] for column in inspect(conn).get_columns("cycle")}
    for name in ("start_notified_at", "end_notified_at"):
        if name not in existing:
            conn.exec_driver_sql(f"ALTER TABLE cycle ADD COLUMN {name} VARCHAR")
    stamp = now_iso()
    conn.execute(cycle.update().where(and_(
        cycle.c.start_notified_at.is_(None), cycle.c.starts_at.is_not(None),
        cycle.c.starts_at <= stamp)).values(start_notified_at=stamp))
    conn.execute(cycle.update().where(and_(
        cycle.c.end_notified_at.is_(None),
        or_(cycle.c.completed_at.is_not(None),
            and_(cycle.c.ends_at.is_not(None), cycle.c.ends_at <= stamp)))).values(end_notified_at=stamp))


def _apply_follow_subjects(conn):
    """Follow projects, initiatives and cycles; a notification can be about any of them.

    Three subscriber tables, and ``inbox_notification`` gains one nullable column per
    non-task subject while ``issue_id`` becomes nullable.

    PostgreSQL alters in place, which keeps its triggers. SQLite has no ALTER COLUMN,
    so the table is rebuilt from its declared definition (already nullable, already
    carrying the new columns) with rows and indexes carried over; dropping the table
    drops its triggers, so the recipient-scoped change triggers are reinstalled.
    Both paths are no-ops on a fresh database, where create_all already built the
    declared shape.
    """
    for table in (project_subscriber, initiative_subscriber, cycle_subscriber):
        table.create(conn, checkfirst=True)
    inspector = inspect(conn)
    columns = {c["name"]: c for c in inspector.get_columns("inbox_notification")}
    missing = [name for name in _INBOX_SUBJECT_COLUMNS if name not in columns]
    issue_required = not columns["issue_id"].get("nullable", True)
    if conn.dialect.name != "sqlite":
        for name in missing:
            conn.exec_driver_sql(f'ALTER TABLE inbox_notification ADD COLUMN "{name}" VARCHAR')
        if issue_required:
            conn.exec_driver_sql('ALTER TABLE inbox_notification ALTER COLUMN "issue_id" DROP NOT NULL')
        for name in _INBOX_SUBJECT_COLUMNS:
            conn.exec_driver_sql(
                f'CREATE INDEX IF NOT EXISTS "ix_inbox_notification_{name}" ON inbox_notification ("{name}")')
        return
    if not missing and not issue_required:
        return
    table = inbox_notification
    indexes = inspector.get_indexes(table.name)
    scratch = MetaData()
    for other in metadata.sorted_tables:
        other.to_metadata(scratch)
    staging = table.to_metadata(scratch, name=f"{table.name}__rebuild")
    staging.indexes.clear()
    staging.create(conn)
    carried = ", ".join(f'"{name}"' for name in columns if name in table.c)
    conn.exec_driver_sql(f'INSERT INTO "{staging.name}" ({carried}) SELECT {carried} FROM "{table.name}"')
    conn.exec_driver_sql(f'DROP TABLE "{table.name}"')
    conn.exec_driver_sql(f'ALTER TABLE "{staging.name}" RENAME TO "{table.name}"')
    recreated = set()
    for index in indexes:
        cols = ", ".join(f'"{c}"' for c in index["column_names"] if c is not None)
        unique = "UNIQUE " if index.get("unique") else ""
        conn.exec_driver_sql(f'CREATE {unique}INDEX IF NOT EXISTS "{index["name"]}" ON "{table.name}" ({cols})')
        recreated.add(index["name"])
    for index in table.indexes:
        if index.name not in recreated:
            index.create(conn, checkfirst=True)
    _install_sqlite_inbox_triggers(conn)


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
    32: _retired_meetings,
    33: _retired_meetings,
    34: _retired_meetings,
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
    47: _apply_organization_vanity_host,
    # Connector migrations renumbered 47/48 -> 48/49 on the merge: main already shipped 47
    # (organization.vanity_host) for the shared tier, so the connector ledger continues after it.
    # 48: a provider whose access token expires (Calendly) stores both halves of the grant.
    48: _apply_connector_access_token,
    49: _apply_connector_external_id,
    # 50: connector OAuth nonces carry the organization, so they are policed by RLS like the
    # rest of the connector tables instead of living in the global allowlist.
    50: _apply_connector_oauth_state,
    # 51: an instance-addressed connector (Grafana) stores its origin on the account row. The
    # branch that carried this revision numbered it 49 from a tree without the vanity-host and
    # OAuth-state revisions, so it takes the next free number here rather than renumbering a
    # revision main already shipped.
    51: _apply_connector_external_url,
    # 52: one GitHub object linked to one issue, unique per (org, kind, external id) so a
    # re-delivered webhook upserts rather than duplicates. Renumbered 51 -> 52 on the merge
    # because main shipped 51 (_apply_connector_external_url) first; the ledger is a
    # contiguous prefix, so the later branch takes the next free numbers.
    52: lambda conn: github_link.create(conn, checkfirst=True),
    # 53: the delivery-id ledger that makes a re-sent webhook a no-op.
    53: lambda conn: github_delivery.create(conn, checkfirst=True),
    # 54: the pull-request detail the Reviews pane renders (diffstat, refs, mergeability,
    # review decision, check summary, body, GitHub's own updated_at). All nullable: the
    # webhook does not carry it, so scripts/enrich-github-link-pulls.sh fills it and a link
    # with no enrichment still reads correctly.
    54: _apply_github_link_pull_meta,
    # 55: the pull request's per-file diff, as JSON text on the link row (one read for the
    # whole Reviews list, no child-table join). Nullable, filled by
    # scripts/enrich-github-link-files.sh; NULL reads back as an empty file list.
    55: _apply_github_link_files,
    # 56: recursive identifiers — the alias ledger plus the two columns that
    # let the allocator compose HOJA-416-A-1 without touching a top-level
    # number. The data migration is deliberately NOT here: it is runnable
    # forward and back by scripts/migrate_recursive_identifiers.py, after a
    # backup, and is never applied implicitly by a deploy.
    56: _apply_recursive_identifiers,
    # 57: Pulse — project_update.updated_at (backfilled from created_at) and the
    # initiative_update table, so the feed orders by last change and initiative
    # updates exist as rows of their own.
    57: _apply_pulse_updates,
    # 58: settings-controlled project statuses (ordered, editable, replacing
    # the free-string Project.state) and project templates applied at
    # projectCreate time. Renumbered 57 -> 58 on the merge: main shipped 57
    # (_apply_pulse_updates) first, so this branch takes the next free number.
    # Status defaults are lazily seeded per workspace on first read; see
    # _apply_project_settings.
    58: _apply_project_settings,
    # 59: permissions and security settings (HOJA-490) — per-capability member
    # grants, the workspace security policy, and the session's last-seen
    # timestamp the idle timeout is measured from. All three are inert until a
    # workspace saves a policy, so the migration preserves today's behavior.
    59: _apply_workspace_permissions,
    # 60: task templates (HOJA-492) — the workspace-level and per-team
    # template store, plus the defaults issue_create expands when a caller
    # sends templateId. Additive only: no column on an existing table changes,
    # so the previous image keeps working against this schema.
    60: _apply_task_templates,
    # 61: SLA policies and the per-task clock (HOJA-491). Additive; inert until
    # a workspace creates a policy.
    61: _apply_sla,
    # 62: api_key.organization_id is the tenant column, so it is backfilled from the
    # key's owner and made NOT NULL (HOJA-602-A-1, F8). Constraining, not additive:
    # a writer that omits the organization now fails at the database rather than
    # leaving a row that authenticates nobody.
    62: _apply_api_key_organization,
    # 63: re-seal every credential written before per-organization keys, under its
    # own organization's key (HOJA-602-A-1, F5). Data-only; a database with nothing
    # legacy to rewrite is a no-op and needs no HOJA_SECRET_KEY.
    63: _apply_org_bound_secrets,
    # 64: agent identity (HOJA-602-A-3, F4) — agent_member, issue.delegate_id, and
    # actor_kind / actor_id / on_behalf_of on change_event. Additive; the capture
    # functions are rebuilt so a delegated agent's writes are attributable.
    64: _apply_agent_identity,
    # 65: the append-only audit trail (HOJA-602-A-8, WP05). The work-package
    # prompt says this revision "owns schema revision 64", but WP04 shipped 64
    # (agent identity) and this work stacks on it, so the next free number is
    # taken rather than renumbering a revision another branch already owns. The
    # ledger stays a contiguous prefix.
    65: _apply_audit_log,
    # 66: the runner lease (HOJA-602-A-6, WP07) — runner, single-use
    # runner_enrollment, agent_lease with a monotonic fence, and the
    # agent_side_effect ledger keyed run_id + seq (FR-018, FR-019, FR-020).
    # Additive; the reaper is what turns an expired lease into work for the next
    # runner, and it uses Hoja's clock, never the runner's process.
    66: _apply_runner_lease,
    # 67: delegation (HOJA-602-A-7, WP06) — agent_session, agent_activity and
    # agent_member.require_approval. Additive. The side-effect ledger this work
    # package's design sketched belongs to WP07 (66) and is used, not duplicated:
    # one idempotency witness per run, keyed run_id + seq.
    67: _apply_agent_delegation,
    # 68: the claim and the verdict (HOJA-602-A-13, WP10). Additive: the columns
    # on agent_session are nullable and the verdict ledger is a new table, so a
    # session opened before this revision reads as "unclaimed, unverified" — which
    # is the truth, and which is what makes an unverified claim unable to reach
    # Done rather than merely unlikely to.
    68: _apply_agent_verification,
    # 69: what an agent may touch (HOJA-602-A-14, WP12) — one policy row per
    # agent member, the named secrets it may be given, and the policy version a
    # lease was granted under.
    69: _apply_agent_policy,
    # 70: what a run cost (HOJA-602-A-12, WP14) — the append-only usage ledger.
    # Additive; the budget check (WP06's schedule and WP12's lease gate) reads it
    # and treats an empty ledger as no spend, so an existing workspace's behavior
    # is unchanged until a runner reports a number.
    70: _apply_usage_ledger,
    # 71: the Hoja Connect exposure record (HOJA-602-A-10, WP09) — one row per published port of
    # one `tailcat serve` process, plus the workspace's Connect kill switch. Additive, and it holds
    # no secret: the address stays in the runner's memory (WP09's grep test) and only the pinned
    # peer key is stored. Without it the Connect screen has no server record to read.
    71: _apply_connect_endpoints,
    # 72: which harness an agent's runs execute under (HOJA-602-A-22) — the policy's
    # default profile and the allowlist that bounds it. Additive and nullable, so an
    # existing policy reads as "nobody chose", which the queue resolves to `pi`: the
    # value `agent_schedule.enqueue_admitted` hard-coded before this revision.
    72: _apply_agent_policy_harness,
    # 73: entity links carry the workspace's own host. Data-only: every stored
    # issue/project/profile/comment link was built from the process-wide
    # HOJA_APP_URL, so in a workspace with a vanity host it names the canonical
    # host and opens the wrong workspace. Rewrites those rows in place onto
    # https://<vanity_host>/<url_key>/…, leaves external URLs alone, and is a
    # no-op on a database whose links are already right.
    73: _apply_entity_url_hosts,
    # 74: an SLA policy an admin can configure as they want (HOJA-704). Five
    # additive, nullable columns: cycle_bound / stops_on / business_days_only on
    # the policy, retargeted_at / detached_at on the clock. No table is created,
    # so the RLS classification and the readiness probe do not change.
    74: _apply_sla_options,
    # 75: Pulse comments. A project update is a thing people reply to, and until
    # now the comment table could only point at an issue.
    75: _apply_project_update_comments,
    # 76: the workspace switch for the Agents screens (Settings -> Workspace).
    76: _apply_organization_agents_enabled,
    # 77: inbox notifications say what happened and can be snoozed.
    77: _apply_inbox_notification_detail,
    # 78: follow projects, initiatives and cycles; notifications about any subject.
    78: _apply_follow_subjects,
    79: _apply_remove_meetings,
    # 80: cycle start/end notifications are stamped once, whoever sends them.
    80: _apply_cycle_event_stamps,
    # 81: a refused queued lease records why, so it stops blocking the queue and is
    # audited once per reason instead of once per poll.
    81: _apply_lease_refusal_reason,
    # 82: one timeline per agent run: runner events, runner logs, server notes.
    82: _apply_agent_run_log,
    # 83: a project can name the repository its agent tasks are worked on in.
    83: _apply_project_repository,
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


def migrate(engine: Engine) -> dict | None:
    """Create a fresh schema, apply every outstanding ordered revision, bind RLS.

    Returns the row-level-security summary (``rls_policy.heal``) on PostgreSQL and
    None elsewhere, so the caller can report which tables this run bound.
    """
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
    return bind_rls(engine)


def bind_rls(engine: Engine) -> dict | None:
    """Install RLS on every tenant table, including tables a revision just added.

    A revision that creates a table creates it unbound, and enabling RLS on it
    needs the table's owner; ``infra/scripts/hoja-rls apply`` has always been able
    to do this, but it is an operator command nobody ran *after* a deploy, so a
    shared-tier database accumulated tables with no policy and ``/ready`` refused
    the whole deployment. The migration container holds the owner credential and
    runs before the app that depends on it starts (``docker-compose.yml``), so it
    is the one place in a deployed stack that can close that window for good.

    Idempotent, and it never decides the schema: it reads the same classification
    ``hoja-rls`` reads from the catalog (see ``rls_policy``). A table this role
    does not own is reported, not altered; the readiness gate is what refuses the
    deployment in that case.
    """
    if engine.dialect.name != "postgresql":
        return None
    from sqlalchemy.exc import SQLAlchemyError

    from hoja_server import rls_policy

    try:
        with engine.begin() as conn:
            summary = rls_policy.heal(conn)
    except SQLAlchemyError as exc:
        # Stop the deploy here instead of starting an app that /ready will refuse
        # ninety seconds later: a lock timeout or a missing privilege is
        # retryable, and the previous release is still serving while it is fixed.
        raise RuntimeError(
            "row-level security could not be installed on this database, so the "
            f"app would refuse to become ready: {getattr(exc, 'orig', exc)}"
        ) from exc
    if summary["unresolved"]:
        log.warning(
            "row-level security: %s table(s) have no tenant classification and are "
            "left unbound: %s (classify them in infra/rls/global-tables.txt or give "
            "them an organization_id)", len(summary["unresolved"]), ", ".join(summary["unresolved"]))
    if summary["skipped"]:
        log.warning(
            "row-level security: %s table(s) are not owned by this role, so their "
            "policies were not installed: %s", len(summary["skipped"]), ", ".join(summary["skipped"]))
    return summary


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


def tenant_base_url(vanity_host: str | None) -> str:
    """The origin a workspace is served from: its own host, else the canonical app.

    A hosted workspace (``organization.vanity_host``) answers on its own origin,
    so every link built for it has to name that origin. This is the one place
    that decision is made; ``entity_url`` and the link repair below only consume
    it, so a host change cannot leave two answers in the code.
    """
    host = (vanity_host or "").strip().strip("/")
    return f"https://{host}" if host else app_url()


def org_links(conn, org_id: str) -> tuple[str, str]:
    """The ``(url_key, origin)`` a workspace's links are built from.

    Read in one query on purpose: the key and the host sit on the same row, and a
    link built from one without the other is exactly the bug this prevents.
    """
    row = conn.execute(
        select(organization.c.url_key, organization.c.vanity_host)
        .where(organization.c.id == org_id)
    ).first()
    if not row:
        return "local", app_url()
    return (row[0] or "local"), tenant_base_url(row[1])


def org_base_url(conn, org_id: str) -> str:
    """The origin one workspace's links are built from."""
    return org_links(conn, org_id)[1]


def entity_url(org_url_key: str, kind: str, entity_key: str,
               base_url: str | None = None) -> str:
    """``<origin>/<url_key>/<kind>/<key>``, on the workspace's own origin when known."""
    origin = (base_url or app_url()).rstrip("/")
    return f"{origin}/{org_url_key}/{kind}/{entity_key}"


def _hosted_entity_url(url: str | None, url_key: str, kind: str,
                       base_url: str) -> str | None:
    """``url`` moved onto ``base_url``, or None when it is not ours to rewrite.

    The shape test is the whole safety story of the repair: only a link whose
    path is the one ``entity_url`` writes (``/<url_key>/<kind>/<key>``, with an
    issue comment's ``#comment-…`` fragment surviving) is touched. An
    attachment's object-store link, a connector's external URL or a webhook's
    endpoint has no such path and is returned as ``None``, untouched.
    """
    if not url or not url_key:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    head = f"/{url_key}/{kind}/"
    if not parts.path.startswith(head) or len(parts.path) == len(head):
        return None
    suffix = f"?{parts.query}" if parts.query else ""
    suffix += f"#{parts.fragment}" if parts.fragment else ""
    return f"{base_url}{parts.path}{suffix}"


# Every table that stores a link ``entity_url`` built, with the kind its path
# carries. ``comment`` repeats ``issue`` because a comment's link is its issue's
# link plus a ``#comment-…`` fragment.
ENTITY_URL_TABLES = (
    ("issue", issue, "issue"),
    ("comment", comment, "issue"),
    ("project", project, "project"),
    ("user", user, "profiles"),
)


def repair_entity_urls(conn, org_id: str) -> int:
    """Move one workspace's stored entity links onto the origin it is served from.

    Idempotent: a row already on its host is left alone, so running it twice
    changes nothing. Revision 73 runs it for every hosted workspace, and the
    paths that can move the host under existing rows (a tenant merge, a host pin)
    call it again for the one workspace they changed.
    """
    row = conn.execute(
        select(organization.c.url_key, organization.c.vanity_host)
        .where(organization.c.id == org_id)
    ).first()
    if not row or not row[0]:
        return 0
    url_key, base_url = row[0], tenant_base_url(row[1])
    moved = 0
    for _name, table, kind in ENTITY_URL_TABLES:
        rows = conn.execute(select(table.c.id, table.c.url).where(and_(
            table.c.organization_id == org_id,
            table.c.url.isnot(None),
        ))).all()
        for row_id, url in rows:
            repaired = _hosted_entity_url(url, url_key, kind, base_url)
            if repaired is None or repaired == url:
                continue
            conn.execute(table.update().where(table.c.id == row_id).values(url=repaired))
            moved += 1
    return moved


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
    org_vanity_host: str | None = None,
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
    # A seeded workspace that is served from its own host links to that host, exactly
    # as a workspace created by writer/cli would; without one it keeps HOJA_APP_URL.
    base_url = tenant_base_url(org_vanity_host)
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
                    vanity_host=(org_vanity_host or None),
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
                    url=entity_url(org_url_key, "profiles", uid, base_url),
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
                    conn, org_id, tid, team_key.upper(), uid, default_state_id, org_url_key,
                    base_url,
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


def _seed_demo_issues(conn, org_id, team_id, team_key, uid, state_id, org_url_key,
                      base_url=None):
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
                url=entity_url(org_url_key, "issue", ident, base_url),
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
