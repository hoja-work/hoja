"""FastAPI + Ariadne ASGI app with token auth middleware.

- POST /graphql : the Linear-compatible endpoint the CLI talks to.
- Auth: raw token in Authorization header (with or without 'Bearer').
  Unknown/revoked token → HTTP 401 + {errors:[{message}]}.
- Never emits 429. GraphQL errors → HTTP 200 + {errors:[...], data:null}.
- Optional OFFLINE mode: if CLINIAR_SERVER_OPEN=1, any token maps to the only
  seeded identity in an unambiguous SQLite database.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ariadne import graphql
from graphql import GraphQLError, OperationDefinitionNode, OperationType, parse
from sqlalchemy import select, text
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute

from cliniar_server.db import (
    _seed_demo_issues,
    api_key,
    browser_session,
    gen_session_token,
    gen_token,
    global_identity,
    identity_credential,
    make_engine,
    new_id,
    now_iso,
    organization,
    organization_membership,
    resolve_database_target,
    resolve_token_identity,
    schema_is_current,
    seed_states_for_team,
    team,
    team_member,
    token_hash,
    user,
)
from cliniar_server.resolvers import build_schema
from cliniar_server.store import Store
from cliniar_server.writer import Writer

_schema = build_schema()
_WEB_ROOT = Path(
    os.environ.get("CLINIAR_WEB_ROOT", Path(__file__).resolve().parent.parent / "frontend" / "dist")
)


def _first_identity(engine):
    """Offline convenience for an unambiguous isolated SQLite database."""
    if engine.dialect.name != "sqlite":
        return None, None
    try:
        return resolve_token_identity(engine)
    except ValueError:
        return None, None


def _valid_public_host(value: str) -> bool:
    """Accept configured DNS names/IP literals, but not URL syntax or userinfo."""
    import re
    from urllib.parse import urlsplit

    candidate = value.strip()
    if not candidate or any(ch in candidate for ch in "/\\@?#"):
        return False
    parsed = urlsplit("//" + candidate)
    if not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
        return False
    try:
        ipaddress.ip_address(parsed.hostname)
        return True
    except ValueError:
        labels = parsed.hostname.rstrip(".").split(".")
        return len(parsed.hostname) <= 253 and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in labels
        )


_MAX_SEQ = 2**63 - 1


def create_app(
    db_path: str | None = None,
    *,
    database_url: str | None = None,
    tenant: str = "default",
    open_mode: bool | None = None,
    trusted_proxy_cidrs: str | None = None,
    trusted_public_host: str | None = None,
) -> Starlette:
    target = resolve_database_target(database_url=database_url, db_path=db_path, tenant=tenant)
    engine = make_engine(target)
    store = Store(engine)
    writer = Writer(engine)
    if open_mode is None:
        open_mode = os.environ.get("CLINIAR_SERVER_OPEN", "0") == "1"

    def _auth(request: Request):
        raw = request.headers.get("authorization", "") or request.headers.get("Authorization", "")
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()
        session_token = request.cookies.get("hoja_session")
        if session_token:
            session = store.resolve_browser_session(session_token)
            if session:
                member = store.membership_for_profile(
                    session["organization_id"], session["user_id"]
                )
                return (
                    session["user_id"],
                    session["organization_id"],
                    (member or {}).get("identity_id"),
                    True,
                )
        if token:
            key = store.resolve_token(token)
            if key:
                store.touch_api_key(key["id"])
                request.state.api_key_access = key["access"]
                return key["user_id"], key["organization_id"], None, False
        if open_mode:
            uid, org = _first_identity(engine)
            if uid:
                return uid, org, None, False
        return None, None, None, False

    def _mcp_oauth_auth(request: Request):
        """Resolve a valid MCP access token to the same pinned Hoja identity."""
        raw = request.headers.get("authorization", "")
        if not raw.lower().startswith("bearer "):
            return None
        from datetime import datetime, timezone

        from sqlalchemy import select

        from cliniar_server.db import oauth_access_token

        digest = token_hash(raw[7:].strip())
        with engine.connect() as conn:
            row = (
                conn.execute(
                    select(oauth_access_token).where(
                        oauth_access_token.c.token_hash == digest,
                        oauth_access_token.c.revoked_at.is_(None),
                        oauth_access_token.c.expires_at > datetime.now(timezone.utc).isoformat(),
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            return None
        member = store.membership_for_profile(row["organization_id"], row["user_id"])
        if not member:
            return None
        return {
            "user_id": row["user_id"],
            "org_id": row["organization_id"],
            "identity_id": member.get("identity_id"),
            "scopes": row["scopes"],
            "client_id": row["client_id"],
            "org_name": (store.workspace(row["organization_id"]) or {}).get("name"),
        }

    from cliniar_server.mcp_oauth import HojaOAuthProvider, build_server

    # OAuth stays fully disabled unless an explicit public origin and trusted
    # proxy CIDR configuration are provided by the deployment.
    proxy_cidrs = (
        os.environ.get("HOJA_TRUSTED_PROXY_CIDRS", "")
        if trusted_proxy_cidrs is None
        else trusted_proxy_cidrs
    )
    public_host = (
        os.environ.get("HOJA_TRUSTED_PUBLIC_HOST", "")
        if trusted_public_host is None
        else trusted_public_host
    )
    proxy_networks = []
    if proxy_cidrs.strip() and public_host.strip():
        try:
            proxy_networks = [
                ipaddress.ip_network(item.strip(), strict=False)
                for item in proxy_cidrs.split(",")
                if item.strip()
            ]
        except ValueError as exc:
            raise ValueError("HOJA_TRUSTED_PROXY_CIDRS must be comma-separated IP networks.") from exc
    trust_proxy = bool(proxy_networks) and _valid_public_host(public_host)

    def _request_from_trusted_proxy(request: Request) -> bool:
        if not trust_proxy:
            return False
        peer = request.client.host if request.client else None
        try:
            address = ipaddress.ip_address(peer) if peer else None
        except ValueError:
            return False
        return bool(address and any(address in network for network in proxy_networks))
    mcp_provider = HojaOAuthProvider(engine, store, trusted_proxy=trust_proxy)
    mcp_server = build_server(mcp_provider, public_host=public_host)
    # SDK auth and protected-resource routes are root-relative to its Starlette
    # app; Mount strips /mcp while preserving it in root_path for metadata URLs.
    from mcp.server.transport_security import TransportSecuritySettings

    mcp_asgi = mcp_server.streamable_http_app(
        streamable_http_path="/", stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public_host.strip(), public_host.strip() + ":*"],
        ),
    )

    async def mcp_endpoint(scope, receive, send):
        request = Request(scope)
        auth_context = _mcp_oauth_auth(request)
        trusted_peer = _request_from_trusted_proxy(request)
        incoming_host = request.headers.get("host", "").lower()
        canonical_host = public_host.strip().lower()
        host_ok = incoming_host == canonical_host or incoming_host.startswith(canonical_host + ":")
        if trust_proxy and (not trusted_peer or not host_ok):
            auth_context = None
            if not host_ok:
                response = JSONResponse({"error": "Untrusted Host header."}, status_code=421)
                await response(scope, receive, send)
                return
            response = JSONResponse({"error": "Request did not originate from a trusted proxy."}, status_code=403)
            await response(scope, receive, send)
            return
        # The browser authorize/consent endpoints authenticate via the existing
        # Hoja session cookie; only MCP resource calls use the OAuth bearer.
        if scope.get("method") in ("GET", "POST") and scope.get("path", "").endswith("/oauth/consent"):
            uid, org, identity_id, is_session = _auth(request)
            if uid and is_session:
                member = store.membership_for_profile(org, uid)
                auth_context = {
                    "user_id": uid,
                    "org_id": org,
                    "identity_id": identity_id or (member or {}).get("identity_id"),
                    "org_name": (store.workspace(org) or {}).get("name"),
                    "source": "browser_session",
                }
        scope["hoja_auth"] = auth_context
        root_path = scope.get("root_path", "")
        if root_path.endswith("/.well-known/oauth-authorization-server/mcp"):
            scope["path"] = "/.well-known/oauth-authorization-server"
            scope["root_path"] = ""
        elif root_path.endswith("/.well-known/oauth-protected-resource/mcp"):
            scope["path"] = "/.well-known/oauth-protected-resource/mcp"
            scope["root_path"] = ""
        await mcp_asgi(scope, receive, send)

    async def authorization_metadata_endpoint(request: Request):
        if not trust_proxy:
            return JSONResponse({"error": "OAuth is not configured."}, status_code=404)
        if not _request_from_trusted_proxy(request):
            return JSONResponse({"error": "OAuth is available only behind the configured trusted proxy."}, status_code=404)
        origin = f"https://{public_host}" if trust_proxy else "https://mcp.invalid"
        issuer = origin + "/mcp"
        return JSONResponse({"issuer": issuer, "authorization_endpoint": issuer + "/authorize", "token_endpoint": issuer + "/token", "registration_endpoint": issuer + "/register", "revocation_endpoint": issuer + "/revoke", "scopes_supported": ["hoja:read", "hoja:write"], "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"]})

    async def resource_metadata_endpoint(request: Request):
        if not trust_proxy:
            return JSONResponse({"error": "OAuth is not configured."}, status_code=404)
        if not _request_from_trusted_proxy(request):
            return JSONResponse({"error": "OAuth is available only behind the configured trusted proxy."}, status_code=404)
        issuer = (f"https://{public_host}" if trust_proxy else "https://mcp.invalid") + "/mcp"
        return JSONResponse({"resource": issuer, "authorization_servers": [issuer], "scopes_supported": ["hoja:read"]})

    from starlette.routing import Mount

    async def realtime_socket(websocket: WebSocket):
        offered = websocket.scope.get("subprotocols") or []
        subprotocol = "bearer" if "bearer" in offered else None
        token_header = ""
        if subprotocol and len(offered) > 1:
            token_header = f"Bearer {offered[1]}"
        websocket.scope["headers"] = [
            *websocket.scope["headers"],
            *([(b"authorization", token_header.encode())] if token_header else []),
        ]
        uid, org, _identity_id, _is_session = _auth(websocket)
        await websocket.accept(subprotocol=subprotocol)
        if not uid:
            await websocket.close(code=4401)
            return
        try:
            cursor = int(websocket.query_params.get("after", "0"))
        except ValueError:
            await websocket.close(code=4400)
            return
        if not 0 <= cursor <= _MAX_SEQ:
            await websocket.close(code=4400)
            return
        last_seq = cursor
        try:
            while True:
                events = await asyncio.to_thread(store.change_events_after, org, last_seq)
                for event in events:
                    await websocket.send_json(event)
                    last_seq = event["seq"]
                if not events:
                    await asyncio.sleep(1)
        except WebSocketDisconnect:
            return

    async def graphql_server(request: Request):
        uid, org, identity_id, is_browser_session = _auth(request)
        if not uid:
            return JSONResponse(
                {
                    "errors": [
                        {
                            "message": "Authentication required",
                            "extensions": {"code": "AUTHENTICATION_ERROR"},
                        }
                    ]
                },
                status_code=401,
            )
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse(
                {"errors": [{"message": "Request body must be valid JSON.",
                             "extensions": {"code": "BAD_REQUEST"}}]},
                status_code=400,
            )
        if not isinstance(data, dict):
            return JSONResponse(
                {"errors": [{"message": "Request body must be a JSON object.",
                             "extensions": {"code": "BAD_REQUEST"}}]},
                status_code=400,
            )
        if getattr(request.state, "api_key_access", "read_write") == "read":
            try:
                document = parse(data.get("query") or "")
            except GraphQLError:
                document = None
            if document is not None and any(
                isinstance(definition, OperationDefinitionNode)
                and definition.operation == OperationType.MUTATION
                for definition in document.definitions
            ):
                return JSONResponse(
                    {"errors": [{"message": "This API key is read-only.",
                                 "extensions": {"code": "FORBIDDEN"}}]},
                    status_code=403,
                )
        context = {
            "request": request,
            "store": store,
            "writer": writer,
            "org_id": org,
            "user_id": uid,
            "identity_id": identity_id,
            "browser_session": is_browser_session,
        }
        _success, result = await graphql(
            _schema,
            data,
            context_value=context,
            debug=(os.environ.get("CLINIAR_SERVER_DEBUG", "0") == "1"),
        )
        target = context.get("switch_target")
        if target:
            session_token, _details = store.create_session_for_membership(
                target.get("identity_id", identity_id), target["user_id"], target["organization_id"]
            )
            if not session_token:
                return JSONResponse(
                    {"errors": [{"message": "Workspace access revoked"}]}, status_code=403
                )
            raw = request.cookies.get("hoja_session")
            if raw:
                store.revoke_browser_session(raw)
            response = JSONResponse(result, status_code=200)
            response.set_cookie(
                "hoja_session",
                session_token,
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="lax",
                max_age=14 * 24 * 60 * 60,
                path="/",
            )
            return response
        return JSONResponse(result, status_code=200)

    async def auth_setup(request: Request):
        if request.method == "GET":
            return JSONResponse(
                {"setup": True, "message": "Set a password using a valid local API token."}
            )
        body = await request.json()
        try:
            setup_token = str(body.get("token", ""))
            setup_email = str(body.get("email", "")).strip().lower()
            identity = store.resolve_token(setup_token)
            if identity and identity["access"] == "read":
                return JSONResponse(
                    {"error": "Read-only API keys cannot set a password."}, status_code=403
                )
            if setup_email and identity:
                with engine.connect() as conn:
                    account_email = conn.execute(
                        select(user.c.email).where(user.c.id == identity["user_id"])
                    ).scalar_one_or_none()
                if not account_email or account_email.strip().lower() != setup_email:
                    return JSONResponse(
                        {"error": "This invite does not match that email address."}, status_code=401
                    )
            ok = store.set_password(setup_token, str(body.get("password", "")))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not ok:
            return JSONResponse({"error": "Invalid API token"}, status_code=401)
        return JSONResponse({"status": "password_set"}, status_code=201)

    async def auth_invite_accept(request: Request):
        body = await request.json()
        try:
            result = store.accept_new_member_invitation(
                str(body.get("code", "")), str(body.get("email", "")), str(body.get("password", ""))
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not result:
            return JSONResponse(
                {"error": "Invite code is invalid, expired, or does not match that email."},
                status_code=401,
            )
        session_token, _identity = result
        response = JSONResponse({"status": "accepted"})
        response.set_cookie(
            "hoja_session",
            session_token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=14 * 24 * 60 * 60,
            path="/",
        )
        return response

    async def onboarding_status(_request: Request):
        try:
            initialized = store.is_initialized()
        except Exception:
            return JSONResponse({"error": "Setup status unavailable"}, status_code=503)
        return JSONResponse({"initialized": initialized})

    async def onboarding_bootstrap(request: Request):
        body = await request.json()
        name = str(body.get("name", "")).strip()
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", ""))
        workspace_name = str(body.get("workspaceName", "")).strip()
        workspace_key = str(body.get("workspaceKey", "")).strip().lower()
        team_name = str(body.get("teamName", "")).strip()
        team_key = str(body.get("teamKey", "")).strip().upper()
        if not all((name, workspace_name, workspace_key, team_name, team_key)) or "@" not in email:
            return JSONResponse(
                {"error": "Complete all fields with a valid email."}, status_code=400
            )
        if len(password) < 12:
            return JSONResponse(
                {"error": "Password must be at least 12 characters."}, status_code=400
            )
        if not workspace_key.replace("-", "").isalnum() or not team_key.isalnum():
            return JSONResponse(
                {
                    "error": "Workspace and team keys may contain only letters, numbers, and workspace hyphens."
                },
                status_code=400,
            )

        raw_api_key = gen_token()
        session_token = gen_session_token()
        now = now_iso()
        identity_id, org_id, uid, tid = new_id(), new_id(), new_id(), new_id()
        password_hash = Store._password_digest(password, secrets.token_bytes(16))
        try:
            # Serialize fresh-instance check and all initial rows in one transaction.
            with engine.begin() as conn:
                if conn.dialect.name == "postgresql":
                    conn.execute(text("SELECT pg_advisory_xact_lock(721946031)"))
                elif conn.dialect.name == "sqlite":
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                if conn.execute(select(organization.c.id).limit(1)).first():
                    return JSONResponse(
                        {"error": "This Hoja instance is already set up."}, status_code=409
                    )
                conn.execute(
                    organization.insert().values(
                        id=org_id,
                        name=workspace_name,
                        url_key=workspace_key,
                        created_at=now,
                        updated_at=now,
                    )
                )
                conn.execute(
                    user.insert().values(
                        id=uid,
                        organization_id=org_id,
                        name=name,
                        display_name=name,
                        email=email,
                        active=True,
                        admin=True,
                        timezone="UTC",
                        created_at=now,
                        updated_at=now,
                    )
                )
                conn.execute(
                    global_identity.insert().values(id=identity_id, email=email, created_at=now)
                )
                conn.execute(
                    organization_membership.insert().values(
                        id=new_id(),
                        identity_id=identity_id,
                        organization_id=org_id,
                        profile_user_id=uid,
                        active=True,
                        created_at=now,
                    )
                )
                conn.execute(
                    identity_credential.insert().values(
                        identity_id=identity_id,
                        password_hash=password_hash,
                        created_at=now,
                        updated_at=now,
                    )
                )
                conn.execute(
                    api_key.insert().values(
                        id=new_id(),
                        token_hash=token_hash(raw_api_key),
                        label="initial admin",
                        user_id=uid,
                        organization_id=org_id,
                        created_at=now,
                    )
                )
                conn.execute(
                    team.insert().values(
                        id=tid,
                        organization_id=org_id,
                        key=team_key,
                        name=team_name,
                        description=f"{team_name} team",
                        color="#5e6ad2",
                        private=False,
                        timezone="UTC",
                        issue_counter=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
                conn.execute(team_member.insert().values(team_id=tid, user_id=uid))
                default_state = seed_states_for_team(conn, org_id, tid)
                conn.execute(
                    team.update().where(team.c.id == tid).values(default_state_id=default_state)
                )
                _seed_demo_issues(conn, org_id, tid, team_key, uid, default_state, workspace_key)
                conn.execute(
                    browser_session.insert().values(
                        id=new_id(),
                        token_hash=token_hash(session_token),
                        user_id=uid,
                        organization_id=org_id,
                        created_at=now,
                        expires_at=(datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
                    )
                )
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError

            if isinstance(exc, IntegrityError):
                return JSONResponse(
                    {"error": "Workspace key or email is already in use."}, status_code=409
                )
            raise
        response = JSONResponse(
            {
                "status": "created",
                "apiKey": raw_api_key,
                "teamId": tid,
                "workspace": {"id": org_id, "name": workspace_name, "urlKey": workspace_key},
            }
        )
        response.set_cookie(
            "hoja_session",
            session_token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=14 * 24 * 60 * 60,
            path="/",
        )
        return response

    async def auth_login(request: Request):
        body = await request.json()
        email = str(body.get("email", ""))
        password = str(body.get("password", ""))
        organization_ref = body.get("organizationId")
        api_key_value = str(body.get("apiKey", ""))
        if not api_key_value and not organization_ref:
            try:
                matches = store.matching_login_workspaces(email)
            except Exception as exc:
                # Old databases may not have the identity/membership schema yet.
                # Do not disguise unrelated database errors as invalid credentials.
                from sqlalchemy.exc import OperationalError

                if not isinstance(exc, OperationalError) or "organization_membership" not in str(
                    exc
                ):
                    raise
                return JSONResponse(
                    {
                        "error": "Workspace sign-in is unavailable until the database schema is migrated."
                    },
                    status_code=503,
                )
            if len(matches) > 1:
                return JSONResponse(
                    {"error": "Choose a workspace", "workspaces": matches}, status_code=409
                )
            if len(matches) == 1:
                organization_ref = matches[0]["organizationId"]
        if api_key_value:
            identity = store.resolve_token(api_key_value)
            if not identity:
                return JSONResponse({"error": "Invalid API key"}, status_code=401)
            if identity["access"] == "read":
                return JSONResponse(
                    {"error": "Read-only API keys cannot open a browser session."}, status_code=403
                )
            # When this identity belongs to several workspaces, an API key is
            # scoped to only one of them. Honor an explicit workspace selection
            # rather than silently creating a session in the key's workspace.
            if organization_ref:
                member = store.membership_for_profile(
                    identity["organization_id"], identity["user_id"]
                )
                selected = store.switch_membership(
                    member.get("identity_id") if member else None, organization_ref
                )
                if selected:
                    session_token, _details = store.create_session_for_membership(
                        member["identity_id"], selected["user_id"], selected["organization_id"]
                    )
                    result = (session_token, selected) if session_token else None
                else:
                    result = None
            else:
                result = store.create_browser_session_for_token(api_key_value)
        else:
            result = store.create_browser_session(
                email, password, organization_ref=organization_ref
            )
        if not result:
            return JSONResponse({"error": "Invalid credentials"}, status_code=401)
        session_token, identity = result
        response = JSONResponse({"status": "authenticated", "expiresAt": identity["expires_at"]})
        response.set_cookie(
            "hoja_session",
            session_token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=14 * 24 * 60 * 60,
            path="/",
        )
        return response

    async def auth_workspaces(request: Request):
        body = await request.json()
        return JSONResponse(
            {"workspaces": store.matching_login_workspaces(str(body.get("email", "")))}
        )

    async def auth_logout(request: Request):
        session_token = request.cookies.get("hoja_session")
        if session_token:
            store.revoke_browser_session(session_token)
        response = JSONResponse({"status": "logged_out"})
        response.delete_cookie("hoja_session", path="/")
        return response

    async def auth_me(request: Request):
        uid, org, _identity_id, _browser_session = _auth(request)
        if not uid:
            return JSONResponse({"error": "Authentication required"}, status_code=401)
        return JSONResponse({"userId": uid, "organizationId": org})

    async def health(request: Request):
        return JSONResponse({"status": "ok", "open_mode": open_mode})

    async def ready(request: Request):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1")).scalar_one()
            from cliniar_server.db import inspect

            if not inspect(engine).has_table("schema_revision"):
                return JSONResponse({"status": "unavailable", "reason": "schema_outdated"}, status_code=503)
            if not schema_is_current(engine):
                return JSONResponse(
                    {"status": "unavailable", "reason": "schema_outdated"},
                    status_code=503,
                )
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    async def web_index(request: Request):
        index = _WEB_ROOT / "index.html"
        if not index.is_file():
            return JSONResponse({"error": "Web UI has not been built"}, status_code=503)
        return FileResponse(index)

    async def web_asset(request: Request):
        name = request.path_params["name"]
        asset = (_WEB_ROOT / "assets" / name).resolve()
        if _WEB_ROOT not in asset.parents or not asset.is_file():
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(asset)

    async def web_fallback(request: Request):
        index = _WEB_ROOT / "index.html"
        if not index.is_file():
            return JSONResponse({"error": "Web UI has not been built"}, status_code=503)
        return FileResponse(index)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal mcp_asgi
        # Each TestClient/server lifespan gets a fresh SDK session manager;
        # the SDK manager is intentionally single-use after shutdown.
        from cliniar_server.mcp_oauth import HojaOAuthProvider, build_server

        runtime_server = build_server(
            HojaOAuthProvider(engine, store, trusted_proxy=trust_proxy), public_host=public_host
        )
        runtime_app = runtime_server.streamable_http_app(
            streamable_http_path="/", stateless_http=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[public_host.strip(), public_host.strip() + ":*"],
            ),
        )
        mcp_asgi = runtime_app
        async with runtime_app.router.lifespan_context(runtime_app):
            if agent_dbos_url := os.environ.get("CLINIAR_AGENT_DBOS_URL"):
                from dbos import DBOS

                DBOS(config={"name": "cliniar-agent", "system_database_url": agent_dbos_url})
                DBOS.launch()
                try:
                    yield
                finally:
                    DBOS.destroy()
            else:
                yield

    app = Starlette(
        routes=[
            Route("/graphql", graphql_server, methods=["POST", "GET"]),
            WebSocketRoute("/ws", realtime_socket),
            Route("/auth/setup", auth_setup, methods=["GET", "POST"]),
            Route("/auth/invite/accept", auth_invite_accept, methods=["POST"]),
            Route("/onboarding/status", onboarding_status, methods=["GET"]),
            Route("/onboarding/bootstrap", onboarding_bootstrap, methods=["POST"]),
            Route("/auth/login", auth_login, methods=["POST"]),
            Route("/auth/workspaces", auth_workspaces, methods=["POST"]),
            Route("/auth/logout", auth_logout, methods=["POST"]),
            Route("/auth/me", auth_me, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Route("/ready", ready, methods=["GET"]),
            Route(
                "/.well-known/oauth-authorization-server/mcp",
                authorization_metadata_endpoint,
                methods=["GET", "OPTIONS"],
            ),
            Route(
                "/.well-known/oauth-protected-resource/mcp",
                resource_metadata_endpoint,
                methods=["GET", "OPTIONS"],
            ),
            Mount("/mcp", app=mcp_endpoint),
            Route("/", web_index, methods=["GET"]),
            Route("/assets/{name:path}", web_asset, methods=["GET"]),
            Route("/{path:path}", web_fallback, methods=["GET"]),
        ],
        lifespan=lifespan,
    )
    return app
