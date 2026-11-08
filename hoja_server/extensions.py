"""Extensions: install anything that speaks HTTPS as a set of revertible registrations.

Design follows Shi, Zhang & Cui, "A Programming Paradigm for Spatiotemporal
Composability" (arXiv:2608.25512), the model behind Cordis / DeepSeek Harness:

- Temporal composability. Every registration an install performs (bot user, API
  key, team seat, webhook, hook, action) is written to ``extension_effect`` with
  the kind that names its inverse. Uninstall replays the ledger last-in-first-out
  inside one transaction, so removal reverts exactly what install did.
- Spatial composability. The manifest declares what the extension reads
  (``access``) and what it provides (``events``, ``hooks``, ``actions``), each
  keyed. An update reconciles old and new manifests by key in one transaction:
  the resulting registrations depend only on the final manifest, and a bad
  manifest leaves the previous version in place.
- Event modes. ``events`` are asynchronous notifications (the webhook pipeline).
  ``hooks`` are synchronous interceptors on issue writes, chained in order: each
  sees the previous one's patch and may patch allowlisted fields or veto.

No extension code ever runs inside Hoja. Hoja makes Linear-signed HTTPS calls to
the extension and the extension calls back with its own bot credential.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from graphql import GraphQLInputObjectType, Undefined
from graphql.utilities import coerce_input_value, validate_input_value
from sqlalchemy import and_, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from hoja_server import secrets_box, tenant_context, webhooks
from hoja_server.db import (
    api_key,
    extension,
    extension_action,
    extension_action_run,
    extension_effect,
    extension_failure,
    extension_hook,
    gen_token,
    issue,
    issue_activity,
    new_id,
    now_iso,
    team,
    team_member,
    token_hash,
    user,
    webhook,
)

log = logging.getLogger("hoja_server.extensions")

HOOK_EVENTS = ("issue.beforeCreate", "issue.beforeUpdate")
ACTION_ENTITIES = ("Issue",)
ACCESS = ("read", "read_write")
FAILURE_POLICIES = ("allow", "block")
# Fields a hook may set. Never teamId, parentId or creator: those change ownership.
PATCHABLE = ("title", "description", "priority", "estimate", "stateId", "assigneeId",
             "labelIds", "dueDate", "projectId", "cycleId")
HOOK_BUDGET_SECONDS = 3.0
HOOK_TIMEOUT_SECONDS = 2.0
ACTION_TIMEOUT_SECONDS = 8.0
BREAKER_THRESHOLD = 5
BREAKER_OPEN_SECONDS = 60.0
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_MANIFEST_FIELDS = {"key", "name", "version", "url", "access", "events", "hooks", "actions", "description"}
_LIMITS = {"events": 10, "hooks": 10, "actions": 25}


class ExtensionError(ValueError):
    """A manifest or request the caller can fix; ``field`` is a manifest path."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


class ExtensionRejectedError(Exception):
    """A hook vetoed the write (or failed with failurePolicy=block)."""

    def __init__(self, extension_name: str, message: str):
        super().__init__(f"{extension_name}: {message}")
        self.extension_name = extension_name
        self.reason = message


# ------------------------------------------------------------------ manifest


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ExtensionError(f"{field} must be a non-empty string of at most {limit} characters.", field)
    return value.strip()


def _key(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _KEY.match(value):
        raise ExtensionError(f"{field} must match {_KEY.pattern}.", field)
    return value


def _path(value: Any, field: str) -> str:
    if (not isinstance(value, str) or not value.startswith("/") or value.startswith("//")
            or ".." in value.split("/") or any(c in value for c in "\\#?@ ") or len(value) > 200):
        raise ExtensionError(f"{field} must be a path like /hooks/name on the extension URL.", field)
    return value


def _items(manifest: dict, name: str) -> list[dict]:
    items = manifest.get(name) or []
    if not isinstance(items, list) or len(items) > _LIMITS[name]:
        raise ExtensionError(f"{name} must be a list of at most {_LIMITS[name]} entries.", name)
    seen = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ExtensionError(f"{name}[{i}] must be an object.", f"{name}[{i}]")
        k = _key(item.get("key"), f"{name}[{i}].key")
        if k in seen:
            raise ExtensionError(f"Duplicate key '{k}' in {name}.", f"{name}[{i}].key")
        seen.add(k)
    return items


def parse_manifest(raw: Any, *, resolve=None) -> dict:
    """Validate and normalize a manifest. Resolves the URL (network): call outside transactions."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExtensionError(f"Manifest is not valid JSON: {exc.msg}.", "manifest") from exc
    if not isinstance(raw, dict):
        raise ExtensionError("Manifest must be a JSON object.", "manifest")
    unknown = sorted(set(raw) - _MANIFEST_FIELDS)
    if unknown:
        raise ExtensionError(f"Unknown manifest fields: {unknown}.", unknown[0])
    url = _text(raw.get("url"), "url", 500).rstrip("/")
    parts = urlsplit(url)
    if parts.query or parts.fragment:
        raise ExtensionError("url must not contain a query or fragment.", "url")
    try:
        webhooks.check_target(url, resolve=resolve)
    except webhooks.WebhookError as exc:
        raise ExtensionError(str(exc), "url") from exc
    access = raw.get("access", "read")
    if access not in ACCESS:
        raise ExtensionError(f"access must be one of {list(ACCESS)}.", "access")
    out = {
        "key": _key(raw.get("key"), "key"),
        "name": _text(raw.get("name"), "name", 80),
        "version": _text(raw.get("version"), "version", 40),
        "description": raw.get("description") if isinstance(raw.get("description"), str) else None,
        "url": url, "access": access, "events": [], "hooks": [], "actions": [],
    }
    for i, ev in enumerate(_items(raw, "events")):
        team_id, all_public = ev.get("teamId"), bool(ev.get("allPublicTeams"))
        if bool(team_id) == all_public:
            raise ExtensionError("Provide exactly one of teamId or allPublicTeams: true.", f"events[{i}].teamId")
        out["events"].append({
            "key": ev["key"], "path": _path(ev.get("path", "/events"), f"events[{i}].path"),
            "resourceTypes": ev.get("resourceTypes") or ["Issue"],
            "teamId": team_id, "allPublicTeams": all_public,
        })
    for i, hook in enumerate(_items(raw, "hooks")):
        if hook.get("on") not in HOOK_EVENTS:
            raise ExtensionError(f"hooks[{i}].on must be one of {list(HOOK_EVENTS)}.", f"hooks[{i}].on")
        policy = hook.get("failurePolicy", "allow")
        if policy not in FAILURE_POLICIES:
            raise ExtensionError(f"hooks[{i}].failurePolicy must be one of {list(FAILURE_POLICIES)}.",
                                 f"hooks[{i}].failurePolicy")
        out["hooks"].append({"key": hook["key"], "on": hook["on"], "failurePolicy": policy,
                             "path": _path(hook.get("path"), f"hooks[{i}].path"), "position": i})
    for i, action in enumerate(_items(raw, "actions")):
        if action.get("entity", "Issue") not in ACTION_ENTITIES:
            raise ExtensionError(f"actions[{i}].entity must be one of {list(ACTION_ENTITIES)}.",
                                 f"actions[{i}].entity")
        out["actions"].append({"key": action["key"], "entity": action.get("entity", "Issue"),
                               "title": _text(action.get("title"), f"actions[{i}].title", 80),
                               "path": _path(action.get("path"), f"actions[{i}].path")})
    return out


# ------------------------------------------------------------- effect ledger


def _record(conn: Connection, org_id: str, ext_id: str, kind: str, ref: str) -> None:
    conn.execute(extension_effect.insert().values(
        organization_id=org_id, extension_id=ext_id, kind=kind, ref=ref, created_at=now_iso()))


def _revert(conn: Connection, org_id: str, kind: str, ref: str) -> None:
    """The inverse of one recorded registration."""
    ts = now_iso()
    if kind == "bot_user":  # deactivated, never deleted: it may have authored issues
        conn.execute(user.update().where(and_(user.c.id == ref, user.c.organization_id == org_id))
                     .values(active=False, archived_at=ts, updated_at=ts))
    elif kind == "api_key":
        conn.execute(api_key.update().where(and_(api_key.c.id == ref, api_key.c.organization_id == org_id,
                                                 api_key.c.revoked_at.is_(None))).values(revoked_at=ts))
    elif kind == "team_seat":
        team_id, user_id = ref.split(":", 1)
        conn.execute(team_member.delete().where(and_(team_member.c.team_id == team_id,
                                                     team_member.c.user_id == user_id)))
    elif kind == "webhook":
        webhooks.remove_webhook(conn, org_id, ref)
    elif kind == "hook":
        conn.execute(extension_hook.delete().where(extension_hook.c.id == ref))
    elif kind == "action":
        conn.execute(extension_action.delete().where(extension_action.c.id == ref))
    else:
        raise RuntimeError(f"Unknown extension effect kind: {kind}")


def _effects(conn: Connection, ext_id: str, kind: str | None = None) -> list[dict]:
    stmt = select(extension_effect).where(extension_effect.c.extension_id == ext_id)
    if kind:
        stmt = stmt.where(extension_effect.c.kind == kind)
    return [dict(r) for r in conn.execute(stmt.order_by(extension_effect.c.id)).mappings()]


def _drop(conn: Connection, org_id: str, effect: dict) -> None:
    _revert(conn, org_id, effect["kind"], effect["ref"])
    conn.execute(extension_effect.delete().where(extension_effect.c.id == effect["id"]))


# ------------------------------------------------------------------ install


def _private_team_ids(conn: Connection, org_id: str, events: list[dict]) -> set[str]:
    ids = set()
    for i, ev in enumerate(events):
        if ev["teamId"]:
            try:
                tid = webhooks.resolve_team(conn, org_id, ev["teamId"], f"events[{i}].teamId")
            except webhooks.WebhookError as exc:
                raise ExtensionError(str(exc), exc.field) from exc
            ev["_team"] = tid
            if conn.execute(select(team.c.visibility).where(team.c.id == tid)).scalar() != "public":
                ids.add(tid)
    return ids


def _stored(manifest: dict) -> str:
    """The manifest as the admin wrote it (without server-resolved helper keys)."""
    clean = dict(manifest, events=[{k: v for k, v in ev.items() if not k.startswith("_")}
                                   for ev in manifest["events"]])
    return json.dumps(clean)


def _event_input(manifest: dict, ev: dict) -> dict:
    return {"url": manifest["url"] + ev["path"], "resourceTypes": ev["resourceTypes"],
            "teamId": ev.get("_team") or ev["teamId"], "allPublicTeams": ev["allPublicTeams"],
            "label": f"extension:{manifest['key']}:{ev['key']}"}


def _add_event(conn, org_id, ext_id, bot_id, manifest, ev, sealed, i):
    try:
        wid = webhooks.insert_webhook(conn, org_id, bot_id, _event_input(manifest, ev), sealed_secret=sealed,
                                      extension_id=ext_id, event_key=ev["key"], field_prefix=f"events[{i}].")
    except webhooks.WebhookError as exc:
        raise ExtensionError(str(exc), exc.field) from exc
    _record(conn, org_id, ext_id, "webhook", wid)


def _add_hook(conn, org_id, ext_id, hook):
    hid = new_id()
    conn.execute(extension_hook.insert().values(
        id=hid, organization_id=org_id, extension_id=ext_id, key=hook["key"], on_event=hook["on"],
        path=hook["path"], failure_policy=hook["failurePolicy"], position=hook["position"]))
    _record(conn, org_id, ext_id, "hook", hid)


def _add_action(conn, org_id, ext_id, action):
    aid = new_id()
    conn.execute(extension_action.insert().values(
        id=aid, organization_id=org_id, extension_id=ext_id, key=action["key"], title=action["title"],
        entity=action["entity"], path=action["path"]))
    _record(conn, org_id, ext_id, "action", aid)


def _add_seat(conn, org_id, ext_id, team_id, bot_id):
    if not conn.execute(select(team_member.c.team_id).where(and_(
            team_member.c.team_id == team_id, team_member.c.user_id == bot_id))).first():
        conn.execute(team_member.insert().values(team_id=team_id, user_id=bot_id, role="member"))
        _record(conn, org_id, ext_id, "team_seat", f"{team_id}:{bot_id}")


def install(engine: Engine, org_id: str, installer_id: str, raw_manifest: Any) -> dict:
    """Install in one transaction. Returns the extension plus the one-time credentials."""
    manifest = parse_manifest(raw_manifest)  # network (DNS) happens here, before any write
    secret = "hoja_ext_" + secrets.token_hex(24)
    sealed = secrets_box.encrypt_secret(secret)
    raw_token = gen_token()
    ts = now_iso()
    ext_id, bot_id, key_id = new_id(), new_id(), new_id()
    try:
        _install_rows(engine, org_id, installer_id, manifest, sealed, raw_token, ts, ext_id, bot_id, key_id)
    except IntegrityError as exc:  # a concurrent install of the same key won the unique index
        raise ExtensionError(f"An extension with key '{manifest['key']}' is already installed.", "key") from exc
    return {"extension": get(engine, org_id, ext_id), "apiKey": raw_token, "signingSecret": secret}


def _install_rows(engine, org_id, installer_id, manifest, sealed, raw_token, ts, ext_id, bot_id, key_id):
    with engine.begin() as conn:
        if conn.execute(select(extension.c.id).where(and_(
                extension.c.organization_id == org_id, extension.c.key == manifest["key"],
                extension.c.uninstalled_at.is_(None)))).first():
            raise ExtensionError(f"An extension with key '{manifest['key']}' is already installed.", "key")
        private_teams = _private_team_ids(conn, org_id, manifest["events"])
        # A bot has no global identity, membership or password: it cannot sign in,
        # only use the key below. It is never an admin.
        conn.execute(user.insert().values(
            id=bot_id, organization_id=org_id, name=manifest["name"], display_name=manifest["name"],
            email=None, active=True, admin=False, kind="bot", created_at=ts, updated_at=ts))
        conn.execute(extension.insert().values(
            id=ext_id, organization_id=org_id, key=manifest["key"], name=manifest["name"],
            version=manifest["version"], url=manifest["url"], access=manifest["access"],
            manifest=_stored(manifest), secret_sealed=sealed, bot_user_id=bot_id,
            installed_by=installer_id, created_at=ts, updated_at=ts))
        _record(conn, org_id, ext_id, "bot_user", bot_id)
        conn.execute(api_key.insert().values(
            id=key_id, token_hash=token_hash(raw_token), label=f"extension:{manifest['key']}",
            hint=raw_token[-4:], access=manifest["access"], user_id=bot_id,
            organization_id=org_id, created_at=ts))
        _record(conn, org_id, ext_id, "api_key", key_id)
        for team_id in sorted(private_teams):
            _add_seat(conn, org_id, ext_id, team_id, bot_id)
        for i, ev in enumerate(manifest["events"]):
            _add_event(conn, org_id, ext_id, bot_id, manifest, ev, sealed, i)
        for hook in manifest["hooks"]:
            _add_hook(conn, org_id, ext_id, hook)
        for action in manifest["actions"]:
            _add_action(conn, org_id, ext_id, action)


def uninstall(engine: Engine, org_id: str, ext_id: str) -> bool:
    """Replay every recorded inverse, newest first, in one transaction."""
    with engine.begin() as conn:
        row = conn.execute(select(extension.c.id).where(and_(
            extension.c.id == ext_id, extension.c.organization_id == org_id,
            extension.c.uninstalled_at.is_(None)))).first()
        if not row:
            return False
        for effect in reversed(_effects(conn, ext_id)):
            _drop(conn, org_id, effect)
        conn.execute(extension.update().where(extension.c.id == ext_id).values(
            uninstalled_at=now_iso(), updated_at=now_iso()))
    _BREAKER.forget(ext_id)
    return True


def update(engine: Engine, org_id: str, ext_id: str, raw_manifest: Any) -> dict | None:
    """Reconcile registrations by key against a new manifest, all-or-nothing."""
    manifest = parse_manifest(raw_manifest)
    with engine.begin() as conn:
        row = conn.execute(select(extension).where(and_(
            extension.c.id == ext_id, extension.c.organization_id == org_id,
            extension.c.uninstalled_at.is_(None)))).mappings().first()
        if not row:
            return None
        if manifest["key"] != row["key"]:
            raise ExtensionError("key cannot change; install a new extension instead.", "key")
        bot_id, sealed = row["bot_user_id"], row["secret_sealed"]
        private_teams = _private_team_ids(conn, org_id, manifest["events"])

        # Access: the existing key keeps working with the new scope.
        for effect in _effects(conn, ext_id, "api_key"):
            conn.execute(api_key.update().where(api_key.c.id == effect["ref"]).values(access=manifest["access"]))
        # Team seats.
        seats = {e["ref"].split(":", 1)[0]: e for e in _effects(conn, ext_id, "team_seat")}
        for team_id, effect in seats.items():
            if team_id not in private_teams:
                _drop(conn, org_id, effect)
        for team_id in sorted(private_teams - set(seats)):
            _add_seat(conn, org_id, ext_id, team_id, bot_id)
        # Events, keyed by event_key; changed ones are updated in place so queued
        # deliveries survive the upgrade.
        current = {}
        for effect in _effects(conn, ext_id, "webhook"):
            event_key = conn.execute(select(webhook.c.event_key).where(webhook.c.id == effect["ref"])).scalar()
            current[event_key] = effect
        wanted = {ev["key"]: (i, ev) for i, ev in enumerate(manifest["events"])}
        for event_key, effect in current.items():
            if event_key not in wanted:
                _drop(conn, org_id, effect)
        for event_key, (i, ev) in wanted.items():
            if event_key in current:
                inp = _event_input(manifest, ev)
                try:
                    types = webhooks._resource_types(inp["resourceTypes"], f"events[{i}].resourceTypes")
                except webhooks.WebhookError as exc:
                    raise ExtensionError(str(exc), exc.field) from exc
                conn.execute(webhook.update().where(webhook.c.id == current[event_key]["ref"]).values(
                    url=inp["url"], resource_types=json.dumps(types), team_id=inp["teamId"],
                    all_public_teams=inp["allPublicTeams"], label=inp["label"], updated_at=now_iso()))
            else:
                _add_event(conn, org_id, ext_id, bot_id, manifest, ev, sealed, i)
        # Hooks and actions, keyed; rows keep their ids when only details change.
        for table, kind, items, columns in (
            (extension_hook, "hook", manifest["hooks"],
             lambda h: {"on_event": h["on"], "path": h["path"], "failure_policy": h["failurePolicy"],
                        "position": h["position"]}),
            (extension_action, "action", manifest["actions"],
             lambda a: {"title": a["title"], "entity": a["entity"], "path": a["path"]}),
        ):
            existing = {}
            for effect in _effects(conn, ext_id, kind):
                existing[conn.execute(select(table.c.key).where(table.c.id == effect["ref"])).scalar()] = effect
            wanted_items = {item["key"]: item for item in items}
            for item_key, effect in existing.items():
                if item_key not in wanted_items:
                    _drop(conn, org_id, effect)
            for item_key, item in wanted_items.items():
                if item_key in existing:
                    conn.execute(table.update().where(table.c.id == existing[item_key]["ref"]).values(**columns(item)))
                elif kind == "hook":
                    _add_hook(conn, org_id, ext_id, item)
                else:
                    _add_action(conn, org_id, ext_id, item)
        conn.execute(user.update().where(user.c.id == bot_id).values(
            name=manifest["name"], display_name=manifest["name"], updated_at=now_iso()))
        conn.execute(extension.update().where(extension.c.id == ext_id).values(
            name=manifest["name"], version=manifest["version"], url=manifest["url"],
            access=manifest["access"], manifest=_stored(manifest), updated_at=now_iso()))
    _BREAKER.forget(ext_id)
    return get(engine, org_id, ext_id)


# -------------------------------------------------------------------- reads


def _ser(conn: Connection, row: dict) -> dict:
    hooks = conn.execute(select(extension_hook).where(extension_hook.c.extension_id == row["id"])
                         .order_by(extension_hook.c.position)).mappings().all()
    actions = conn.execute(select(extension_action).where(extension_action.c.extension_id == row["id"])
                           .order_by(extension_action.c.key)).mappings().all()
    failures = conn.execute(select(extension_failure).where(extension_failure.c.extension_id == row["id"])
                            .order_by(extension_failure.c.created_at.desc()).limit(20)).mappings().all()
    return {
        "id": row["id"], "key": row["key"], "name": row["name"], "version": row["version"],
        "url": row["url"], "access": row["access"], "enabled": row["uninstalled_at"] is None,
        "manifest": json.loads(row["manifest"]), "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        "_bot_user_id": row["bot_user_id"],
        "hooks": [{"key": h["key"], "on": h["on_event"], "failurePolicy": h["failure_policy"],
                   "position": h["position"]} for h in hooks],
        "actions": [{"id": a["id"], "key": a["key"], "title": a["title"], "entity": a["entity"],
                     "extensionId": row["id"], "extensionName": row["name"]} for a in actions],
        "failures": [{"at": f["created_at"], "kind": f["kind"], "key": f["key"],
                      "httpStatus": f["http_status"], "message": f["message"]} for f in failures],
    }


def get(engine: Engine, org_id: str, ext_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(select(extension).where(and_(
            extension.c.id == ext_id, extension.c.organization_id == org_id,
            extension.c.uninstalled_at.is_(None)))).mappings().first()
        return _ser(conn, dict(row)) if row else None


def list_installed(engine: Engine, org_id: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(extension).where(and_(
            extension.c.organization_id == org_id, extension.c.uninstalled_at.is_(None),
        )).order_by(extension.c.created_at, extension.c.id)).mappings().all()
        return [_ser(conn, dict(r)) for r in rows]


def list_actions(engine: Engine, org_id: str, entity: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(extension_action, extension.c.name.label("extension_name")).join(
            extension, extension.c.id == extension_action.c.extension_id).where(and_(
                extension_action.c.organization_id == org_id, extension_action.c.entity == entity,
                extension.c.uninstalled_at.is_(None),
            )).order_by(extension.c.name, extension_action.c.title)).mappings().all()
        return [{"id": r["id"], "key": r["key"], "title": r["title"], "entity": r["entity"],
                 "extensionId": r["extension_id"], "extensionName": r["extension_name"]} for r in rows]


# ------------------------------------------------------------ outbound calls


class _Breaker:
    """Per-process circuit breaker so a dead hook cannot stall every write."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float]] = {}

    def is_open(self, hook_id: str) -> bool:
        with self._lock:
            _failures, until = self._state.get(hook_id, (0, 0.0))
            return until > time.monotonic()

    def record(self, hook_id: str, ok: bool) -> None:
        with self._lock:
            if ok:
                self._state.pop(hook_id, None)
                return
            failures = self._state.get(hook_id, (0, 0.0))[0] + 1
            until = time.monotonic() + BREAKER_OPEN_SECONDS if failures >= BREAKER_THRESHOLD else 0.0
            self._state[hook_id] = (0 if until else failures, until)

    def forget(self, prefix: str) -> None:
        with self._lock:
            for hook_id in [k for k in self._state if k.startswith(prefix + ":")]:
                self._state.pop(hook_id)


_BREAKER = _Breaker()


# Extension columns joined onto hook/action rows, labelled to avoid id/key clashes.
_EXT_COLUMNS = (extension.c.name, extension.c.url, extension.c.secret_sealed,
                extension.c.bot_user_id, extension.c.created_at.label("extension_created_at"))


def _call(row: dict, path: str, payload: dict, event: str, timeout: float) -> tuple[int, Any]:
    url = row["url"] + path
    webhooks.check_target(url)
    body, headers = webhooks.build_request(secrets_box.decrypt_secret(row["secret_sealed"]), payload,
                                           new_id(), event)
    status, raw = webhooks.post_capped(url, body, headers, timeout=timeout)
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = None
    return status, parsed


def _record_failure(engine: Engine, org_id: str, ext_id: str, kind: str, key: str,
                    status: int | None, message: str) -> None:
    with tenant_context.system(), engine.begin() as conn:
        conn.execute(extension_failure.insert().values(
            id=new_id(), organization_id=org_id, extension_id=ext_id, kind=kind, key=key,
            http_status=status, message=message[:500], created_at=now_iso()))


def _actor(engine: Engine, org_id: str, actor_id: str | None) -> dict | None:
    if not actor_id:
        return None
    with engine.connect() as conn:
        who = conn.execute(select(user.c.id, user.c.name, user.c.email, user.c.kind).where(and_(
            user.c.id == actor_id, user.c.organization_id == org_id))).mappings().first()
    if not who:
        return None
    return {"id": who["id"], "type": "integration" if who["kind"] == "bot" else "user",
            "name": who["name"], "email": who["email"] if who["kind"] != "bot" else None}


def issue_data(engine: Engine, org_id: str, issue_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(select(issue).where(and_(
            issue.c.organization_id == org_id, issue.c.id == issue_id))).mappings().first()
        return webhooks._issue_data(conn, dict(row)) if row else None


def _typed_patch(patch: Any, input_type: GraphQLInputObjectType | None) -> tuple[dict, str | None]:
    """Coerce a hook patch exactly as GraphQL coerces client input; return (patch, error)."""
    if not isinstance(patch, dict):
        return {}, "patch must be an object"
    bad = sorted(set(patch) - set(PATCHABLE))
    if bad:
        return {}, f"patch may only set {list(PATCHABLE)}; got {bad}"
    if "title" in patch and (not isinstance(patch["title"], str) or not patch["title"].strip()):
        return {}, "patch.title must be a non-empty string"
    if input_type is None:
        return patch, None
    out = {}
    for key, value in patch.items():
        field = input_type.fields.get(key)
        if field is None:
            return {}, f"patch.{key} is not accepted by {input_type.name}"
        coerced = coerce_input_value(value, field.type)
        if coerced is Undefined:
            problems: list[str] = []
            validate_input_value(value, field.type, lambda err, _path, sink=problems: sink.append(err.message))
            return {}, f"patch.{key}: {problems[0] if problems else 'invalid value'}"
        out[key] = coerced
    return out, None


def run_hooks(engine: Engine, org_id: str, actor_id: str | None, event: str, inp: dict,
              current: dict | None = None,
              input_type: GraphQLInputObjectType | None = None) -> tuple[dict, list[tuple[str, str, list[str]]]]:
    """Run the synchronous hook chain for one write. Returns (input, patches applied).

    Patches are coerced through ``input_type`` (the mutation's GraphQL input type),
    so a hook can never hand the writer a value a client could not send.

    Raises ExtensionRejectedError on a veto, or on a failure of a hook whose policy is block.
    """
    with engine.connect() as conn:
        hooks = conn.execute(select(extension_hook, *_EXT_COLUMNS).join(
            extension, extension.c.id == extension_hook.c.extension_id).where(and_(
                extension_hook.c.organization_id == org_id, extension_hook.c.on_event == event,
                extension.c.uninstalled_at.is_(None),
            )).order_by(extension.c.created_at, extension.c.id, extension_hook.c.position)).mappings().all()
    if not hooks:
        return inp, []
    hooks = [dict(h) for h in hooks if h["bot_user_id"] != actor_id]  # loop guard
    deadline = time.monotonic() + HOOK_BUDGET_SECONDS
    actor = _actor(engine, org_id, actor_id)
    patches: list[tuple[str, str, list[str]]] = []
    result = dict(inp)
    for h in hooks:
        ext_id, breaker_key = h["extension_id"], f"{h['extension_id']}:{h['key']}"
        remaining = deadline - time.monotonic()
        error, status = None, None
        circuit_open = _BREAKER.is_open(breaker_key)
        if circuit_open:
            error = "circuit open after repeated failures"
        elif remaining <= 0.05:
            error = "hook budget for this write was exhausted"
        else:
            payload = {"type": "Issue", "hook": h["key"], "on": event, "input": result, "data": current,
                       "actor": actor, "organizationId": org_id, "extensionId": ext_id}
            try:
                status, body = _call(h, h["path"], payload, "IssueHook", min(HOOK_TIMEOUT_SECONDS, remaining))
                if not 200 <= status < 300:
                    error = f"HTTP {status}"
                elif not isinstance(body, dict) or not isinstance(body.get("allow"), bool):
                    error = 'response must be JSON {"allow": true|false, "patch"?: {...}, "message"?: "..."}'
                elif not body["allow"]:
                    _BREAKER.record(breaker_key, True)
                    raise ExtensionRejectedError(h["name"], str(body.get("message") or "rejected this change"))
                else:
                    patch, error = _typed_patch(body.get("patch") or {}, input_type)
                    if error is None and patch:
                        result.update(patch)
                        patches.append((h["bot_user_id"], h["key"], sorted(patch)))
            except ExtensionRejectedError:
                raise
            except Exception as exc:  # timeout, connection, TLS, refused target
                error = f"{type(exc).__name__}: {exc}"
        if error is None:
            _BREAKER.record(breaker_key, True)
            continue
        if not circuit_open:  # a skipped call is not a new failure (it would reset the breaker)
            _BREAKER.record(breaker_key, False)
            _record_failure(engine, org_id, ext_id, "hook", h["key"], status, error)
        log.warning("extension %s hook %s failed: %s", ext_id, h["key"], error)
        if h["failure_policy"] == "block":
            raise ExtensionRejectedError(h["name"], f"is unavailable ({error}); the change was blocked")
    return result, patches


def record_patches(engine: Engine, org_id: str, issue_id: str, patches) -> None:
    """Attribute hook patches to the extension's bot in the issue history."""
    if not patches:
        return
    with engine.begin() as conn:
        for bot_id, hook_key, fields in patches:
            conn.execute(issue_activity.insert().values(
                id=new_id(), organization_id=org_id, issue_id=issue_id, actor_id=bot_id,
                event_type="extensionPatched", changes=json.dumps({"hook": hook_key, "fields": fields}),
                created_at=now_iso()))


def run_action(engine: Engine, store, org_id: str, actor_id: str, action_id: str, entity_id: str) -> dict:
    """Invoke an action once (no automatic retry) and audit the run."""
    with engine.connect() as conn:
        row = conn.execute(select(extension_action, *_EXT_COLUMNS).join(
            extension, extension.c.id == extension_action.c.extension_id).where(and_(
                extension_action.c.id == action_id, extension_action.c.organization_id == org_id,
                extension.c.uninstalled_at.is_(None)))).mappings().first()
    if not row:
        raise ExtensionError("Action not found.", "actionId")
    visible = store.issue_by_id_or_identifier(org_id, entity_id, viewer_id=actor_id)
    if not visible:
        raise ExtensionError("Issue not found.", "entityId")
    data = issue_data(engine, org_id, visible["id"])
    run_id = new_id()
    payload = {"type": "Issue", "action": row["key"], "runId": run_id, "data": data, "url": data["url"],
               "actor": _actor(engine, org_id, actor_id), "organizationId": org_id,
               "extensionId": row["extension_id"]}
    status, message, code = "failed", None, None
    try:
        code, body = _call(dict(row), row["path"], payload, "IssueAction", ACTION_TIMEOUT_SECONDS)
        reply = body.get("message") if isinstance(body, dict) else None
        message = str(reply)[:500] if reply is not None else None
        if 200 <= code < 300:
            status = "succeeded"
        else:
            message = message or f"HTTP {code}"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"[:500]
    with engine.begin() as conn:
        conn.execute(extension_action_run.insert().values(
            id=run_id, organization_id=org_id, extension_id=row["extension_id"], action_key=row["key"],
            entity_id=visible["id"], actor_id=actor_id, status=status, http_status=code,
            message=message, created_at=now_iso()))
    if status == "failed":
        _record_failure(engine, org_id, row["extension_id"], "action", row["key"], code, message or "failed")
    return {"success": status == "succeeded", "runId": run_id, "status": status, "message": message}
