"""Read-only self-check for one workspace host in the shared tier.

``hoja-serve tenant-doctor <host>`` answers "will the owner get into this
workspace on this host?" before anyone tries. Every check either reads, or runs
inside a transaction that is rolled back, so it is safe against production with
the app's own credentials.

Each check reports ``ok``, ``warn``, ``fail`` or ``skip``. Only ``fail`` makes
the command exit non-zero; ``warn`` is something an operator should know about
that does not stop the owner signing in (mail not configured, a member the
control-plane directory cannot route).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from sqlalchemy import and_, func, or_, select

from hoja_server import (
    addresses,
    google_login,
    identity_transfer,
    mail_config,
    signup_config,
    tenant_context,
)
from hoja_server.db import (
    organization,
    organization_membership,
    signup_eligibility,
    tenant_membership_hint,
    user,
)
from hoja_server.workspace_provision import ProvisionError, tenant_base_domain, validate_vanity_host

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
ROUTES_DIR_ENV = "HOJA_TRAEFIK_DYNAMIC_DIR"
DEFAULT_ROUTES_DIR = "/opt/hoja/deploy/traefik/dynamic"
SHARED_SERVICE = "hoja"
_PROBE_SUBJECT = "tenant-doctor"


class Report:
    def __init__(self, host: str) -> None:
        self.host = host
        self.checks: list[dict] = []
        self.facts: dict = {}

    def add(self, name: str, status: str, detail: str, fix: str | None = None) -> None:
        entry = {"check": name, "status": status, "detail": detail}
        if fix:
            entry["fix"] = fix
        self.checks.append(entry)

    @property
    def ok(self) -> bool:
        return not any(c["status"] == FAIL for c in self.checks)

    def as_dict(self) -> dict:
        return {"host": self.host, "ok": self.ok, "facts": self.facts, "checks": self.checks}

    def lines(self) -> list[str]:
        out = [f"tenant-doctor {self.host}"]
        for key, value in self.facts.items():
            out.append(f"  {key:<16} {value}")
        out.append("")
        for c in self.checks:
            out.append(f"  [{c['status'].upper():<4}] {c['check']:<18} {c['detail']}")
            if c.get("fix"):
                out.append(f"  {'':<6} {'':<18} fix: {c['fix']}")
        out.append("")
        out.append("verdict: " + ("OK" if self.ok else "BLOCKED (see FAIL lines)"))
        return out


def _route_check(report: Report, host: str, label: str, route_text: str | None,
                 routes_dir: str | None, env) -> None:
    source = "--route-file"
    if route_text is None:
        directory = Path(routes_dir or env.get(ROUTES_DIR_ENV) or DEFAULT_ROUTES_DIR)
        path = directory / f"tenant-{label}.yml"
        unreadable = (f"{directory} is not readable from here (the app container does not mount it)",
                      f"pass --route-file - < {DEFAULT_ROUTES_DIR}/tenant-{label}.yml, or --routes-dir")
        try:
            present = directory.is_dir()
        except OSError:
            present = False
        if not present:
            report.add("route", SKIP, *unreadable)
            return
        try:
            route_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            report.add("route", FAIL, f"{path} is missing: Traefik sends {host} to the catch-all "
                       "redirect to the marketing site",
                       f"infra/scripts/hoja-tenant route {label} --apply")
            return
        except OSError:
            report.add("route", SKIP, *unreadable)
            return
        source = str(path)
    if f"Host(`{host}`)" not in route_text:
        report.add("route", FAIL, f"{source} has no Host(`{host}`) rule",
                   f"infra/scripts/hoja-tenant route {label} --apply")
        return
    if not re.search(rf"^\s*service:\s*{SHARED_SERVICE}\s*$", route_text, re.MULTILINE):
        report.add("route", WARN, f"{source} does not point at the shared service `{SHARED_SERVICE}` "
                   "(a dedicated-container route?)", f"infra/scripts/hoja-tenant route {label} --apply")
        return
    if "certResolver" in route_text:
        report.add("route", WARN, f"{source} requests its own certificate instead of the wildcard",
                   f"infra/scripts/hoja-tenant route {label} --apply")
        return
    report.add("route", OK, f"{source} routes {host} to `{SHARED_SERVICE}` on the wildcard certificate")


def _handoff_check(report: Report, host: str, env) -> None:
    try:
        master = signup_config.identity_transfer_master(env)
        shared_key = signup_config.identity_transfer_key(env)
    except signup_config.SignupConfigError as exc:
        report.add("handoff_key", FAIL, str(exc))
        return
    if not master:
        if shared_key:
            report.add("handoff_key", WARN,
                       f"{signup_config.TRANSFER_MASTER_ENV} is unset; {host} verifies hand-offs with the "
                       f"deployment-wide {signup_config.TRANSFER_KEY_ENV}, which the control plane does not "
                       "derive per host", f"set {signup_config.TRANSFER_MASTER_ENV} in the app environment")
        else:
            report.add("handoff_key", FAIL,
                       f"neither {signup_config.TRANSFER_MASTER_ENV} nor {signup_config.TRANSFER_KEY_ENV} is set: "
                       f"/auth/google/finish on {host} answers 503",
                       f"set {signup_config.TRANSFER_MASTER_ENV} in the app environment")
        return
    try:
        key = identity_transfer.tenant_key(master, host)
        token = identity_transfer.issue(key=key, audience=host, email="probe@tenant-doctor.invalid",
                                        subject=_PROBE_SUBJECT, email_verified=True)
        claims = identity_transfer.redeem(key=key, token=token, audience=host)
    except identity_transfer.TransferError as exc:
        report.add("handoff_key", FAIL, f"derived-key round trip failed: {exc.reason}")
        return
    try:
        identity_transfer.redeem(key=key, token=token, audience="other." + host)
    except identity_transfer.TransferError:
        pass
    else:
        report.add("handoff_key", FAIL, "a token for this host was accepted for another audience")
        return
    if claims.get("aud") != host:
        report.add("handoff_key", FAIL, "round trip returned the wrong audience")
        return
    report.add("handoff_key", OK, "per-host key derives from the master; issue and redeem round-trip, "
               "other audiences refused")


def _owner_address(conn, org_id: str, slug: str, explicit: str | None) -> tuple[str | None, str]:
    if explicit:
        return addresses.canonical(explicit), "--owner-email"
    row = conn.execute(select(signup_eligibility.c.email).where(and_(
        signup_eligibility.c.slug == slug, signup_eligibility.c.status == "active"))).first()
    if row:
        return addresses.canonical(row[0]), "sign-up request"
    row = conn.execute(select(tenant_membership_hint.c.email).where(and_(
        tenant_membership_hint.c.slug == slug, tenant_membership_hint.c.role == "admin"))
        .order_by(tenant_membership_hint.c.created_at)).first()
    if row:
        return addresses.canonical(row[0]), "directory entry"
    row = conn.execute(select(user.c.email).where(and_(
        user.c.organization_id == org_id, user.c.admin.is_(True), user.c.active.is_(True)))
        .order_by(user.c.created_at)).first()
    if row and row[0]:
        return addresses.canonical(row[0]), "oldest active admin"
    return None, "none"


def _workspace_checks(report: Report, engine, host: str, label: str, owner_email: str | None) -> None:
    # One transaction, always rolled back: resolve_member below may write the
    # membership it would create, and nothing the doctor does may persist.
    with tenant_context.system(), engine.connect() as conn:
        trans = conn.begin()
        try:
            _workspace_checks_in(report, conn, host, label, owner_email)
        finally:
            trans.rollback()


def _workspace_checks_in(report: Report, conn, host: str, label: str, owner_email: str | None) -> None:
    rows = conn.execute(select(organization.c.id, organization.c.url_key, organization.c.name)
                        .where(func.lower(organization.c.vanity_host) == host)).all()
    if not rows:
        by_slug = conn.execute(select(organization.c.id, organization.c.vanity_host)
                               .where(func.lower(organization.c.url_key) == label)).first()
        detail = f"no workspace has vanity_host={host}; the app redirects {host} to the canonical host"
        fix = None
        if by_slug is not None:
            detail += (f" (workspace {label!r} exists with vanity_host="
                       f"{by_slug[1] or 'NULL'})")
            fix = (f"hoja-serve create-workspace --slug {label} --owner-email <owner> "
                   f"--vanity-host {host}")
        report.add("vanity_host", FAIL, detail, fix)
        for name in ("slug", "admins", "members", "owner_admission", "directory"):
            report.add(name, SKIP, "no workspace for this host")
        return
    if len(rows) > 1:
        report.add("vanity_host", FAIL, f"{len(rows)} workspaces claim {host}: "
                   + ", ".join(r[1] for r in rows))
        for name in ("slug", "admins", "members", "owner_admission", "directory"):
            report.add(name, SKIP, "host is ambiguous")
        return
    org_id, slug, name = rows[0]
    report.facts.update({"organization": org_id, "url_key": slug, "name": name})
    report.add("vanity_host", OK, f"exactly one workspace ({slug}) is pinned to {host}")
    if slug.lower() == label:
        report.add("slug", OK, f"url_key {slug!r} matches the host label")
    else:
        report.add("slug", WARN, f"url_key {slug!r} differs from {label!r}: the control plane hands "
                   f"members of {slug!r} to {slug}.<base>, not to {host}")

    admins = conn.execute(select(user.c.email).where(and_(
        user.c.organization_id == org_id, user.c.admin.is_(True), user.c.active.is_(True)))).scalars().all()
    if admins:
        report.add("admins", OK, f"{len(admins)} active admin profile(s)")
    else:
        report.add("admins", FAIL, "no active admin: nobody can manage members or settings")

    members = conn.execute(select(func.count()).select_from(organization_membership).where(and_(
        organization_membership.c.organization_id == org_id,
        organization_membership.c.active.is_(True)))).scalar_one()
    report.facts["members"] = members
    if members:
        report.add("members", OK, f"{members} active membership(s)")
    else:
        report.add("members", WARN, "zero active memberships: every sign-in depends on the "
                   "profile-link path", f"hoja-serve create-workspace --slug {slug} --owner-email <owner> "
                   f"--vanity-host {host}")

    address, source = _owner_address(conn, org_id, slug, owner_email)
    report.facts["owner"] = f"{address or '-'} ({source})"
    if address is None:
        report.add("owner_admission", FAIL, "no owner address is known for this workspace",
                   "pass --owner-email")
    else:
        verdict = google_login.resolve_member(conn, email=address, subject=_PROBE_SUBJECT,
                                              organization_id=org_id)
        if verdict["action"] != google_login.ACTION_LOGIN:
            report.add("owner_admission", FAIL,
                       f"{address} would be refused on {host}: {verdict.get('reason', 'denied')}",
                       f"hoja-serve create-workspace --slug {slug} --owner-email {address} "
                       f"--vanity-host {host}")
        elif verdict.get("linked"):
            report.add("owner_admission", OK, f"{address} would be admitted by linking its unclaimed "
                       "profile (no membership yet)")
        else:
            report.add("owner_admission", OK, f"{address} would be admitted through its membership")

    hints = set(conn.execute(select(tenant_membership_hint.c.email).where(
        tenant_membership_hint.c.slug == slug)).scalars())
    signups = set(conn.execute(select(signup_eligibility.c.email).where(and_(
        signup_eligibility.c.slug == slug,
        or_(signup_eligibility.c.status == "active", signup_eligibility.c.status == "provisioning"))))
        .scalars())
    known = {addresses.canonical(a) for a in hints | signups}
    missing = sorted(a for a in (addresses.canonical(e) for e in admins if e) if a not in known)
    if not admins:
        report.add("directory", SKIP, "no active admins to look up")
    elif missing:
        report.add("directory", WARN,
                   f"the control plane cannot route {len(missing)} admin(s) from app sign-in: "
                   + ", ".join(missing),
                   "the owner is added by create-workspace; others appear after their next sign-in "
                   "on the workspace host once membership announcements are verified")
    else:
        report.add("directory", OK, "every active admin is in the control-plane directory")


def diagnose(engine, host: str, *, owner_email: str | None = None, route_text: str | None = None,
             routes_dir: str | None = None, environ=None) -> Report:
    env = os.environ if environ is None else environ
    host = (host or "").strip()
    report = Report(host)
    base = tenant_base_domain(env)
    report.facts["base_domain"] = base
    label = host[: -len(base) - 1] if host.endswith("." + base) else host.split(".", 1)[0]
    try:
        validate_vanity_host(host, slug=label, base_domain=base)
    except ProvisionError as exc:
        report.add("host", FAIL, exc.message)
    else:
        report.add("host", OK, f"{host} is a single, unreserved label under {base}")

    _workspace_checks(report, engine, host.lower(), label.lower(), owner_email)
    _route_check(report, host.lower(), label.lower(), route_text, routes_dir, env)
    _handoff_check(report, host.lower(), env)

    try:
        control_plane = signup_config.control_plane_url(env)
    except signup_config.SignupConfigError as exc:
        report.add("control_plane", FAIL, str(exc))
    else:
        if control_plane:
            report.add("control_plane", OK, f"Google sign-in on {host} delegates to {control_plane}")
        else:
            report.add("control_plane", FAIL, f"{signup_config.CONTROL_PLANE_ENV} is unset: {host} has "
                       "nowhere to send Google sign-in", f"set {signup_config.CONTROL_PLANE_ENV}")

    try:
        mail = mail_config.load(env)
    except mail_config.MailConfigError as exc:
        report.add("mail", WARN, f"mail configuration is invalid: {exc}")
    else:
        if mail.configured:
            report.add("mail", OK, f"transport {mail.transport}")
        else:
            report.add("mail", WARN, "mail transport is disabled: invitations are created but not emailed")
    return report
