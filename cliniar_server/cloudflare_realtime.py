"""Cloudflare Realtime (SFU + TURN) HTTP client.

Media flows browser <-> Cloudflare; this server only signs and authorizes the control calls,
so the SFU app secret and TURN key never reach a browser.

Configuration (environment):
    CLINIAR_CF_REALTIME_APP_ID, CLINIAR_CF_REALTIME_APP_SECRET   SFU application
    CLINIAR_CF_TURN_KEY_ID, CLINIAR_CF_TURN_KEY_API_TOKEN        TURN key (optional)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

SFU_BASE = "https://rtc.live.cloudflare.com/v1"
ICE_TTL_SECONDS = 3600
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class SFUError(Exception):
    """Cloudflare rejected or failed a control call."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


class CloudflareRealtime:
    def __init__(self, app_id: str, app_secret: str, turn_key_id: str | None = None,
                 turn_key_token: str | None = None, base_url: str = SFU_BASE,
                 client: httpx.AsyncClient | None = None) -> None:
        self.app_id = app_id
        self._secret = app_secret
        self._turn_id = turn_key_id
        self._turn_token = turn_key_token
        self._base = base_url.rstrip("/")
        self._client = client

    @classmethod
    def from_env(cls) -> CloudflareRealtime | None:
        app_id = os.environ.get("CLINIAR_CF_REALTIME_APP_ID", "").strip()
        secret = os.environ.get("CLINIAR_CF_REALTIME_APP_SECRET", "").strip()
        if not app_id or not secret:
            return None
        return cls(app_id, secret, os.environ.get("CLINIAR_CF_TURN_KEY_ID", "").strip() or None,
                   os.environ.get("CLINIAR_CF_TURN_KEY_API_TOKEN", "").strip() or None)

    @property
    def turn_configured(self) -> bool:
        return bool(self._turn_id and self._turn_token)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _call(self, method: str, url: str, token: str, body: Any = None) -> dict:
        try:
            resp = await self._http().request(
                method, url, headers={"Authorization": f"Bearer {token}"},
                **({"json": body} if body is not None else {}))
        except httpx.HTTPError as exc:
            raise SFUError(f"Realtime service unreachable ({type(exc).__name__}).", 502) from exc
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            detail = data.get("errorDescription") or data.get("error") or f"HTTP {resp.status_code}"
            raise SFUError(f"Realtime service rejected the request: {detail}",
                           429 if resp.status_code == 429 else 502)
        return data if isinstance(data, dict) else {}

    def _sfu(self, path: str) -> str:
        return f"{self._base}/apps/{self.app_id}{path}"

    async def new_session(self) -> dict:
        return await self._call("POST", self._sfu("/sessions/new"), self._secret)

    async def add_tracks(self, session_id: str, body: dict) -> dict:
        return await self._call("POST", self._sfu(f"/sessions/{session_id}/tracks/new"), self._secret, body)

    async def update_tracks(self, session_id: str, body: dict) -> dict:
        return await self._call("PUT", self._sfu(f"/sessions/{session_id}/tracks/update"), self._secret, body)

    async def close_tracks(self, session_id: str, body: dict) -> dict:
        return await self._call("PUT", self._sfu(f"/sessions/{session_id}/tracks/close"), self._secret, body)

    async def renegotiate(self, session_id: str, body: dict) -> dict:
        return await self._call("PUT", self._sfu(f"/sessions/{session_id}/renegotiate"), self._secret, body)

    async def ice_servers(self, ttl: int = ICE_TTL_SECONDS) -> list[dict]:
        """Short-lived STUN/TURN credentials; free STUN only when no TURN key is configured."""
        if not self.turn_configured:
            return [{"urls": ["stun:stun.cloudflare.com:3478"]}]
        url = f"{self._base}/turn/keys/{self._turn_id}/credentials/generate-ice-servers"
        data = await self._call("POST", url, self._turn_token or "", {"ttl": ttl})
        servers = data.get("iceServers")
        if isinstance(servers, dict):  # older responses wrap a single object
            servers = [servers]
        if not isinstance(servers, list) or not servers:
            raise SFUError("Realtime service returned no ICE servers.")
        return servers
