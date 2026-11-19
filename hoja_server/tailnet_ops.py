"""GraphQL for Tailscale-connected machines (HOJA-602). The mechanics are in ``tailnet.py``.

    query    tailnet                      connected? Hoja's node, the policy, the tailnet's
                                          machines (online, tagged, runner, access)
    mutation tailnetSetupPlan(accessToken)  what Hoja would add to the policy (changes nothing)
             tailnetSetup(accessToken)      add it, create Hoja's own OAuth client, connect;
                                            the access token is used for this call only
             tailnetConnect(clientId, clientSecret)   the manual path
             tailnetDisconnect
             tailnetMachineAllow(deviceId, sshUser, mode, workRoot)
                                          tag it, record the permission, install the runner
                                          over Tailscale SSH (in the background; poll tailnet)
             tailnetMachineRevoke(deviceId)

Admin only, all of it: the device list is the tailnet's inventory, and allowing a machine lets
Hoja run a command on it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from ariadne import MutationType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import and_, select

from hoja_server import agent_run_logs, audit, runner_api, runner_release, secrets_box, tailnet
from hoja_server.agent_runners import runner_status
from hoja_server.db import new_id, now_iso, organization, runner
from hoja_server.db import tailnet as tailnet_table
from hoja_server.db import tailnet_machine

LOG = logging.getLogger("hoja_server.tailnet_ops")
query = QueryType()
mutation = MutationType()

#: The device list is read from Tailscale at most this often per workspace.
DEVICES_TTL_S = 10.0
LOG_LINES = 200

SDL = gql(
    """
    "A workspace's Tailscale connection: Hoja on the tailnet, and the tailnet's machines."
    type Tailnet {
      connected: Boolean!
      "The tailnet's DNS name, e.g. tail1234.ts.net."
      name: String
      clientId: String
      "Hoja's own node on the tailnet: running, starting, stopped (it joins when it needs to), or failed."
      nodeState: String!
      nodeName: String
      nodeError: String
      hojaTag: String!
      machineTag: String!
      "The tailnet policy lines Hoja needs, as JSON."
      policy: String!
      devices: [TailnetDevice!]!
      "Why the device list could not be read, if it could not."
      devicesError: String
    }

    type TailnetDevice {
      id: ID!
      name: String!
      hostname: String!
      os: String
      address: String
      online: Boolean!
      lastSeen: String
      tags: [String!]!
      "Tailscale SSH is on for it (null: Tailscale did not say)."
      sshEnabled: Boolean
      "Can Tailscale SSH run on it: yes (Linux), cli (macOS with the open-source tailscaled only), or no (Windows, phones, the Mac App Store app)."
      sshSupport: String!
      "A likely user name on the machine, from the login that registered it."
      suggestedUser: String
      "Tagged for Hoja: Tailscale lets Hoja's node reach it."
      reachable: Boolean!
      runnerId: String
      runnerName: String
      runnerStatus: String
      access: TailnetAccess
    }

    "A person's permission for Hoja to reach one machine, and how installing the runner went."
    type TailnetAccess {
      "standing (until revoked) or once (one install)."
      mode: String!
      sshUser: String!
      workRoot: String
      "allowed, installing, connected, failed or revoked."
      state: String!
      log: String
      hostKey: String
      grantedAt: String!
      usedAt: String
    }

    "What connecting with an access token would do, before it is done."
    type TailnetSetupPlan {
      "The tailnet's DNS name."
      name: String
      devices: Int!
      "One line per addition to the policy file; empty when it already has everything."
      additions: [String!]!
      "The lines that would be added, as they would appear in the policy file."
      addedText: String!
      "Tailscale's verdict on the changed policy: null when it accepts it."
      problem: String
    }

    extend type Query {
      "Admin only."
      tailnet: Tailnet!
    }

    extend type Mutation {
      "Admin only. Read the tailnet with a person's API access token (tskey-api-…) and say what setting Hoja up would add to its policy. Changes nothing; the token is not kept."
      tailnetSetupPlan(accessToken: String!): TailnetSetupPlan!
      "Admin only. With a person's API access token: add Hoja's lines to the tailnet policy (only what is missing, comments kept), create Hoja's own OAuth client, and connect. The token is used for this call only and never stored."
      tailnetSetup(accessToken: String!): Tailnet!
      "Admin only. Connect the workspace to a tailnet with an OAuth client (scopes devices:core and auth_keys, tags tag:hoja and tag:hoja-machine)."
      tailnetConnect(clientId: String!, clientSecret: String!): Tailnet!
      "Admin only. Forget the OAuth client and take Hoja's node off the tailnet."
      tailnetDisconnect: Tailnet!
      "Admin only. Allow Hoja to reach this machine as sshUser (standing or once), and install the runner there."
      tailnetMachineAllow(deviceId: String!, sshUser: String!, mode: String = "standing", workRoot: String): TailnetDevice!
      "Admin only. Hoja will not connect to this machine again."
      tailnetMachineRevoke(deviceId: String!): TailnetDevice
    }
    """
)

_devices_cache: dict[str, tuple[float, list[tailnet.Device]]] = {}
_node_errors: dict[str, str] = {}
_lock = threading.Lock()


def _ctx(info):
    c = info.context
    return c["store"], c["org_id"], c["user_id"]


def _require_admin(store, org: str, uid: str) -> None:
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError("Only workspace admins can manage Tailscale machines.",
                           extensions={"code": "FORBIDDEN"})


def _user_error(exc: Exception, field: str | None = None) -> GraphQLError:
    ext: dict[str, Any] = {"code": "BAD_USER_INPUT"}
    if field:
        ext["field"] = field
    return GraphQLError(str(exc), extensions=ext)


def _connection(engine, org: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(select(tailnet_table).where(
            tailnet_table.c.organization_id == org)).mappings().first()
    return dict(row) if row else None


def _api(row: dict, org: str):
    return tailnet.api_factory(row["client_id"], secrets_box.decrypt_secret(row["client_secret"], org))


def _secret(row: dict, org: str) -> str:
    return secrets_box.decrypt_secret(row["client_secret"], org)


def _devices(row: dict, org: str, *, fresh: bool = False) -> list[tailnet.Device]:
    with _lock:
        cached = _devices_cache.get(org)
    if cached and not fresh and time.time() - cached[0] < DEVICES_TTL_S:
        return cached[1]
    found = _api(row, org).devices()
    with _lock:
        _devices_cache[org] = (time.time(), found)
    return found


def _org_slug(engine, org: str) -> str:
    with engine.connect() as conn:
        return conn.execute(select(organization.c.url_key).where(
            organization.c.id == org)).scalar() or "workspace"


def _start_node(engine, org: str, row: dict) -> None:
    """Join the tailnet in the background; the result shows up as nodeState."""
    secret, hostname = _secret(row, org), tailnet.node_hostname(_org_slug(engine, org))

    def work():
        try:
            tailnet.node_factory(org).up(secret, hostname)
            _node_errors.pop(org, None)
        except Exception as exc:  # noqa: BLE001 - reported on the screen, not raised
            _node_errors[org] = str(exc) if isinstance(exc, tailnet.TailnetError) else (
                f"Hoja could not join the tailnet: {exc}")
            LOG.warning("tailnet node for %s: %s", org, exc)

    tailnet.in_background(work)


# --------------------------------------------------------------------------- serializers
def _machines(engine, org: str) -> dict[str, dict]:
    with engine.connect() as conn:
        return {r["device_id"]: dict(r) for r in conn.execute(select(tailnet_machine).where(
            tailnet_machine.c.organization_id == org)).mappings()}


def _runners(engine, org: str) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(runner).where(and_(
            runner.c.organization_id == org, runner.c.revoked_at.is_(None)))).mappings()]


def _runner_for(device: tailnet.Device, access: dict | None, runners: list[dict]) -> dict | None:
    if access and access.get("runner_id"):
        for r in runners:
            if r["id"] == access["runner_id"]:
                return r
    want = device.hostname.lower()
    for r in sorted(runners, key=lambda r: r.get("last_seen") or "", reverse=True):
        try:
            labels = json.loads(r.get("labels") or "{}")
        except ValueError:
            labels = {}
        names = {str(r.get("name") or "").lower(), str(labels.get("hostname") or "").lower()}
        if want in names:
            return r
    return None


def _access(row: dict | None) -> dict | None:
    if not row:
        return None
    return {"mode": row["mode"], "sshUser": row["ssh_user"], "workRoot": row.get("work_root"),
            "state": row["state"], "log": row.get("log"), "hostKey": row.get("host_key"),
            "grantedAt": row["granted_at"], "usedAt": row.get("used_at")}


def _device_out(device: tailnet.Device, access: dict | None, runners: list[dict]) -> dict:
    found = _runner_for(device, access, runners)
    return {"id": device.id, "name": device.name, "hostname": device.hostname, "os": device.os,
            "address": device.address, "online": device.online, "lastSeen": device.last_seen,
            "tags": list(device.tags), "reachable": tailnet.MACHINE_TAG in device.tags,
            "sshEnabled": device.ssh_enabled, "sshSupport": device.ssh_support, "suggestedUser": tailnet.suggested_user(device.user),
            "runnerId": found["id"] if found else None,
            "runnerName": found["name"] if found else None,
            "runnerStatus": runner_status(found) if found else None,
            "access": _access(access)}


def _payload(engine, org: str, *, fresh: bool = False) -> dict:
    row = _connection(engine, org)
    base = {"connected": False, "name": None, "clientId": None, "nodeState": "stopped",
            "nodeName": None, "nodeError": None, "hojaTag": tailnet.HOJA_TAG,
            "machineTag": tailnet.MACHINE_TAG, "policy": json.dumps(tailnet.POLICY, indent=2),
            "devices": [], "devicesError": None}
    if row is None:
        return base
    status = tailnet.node_factory(org).status()
    error = _node_errors.get(org)
    base.update(connected=True, name=row.get("name"), clientId=row["client_id"],
                nodeState="failed" if error and status["state"] != "running" else status["state"],
                nodeName=status.get("name"), nodeError=error)
    try:
        devices = _devices(row, org, fresh=fresh)
    except tailnet.TailnetError as exc:
        base["devicesError"] = str(exc)
        return base
    except Exception as exc:  # noqa: BLE001 - Tailscale unreachable is a state, not a crash
        base["devicesError"] = f"Could not reach Tailscale: {exc}"
        return base
    machines, runners = _machines(engine, org), _runners(engine, org)
    hoja_names = {tailnet.node_hostname(_org_slug(engine, org))}
    base["devices"] = sorted(
        (_device_out(d, machines.get(d.id), runners) for d in devices
         if tailnet.HOJA_TAG not in d.tags and d.hostname not in hoja_names),
        key=lambda d: (d["sshSupport"] == "no", not d["online"], d["hostname"].lower()))
    return base


# ------------------------------------------------------------------------------- query
@query.field("tailnet")
def r_tailnet(_obj, info):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    return _payload(store.engine, org)


# ---------------------------------------------------------------------------- mutations
def _token_api(token: str | None):
    text = (token or "").strip()
    if not text.startswith("tskey-api-"):
        raise _user_error(ValueError("Paste an API access token from Tailscale: it starts with "
                                     "tskey-api-."), "accessToken")
    return tailnet.token_api_factory(text)


def _plan(api) -> tuple[list[tailnet.Device], str, str, str, list[str]]:
    devices = api.devices()
    text, etag = api.get_policy()
    new, notes = tailnet.plan_policy(text)
    return devices, text, etag, new, notes


@mutation.field("tailnetSetupPlan")
def m_tailnet_setup_plan(_obj, info, accessToken):  # noqa: N803
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    api = _token_api(accessToken)
    try:
        devices, text, _etag, new, notes = _plan(api)
        problem = None
        if notes:
            try:
                api.validate_policy(new)
            except tailnet.TailnetError as exc:
                problem = str(exc)
    except tailnet.TailnetError as exc:
        raise _user_error(exc, "accessToken") from exc
    except Exception as exc:  # noqa: BLE001
        raise _user_error(ValueError(f"Could not reach Tailscale: {exc}")) from exc
    return {"name": next((d.tailnet_name for d in devices if d.tailnet_name), None),
            "devices": len([d for d in devices if tailnet.HOJA_TAG not in d.tags]),
            "additions": notes, "addedText": tailnet.added_snippet(text), "problem": problem}


@mutation.field("tailnetSetup")
def m_tailnet_setup(_obj, info, accessToken):  # noqa: N803
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    api = _token_api(accessToken)
    try:
        devices, _text, etag, new, notes = _plan(api)
        if notes:
            api.validate_policy(new)
            api.set_policy(new, etag)
        client_id, secret = api.create_oauth_client(
            "Hoja agents", tailnet.CLIENT_SCOPES, tailnet.CLIENT_TAGS)
    except tailnet.TailnetError as exc:
        raise _user_error(exc, "accessToken") from exc
    except Exception as exc:  # noqa: BLE001
        raise _user_error(ValueError(f"Could not reach Tailscale: {exc}")) from exc
    if notes:
        audit.record_request(store, info, user_id=uid, action="tailnet.policy.change",
                             target_kind="tailnet", target_id=org, detail={"added": notes})
    _store_connection(store, info, org, uid, client_id, secret, devices, method="access token")
    return _payload(store.engine, org)


def _store_connection(store, info, org: str, uid: str, client_id: str, secret: str,
                      devices: list[tailnet.Device], *, method: str) -> None:
    name = next((d.tailnet_name for d in devices if d.tailnet_name), None)
    stamp, sealed = now_iso(), secrets_box.encrypt_secret(secret, org)
    with store.engine.begin() as conn:
        existing = conn.execute(select(tailnet_table.c.id).where(
            tailnet_table.c.organization_id == org)).scalar()
        values = {"client_id": client_id, "client_secret": sealed, "name": name,
                  "updated_at": stamp}
        if existing:
            conn.execute(tailnet_table.update().where(tailnet_table.c.id == existing).values(**values))
        else:
            conn.execute(tailnet_table.insert().values(
                id=new_id(), organization_id=org, created_by=uid, created_at=stamp, **values))
    with _lock:
        _devices_cache[org] = (time.time(), devices)
    _node_errors.pop(org, None)
    audit.record_request(store, info, user_id=uid, action="tailnet.connect",
                         target_kind="tailnet", target_id=org,
                         detail={"client_id": client_id, "tailnet": name, "method": method,
                                 "devices": len(devices)})
    _start_node(store.engine, org, _connection(store.engine, org))


@mutation.field("tailnetConnect")
def m_tailnet_connect(_obj, info, clientId, clientSecret):  # noqa: N803
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    client_id, secret = (clientId or "").strip(), (clientSecret or "").strip()
    if not client_id or not secret.startswith("tskey-client-"):
        raise _user_error(ValueError("Paste the OAuth client's ID and its secret (it starts "
                                     "with tskey-client-)."), "clientSecret")
    try:
        devices = tailnet.api_factory(client_id, secret).devices()
    except tailnet.TailnetError as exc:
        raise _user_error(exc, "clientSecret") from exc
    except Exception as exc:  # noqa: BLE001
        raise _user_error(ValueError(f"Could not reach Tailscale: {exc}")) from exc
    _store_connection(store, info, org, uid, client_id, secret, devices, method="oauth client")
    return _payload(store.engine, org)


@mutation.field("tailnetDisconnect")
def m_tailnet_disconnect(_obj, info):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    stamp = now_iso()
    with store.engine.begin() as conn:
        conn.execute(tailnet_table.delete().where(tailnet_table.c.organization_id == org))
        conn.execute(tailnet_machine.update().where(and_(
            tailnet_machine.c.organization_id == org,
            tailnet_machine.c.state != "revoked")).values(
                state="revoked", revoked_at=stamp, updated_at=stamp))
    with _lock:
        _devices_cache.pop(org, None)
    _node_errors.pop(org, None)
    tailnet.in_background(lambda: tailnet.node_factory(org).down())
    audit.record_request(store, info, user_id=uid, action="tailnet.disconnect",
                         target_kind="tailnet", target_id=org, detail={})
    return _payload(store.engine, org)


@mutation.field("tailnetMachineAllow")
def m_tailnet_machine_allow(_obj, info, deviceId, sshUser, mode="standing",  # noqa: N803
                            workRoot=None):
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    row = _connection(store.engine, org)
    if row is None:
        raise _user_error(ValueError("Connect Tailscale first."))
    if mode not in tailnet.MODES:
        raise _user_error(ValueError("mode is standing or once."), "mode")
    try:
        user = tailnet.clean_user(sshUser)
    except tailnet.TailnetError as exc:
        raise _user_error(exc, "sshUser") from exc
    try:
        work_root = runner_release.clean_work_root(workRoot)
    except ValueError as exc:
        raise _user_error(exc, "workRoot") from exc
    try:
        devices = _devices(row, org, fresh=True)
    except tailnet.TailnetError as exc:
        raise _user_error(exc) from exc
    device = next((d for d in devices if d.id == deviceId), None)
    if device is None:
        raise GraphQLError("That machine is not on the tailnet.", extensions={"code": "NOT_FOUND"})
    if not device.address:
        raise _user_error(ValueError("Tailscale gave no address for that machine."))
    if device.ssh_support == "no":
        raise _user_error(ValueError(
            f"Tailscale SSH cannot run on {device.os or 'that machine'}, so Hoja cannot set it "
            "up from here. Connect it with the command instead."))
    if device.ssh_enabled is False:
        raise _user_error(ValueError(
            f"Tailscale SSH is off on {device.hostname}. Run this there once, then try again: "
            "sudo tailscale set --ssh"))
    if machine_busy(store.engine, org, deviceId):
        raise _user_error(ValueError("Hoja is already installing on that machine."))
    if tailnet.MACHINE_TAG not in device.tags:
        try:
            _api(row, org).set_tags(device.id, [*device.tags, tailnet.MACHINE_TAG])
        except tailnet.TailnetError as exc:
            raise _user_error(exc) from exc
        device.tags = (*device.tags, tailnet.MACHINE_TAG)
        with _lock:
            _devices_cache.pop(org, None)
    stamp = now_iso()
    values = {"hostname": device.hostname, "address": device.address, "ssh_user": user,
              "mode": mode, "work_root": work_root, "state": "installing", "log": "",
              "granted_by": uid, "granted_at": stamp, "used_at": None, "revoked_at": None,
              "updated_at": stamp}
    with store.engine.begin() as conn:
        existing = conn.execute(select(tailnet_machine.c.id).where(and_(
            tailnet_machine.c.organization_id == org,
            tailnet_machine.c.device_id == deviceId))).scalar()
        if existing:
            conn.execute(tailnet_machine.update().where(
                tailnet_machine.c.id == existing).values(**values))
        else:
            conn.execute(tailnet_machine.insert().values(
                id=new_id(), organization_id=org, device_id=deviceId, **values))
    audit.record_request(store, info, user_id=uid, action="tailnet.machine.allow",
                         target_kind="tailnet_machine", target_id=deviceId,
                         detail={"hostname": device.hostname, "ssh_user": user, "mode": mode,
                                 "work_root": work_root})
    _, token = runner_api.mint_enrollment_token(
        store.engine, organization_id=org, created_by=uid,
        label=f"Tailscale: {device.hostname}", ttl_s=30 * 60)
    from hoja_server.agent_runner_admin import _base_url

    command = runner_release.connect_command(_base_url(info), token, work_root=work_root)
    tailnet.in_background(lambda: install(store.engine, org, uid, row, device, user, command))
    return _device_out(device, _machines(store.engine, org).get(deviceId),
                       _runners(store.engine, org))


@mutation.field("tailnetMachineRevoke")
def m_tailnet_machine_revoke(_obj, info, deviceId):  # noqa: N803
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    stamp = now_iso()
    with store.engine.begin() as conn:
        changed = conn.execute(tailnet_machine.update().where(and_(
            tailnet_machine.c.organization_id == org,
            tailnet_machine.c.device_id == deviceId)).values(
                state="revoked", revoked_at=stamp, updated_at=stamp)).rowcount
    if not changed:
        raise GraphQLError("Hoja was not allowed on that machine.", extensions={"code": "NOT_FOUND"})
    audit.record_request(store, info, user_id=uid, action="tailnet.machine.revoke",
                         target_kind="tailnet_machine", target_id=deviceId, detail={})
    payload = _payload(store.engine, org)
    return next((d for d in payload["devices"] if d["id"] == deviceId), None)


# ------------------------------------------------------------------------------ install
def machine_busy(engine, org: str, device_id: str) -> bool:
    with engine.connect() as conn:
        state = conn.execute(select(tailnet_machine.c.state, tailnet_machine.c.updated_at).where(
            and_(tailnet_machine.c.organization_id == org,
                 tailnet_machine.c.device_id == device_id))).first()
    return bool(state and state[0] == "installing")


def _write(engine, org: str, device_id: str, **values) -> None:
    from hoja_server import tenant_context

    with tenant_context.scoped(org), engine.begin() as conn:
        conn.execute(tailnet_machine.update().where(and_(
            tailnet_machine.c.organization_id == org,
            tailnet_machine.c.device_id == device_id)).values(updated_at=now_iso(), **values))


def may_connect(engine, org: str, device_id: str) -> bool:
    """Hoja's own rule, on top of the tailnet's: a person allowed it, not revoked, and a
    "just this once" permission not used yet."""
    with engine.connect() as conn:
        row = conn.execute(select(tailnet_machine).where(and_(
            tailnet_machine.c.organization_id == org,
            tailnet_machine.c.device_id == device_id))).mappings().first()
    return bool(row and not row["revoked_at"] and not (row["mode"] == "once" and row["used_at"]))


def install(engine, org: str, uid: str, row: dict, device: tailnet.Device, user: str,
            command: str) -> None:
    """Join the tailnet if needed, then run the installer on the machine. Never raises."""
    lines: list[str] = []
    last = [0.0]

    def out(line: str) -> None:
        lines.append(line.replace(command, "[installer]")[:500])
        del lines[:-LOG_LINES]
        if time.time() - last[0] > 1:
            last[0] = time.time()
            _write(engine, org, device.id, log="\n".join(lines))

    state, summary, host_key = "failed", "", None
    try:
        if not may_connect(engine, org, device.id):
            raise tailnet.TailnetError("Hoja is not allowed on this machine any more.")
        out(f"Joining the tailnet as {tailnet.HOJA_TAG}…")
        node = tailnet.node_factory(org)
        node.up(_secret(row, org), tailnet.node_hostname(_org_slug(engine, org)))
        _node_errors.pop(org, None)
        out(f"Connecting to {device.hostname} ({device.address}) as {user} over Tailscale SSH…")
        code, host_key = tailnet.ssh(node, device.address, user, command, on_output=out)
        if code == 0:
            state, summary = "connected", f"installed the runner on {device.hostname}"
            out("Installed. The machine shows up as online within a few seconds.")
        else:
            summary = f"the installer exited with {code}"
            out(f"The installer exited with {code}.")
    except tailnet.TailnetError as exc:
        summary = str(exc)
        out(str(exc))
    except Exception as exc:  # noqa: BLE001 - the screen shows it; the server keeps going
        LOG.exception("tailnet install on %s", device.hostname)
        summary = f"unexpected error: {exc}"
        out(summary)
    _write(engine, org, device.id, state=state, log="\n".join(lines), host_key=host_key,
           used_at=now_iso())
    agent_run_logs.note(engine, org, None, f"Tailscale install on {device.hostname}: {summary}",
                        level="info" if state == "connected" else "error",
                        kind="tailnet.install",
                        detail={"device": device.id, "user": user, "state": state})
    try:
        audit.record(engine, organization_id=org, actor_kind="user", actor_id=uid,
                     action="tailnet.machine.install", target_kind="tailnet_machine",
                     target_id=device.id, detail={"hostname": device.hostname, "state": state,
                                                  "summary": summary[:300]})
    except Exception:  # noqa: BLE001 - auditing never fails the install
        LOG.debug("could not audit tailnet install", exc_info=True)
