"""The admin surface that hands out and takes back runner credentials (HOJA-635).

``runner_api.py`` is the data plane a runner talks to. ``agent_runners.py`` is the
read half a review screen needs. Neither is a place where a person can *mint* the
one-time token that lets a machine in, or disable one that is already in — so
until this module the only way to enroll a runner was to call
``runner_api.mint_enrollment_token`` from Python, which is how the geometry test
has been doing it. HOJA-602-A-6's PR body listed exactly that as an open gap.

Four decisions shape this surface:

* **The plaintext exists once, in one response.** ``runnerEnrollmentCreate``
  returns the token, the enrollment row and the exact command an operator runs on
  the machine. Only the sha256 is stored (``runner_enrollment.token_hash``), so
  the mutation's result cannot be replayed, re-read, or recovered — a client that
  loses it mints another and revokes this one.
* **Nothing here can leak a credential on a read.** ``RunnerEnrollment`` carries
  the enrollment's id, label and lifecycle; it does not carry ``token_hash`` and
  does not carry ``hint`` (the token's last four characters). ``agent_runners.py``
  makes the same omission for ``runner.token_hint`` and for the same reason: a
  fragment of a credential is still a credential.
* **Admin only, refused rather than emptied.** A member gets ``FORBIDDEN``, the
  same gate ``runners`` and ``workspaceAgentSessions`` use. An empty list would
  read as "this workspace has no enrollment tokens", which is a different and
  wrong sentence.
* **Every write is audited after its commit.** ``runner.enrollment.create``,
  ``runner.enrollment.revoke`` and ``runner.revoke`` name the row they changed and
  never its secret; the audit row is written outside the transaction so a rollback
  cannot leave a claim that something happened.

Why the mutations live here and not in ``agent_runners.py``: that module's stated
invariant is that it is read-only, so that a review screen can never disable a
machine. Keeping the write half in a separate module keeps that sentence true and
puts the one authority for "who may enroll" in one file.
"""
from __future__ import annotations

from datetime import datetime

from ariadne import MutationType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import desc, select

from hoja_server import audit, runner_api, runner_release, tenant_context
from hoja_server.db import runner_enrollment

query = QueryType()
mutation = MutationType()

# Bounded, like every other admin page: an operator enrolls machines a handful at a
# time, and a list that could be unbounded is a list a screen cannot draw.
MAX_PAGE = 200

# The default lifetime of an enrollment token, in minutes. A day: long enough to
# reach a machine and run one command, short enough that a token left in a ticket
# is worthless tomorrow. ``ttlMinutes: 0`` means "no expiry", which the mutation
# allows because an air-gapped install may only be visited once.
DEFAULT_TTL_MINUTES = 24 * 60
MAX_TTL_MINUTES = 365 * 24 * 60

PENDING = "pending"
SPENT = "spent"
EXPIRED = "expired"

#: Where ``--enroll`` keeps the runner's identity, and where the daemon reads it back.
#: One constant because the enroll and start commands must agree on it: a start that
#: looked in a different directory would find no credential and dial the control plane
#: anonymously.
STATE_DIR = "~/.hoja/runner"

SDL = gql(
    """
    "One single-use registration token, as an admin screen reads it: never the secret."
    type RunnerEnrollment {
      id: ID!
      "The workspace the row belongs to: the request's own tenant."
      organizationId: ID!
      "What an admin called it when it was minted: usually the machine it is for."
      label: String
      "pending (usable), spent (a runner registered with it), expired (aged out or revoked)."
      status: String!
      "True while the token would still be accepted at /runner/enroll."
      usable: Boolean!
      "When the token stops being accepted; null when it never expires."
      expiresAt: DateTime
      "When a runner registered with it; null while it is unspent."
      usedAt: DateTime
      "The runner that spent it, once one has."
      usedByRunner: String
      createdAt: DateTime!
    }

    "The one and only time a token is readable."
    type RunnerEnrollmentCreated {
      enrollment: RunnerEnrollment!
      "The plaintext token. This response is the only place it exists; only its sha256 is stored."
      token: String!
      "The exact command an operator runs on the machine, with this token and this workspace's URL."
      command: String!
      "The command that installs the daemon from this deployment's own wheel (/downloads). Null when this deployment publishes no wheel: a URL that 404s is worse than saying so."
      installCommand: String
      "One line that installs, connects and starts a runner on a machine (null when this deployment publishes no runner)."
      connectCommand: String
      "The exact command that starts the daemon after enrolling, with this workspace's URL."
      startCommand: String!
      "Where this deployment serves the wheel; null when it serves none."
      downloadUrl: String
      "The cadence the server asks the daemon to heartbeat at (WP07 gap 5)."
      heartbeatIntervalS: Int!
    }

    extend type Query {
      "Every enrollment token in the workspace, newest first, admin only. Never the secret."
      runnerEnrollments(first: Int = 50): [RunnerEnrollment!]!
    }

    extend type Mutation {
      "Mint a single-use enrollment token. The plaintext is returned by this call only."
      runnerEnrollmentCreate(name: String!, ttlMinutes: Int,
        "Where agents should work on the machine; baked into connectCommand. Default ~/hoja."
        workRoot: String): RunnerEnrollmentCreated!
      "Withdraw an unspent enrollment token. False when this workspace has no such row."
      runnerEnrollmentRevoke(id: String!): Boolean!
      "Disable an enrolled runner and every token it holds. False when it was already revoked."
      runnerRevoke(id: String!): Boolean!
    }
    """
)


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org_id: str, user_id: str) -> None:
    """The ``runners`` gate, for the same reason: a refusal, not an empty result.

    Minting a token is the act of granting a machine access, so the person doing it
    has to be the person who may read the machine list.
    """
    actor = store.get_user(org_id, user_id)
    if not actor or not actor.get("admin"):
        raise GraphQLError("Only workspace admins can enroll or revoke runners.",
                           extensions={"code": "FORBIDDEN"})


def _bad(field: str, message: str) -> None:
    raise GraphQLError(message, extensions={"code": "BAD_USER_INPUT", "field": field})


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def enrollment_status(row: dict, *, now: datetime | None = None) -> str:
    """``spent`` / ``expired`` / ``pending``, in that order of precedence.

    Spent wins over expired because it is the fact that matters: the token did its
    job. Expired covers both a token that aged out and one an admin withdrew —
    ``runner_enrollment`` has no ``revoked_at`` column and HOJA-635 forbids a
    migration, so a revocation is an expiry moved to the server's now
    (``runner_api.revoke_enrollment``). Which of the two it was is recorded in the
    audit ledger, where the ``runner.enrollment.revoke`` row names the enrollment.
    """
    if row.get("used_at"):
        return SPENT
    expires = _parse_iso(row.get("expires_at"))
    if expires is None:
        return PENDING
    now = now or datetime.now(expires.tzinfo)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=now.tzinfo)
    return EXPIRED if expires <= now else PENDING


def serialize_enrollment(row: dict, *, now: datetime | None = None) -> dict:
    status = enrollment_status(row, now=now)
    return {
        "id": row["id"],
        "organizationId": row["organization_id"],
        "label": row.get("label"),
        "status": status,
        "usable": status == PENDING,
        "expiresAt": row.get("expires_at"),
        "usedAt": row.get("used_at"),
        "usedByRunner": row.get("used_by_runner"),
        "createdAt": row["created_at"],
        # Deliberately absent: ``token_hash`` (the credential) and ``hint`` (four
        # characters of its plaintext). The SDL cannot select what it does not name.
        "_row": row,
    }


def _page_size(first) -> int:
    return max(1, min(int(first or 50), MAX_PAGE))


def read_enrollments(conn, org_id: str, *, first=50, now: datetime | None = None) -> list[dict]:
    """One bounded page of the workspace's enrollment tokens, newest first."""
    rows = conn.execute(
        select(runner_enrollment)
        .where(runner_enrollment.c.organization_id == org_id)
        .order_by(desc(runner_enrollment.c.created_at), desc(runner_enrollment.c.id))
        .limit(_page_size(first))
    ).mappings().all()
    return [serialize_enrollment(dict(row), now=now) for row in rows]


def enroll_command(base_url: str, *, name: str, token: str) -> str:
    """The exact line an operator runs on the machine.

    It is a string the server builds so that the screen and the daemon cannot drift
    apart: the flag spellings here are ``hoja_runnerd.main``'s own. The token goes
    in through the environment rather than ``argv`` on purpose — ``ps`` shows every
    command line on a shared host, and this value is a credential.

    It registers and exits (HOJA-712). ``start_command`` below is the line that keeps
    the machine working, and the screen shows both: an operator who runs only this one
    believes the runner is up while the token is already spent and nothing is polling.
    """
    return (
        f"HOJA_ENROLLMENT_TOKEN={token} hoja-runnerd --enroll "
        f"--base-url {base_url} --name {name} --state-dir {STATE_DIR}"
    )


def start_command(base_url: str) -> str:
    """The exact line that starts the daemon, once, on the enrolled machine.

    No harness flag, and that is the point of HOJA-602-A-20: the default harness is
    whatever the control plane named at enroll (first entry of the allowlist that call
    registered, sorted) and ``--enroll`` wrote into the identity file, so a start that
    names the same ``--state-dir`` resolves it without inventing one. Passing
    ``--default-harness`` here would be a second, silently diverging answer.

    No credential either: ``--enroll`` stored the runner's own token in that state
    directory and ``main`` reads it from there (HOJA-635).
    """
    return f"hoja-runnerd --base-url {base_url} --state-dir {STATE_DIR}"


def _base_url(info) -> str:
    """This deployment's URL, as the browser reached it.

    Taken from the request rather than from configuration, because this mutation is
    served by the same host the operator is looking at: a hard-coded default would
    print ``app.hoja.work`` to a self-hosted install. ``X-Forwarded-Proto`` is
    honoured because the app runs behind a proxy that terminates TLS.
    """
    context = getattr(info, "context", None) or {}
    request = context.get("request") if isinstance(context, dict) else None
    if request is None:
        return "https://app.hoja.work"
    headers = getattr(request, "headers", {}) or {}
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").strip()
    if not host:
        return "https://app.hoja.work"
    scheme = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if scheme not in ("http", "https"):
        scheme = getattr(getattr(request, "url", None), "scheme", "https") or "https"
    return f"{scheme}://{host}"


@query.field("runnerEnrollments")
def r_runner_enrollments(_obj, info, first=None):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    # ``scoped`` rather than the ambient request context: row-level security then
    # binds to the tenant this resolver just authorized, whatever the caller's
    # context said. The explicit predicate below is the second belt.
    with tenant_context.scoped(org), store.engine.connect() as conn:
        return read_enrollments(conn, org, first=first or 50)


@mutation.field("runnerEnrollmentCreate")
def m_runner_enrollment_create(_obj, info, name, ttlMinutes=None,  # noqa: N803 - GraphQL names
                               workRoot=None):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    try:
        work_root = runner_release.clean_work_root(workRoot)
    except ValueError as exc:
        raise GraphQLError(str(exc), extensions={"code": "BAD_USER_INPUT", "field": "workRoot"}) from exc
    label = (name or "").strip()
    if not label:
        _bad("name", "name must not be empty")
    if len(label) > 120:
        _bad("name", "name must be at most 120 characters")
    if ttlMinutes is None:
        minutes = DEFAULT_TTL_MINUTES
    else:
        try:
            minutes = int(ttlMinutes)
        except (TypeError, ValueError):
            _bad("ttlMinutes", "ttlMinutes must be an integer")
        if minutes < 0:
            _bad("ttlMinutes", "ttlMinutes must not be negative")
        if minutes > MAX_TTL_MINUTES:
            _bad("ttlMinutes", f"ttlMinutes must be at most {MAX_TTL_MINUTES}")
    row, token = runner_api.mint_enrollment_token(
        store.engine,
        organization_id=org,
        created_by=uid,
        label=label,
        ttl_s=minutes * 60,
    )
    # After the commit, never inside it (WP05's rule, restated in agent_policy.py).
    # The row names the enrollment; the token is not in this detail and cannot be.
    audit.record_request(
        store, info, user_id=uid, action="runner.enrollment.create",
        target_kind="runner_enrollment", target_id=row["id"],
        detail={"label": label, "ttl_minutes": minutes,
                "expires_at": row.get("expires_at")},
    )
    base_url = _base_url(info)
    # The artifact this deployment publishes, if it built one (HOJA-712). Read at
    # request time rather than at import: the wheel lands in the image after the
    # modules are first imported, and a test can point HOJA_DOWNLOAD_ROOT at a fixture.
    download = runner_release.download_url(base_url)
    return {
        "enrollment": serialize_enrollment(row),
        "token": token,
        # Kept for compatibility: the enroll half of the same response, unchanged.
        "command": enroll_command(base_url, name=label, token=token),
        "installCommand": runner_release.install_command(download) if download else None,
        "connectCommand": (runner_release.connect_command(base_url, token, work_root=work_root)
                           if download else None),
        "startCommand": start_command(base_url),
        "downloadUrl": download,
        "heartbeatIntervalS": runner_api.HEARTBEAT_INTERVAL_S,
    }


@mutation.field("runnerEnrollmentRevoke")
def m_runner_enrollment_revoke(_obj, info, id):  # noqa: A002 - GraphQL argument name
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    row = runner_api.revoke_enrollment(store.engine, organization_id=org, enrollment_id=str(id))
    if row is None:
        # Not a silent False: "no such token" and "already spent" are different
        # answers, and the screen states which one it got.
        _bad("id", "No enrollment token with that id in this workspace.")
    if row.get("revoked"):
        audit.record_request(
            store, info, user_id=uid, action="runner.enrollment.revoke",
            target_kind="runner_enrollment", target_id=row["id"],
            detail={"label": row.get("label"), "expires_at": row.get("expires_at")},
        )
    return bool(row.get("revoked"))


@mutation.field("runnerRevoke")
def m_runner_revoke(_obj, info, id):  # noqa: A002 - GraphQL argument name
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    revoked = runner_api.revoke_runner(store.engine, organization_id=org, runner_id=str(id))
    if not revoked:
        # Either the runner is not in this workspace, or its token was already
        # turned off. Both are "nothing happened"; the message says so rather than
        # letting a screen report a revocation that did not occur.
        _bad("id", "No live runner with that id in this workspace.")
    audit.record_request(
        store, info, user_id=uid, action="runner.revoke",
        target_kind="runner", target_id=str(id), detail={},
    )
    return True
