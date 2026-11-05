"""Async GraphQL client for Hoja's Linear-compatible GraphQL API.

Design goals:
- Single httpx.AsyncClient with sensible defaults (HTTP/2, timeout, retries).
- Token never logged in plaintext (redacted in verbose mode).
- GraphQL errors lifted into typed exceptions.
- Rate limit awareness (X-RateLimit-* headers honored).
- Cursor-based pagination helper.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, TypeVar

import httpx
from pydantic import BaseModel

from hoja import __version__
from hoja.config import redact_token
from hoja.errors import (
    APIError,
    AuthError,
    NetworkError,
    RateLimitError,
    ValidationError,
)

logger = logging.getLogger("hoja.client")

# Endpoint used when no --api-url flag, $HOJA_API_URL (or deprecated
# $LINEAR_API_URL), or accounts.<name>.base_url is configured. Point the client
# at your own `hoja-serve` instance (e.g. http://127.0.0.1:8787/graphql) to stay
# fully self-hosted; this fallback is the public Linear-compatible API.
DEFAULT_API_URL = "https://api.linear.app/graphql"

T = TypeVar("T", bound=BaseModel)


class LinearClient:
    """Async GraphQL client for a Linear-compatible GraphQL API."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_API_URL,
        timeout: float = 30.0,
        verbose: bool = False,
    ) -> None:
        self._token = token
        self._base_url = base_url
        self._verbose = verbose
        self._http = httpx.AsyncClient(
            http2=False,  # httpx[http2] is a separate dep — keep deps minimal
            timeout=timeout,
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
                "User-Agent": f"hoja/{__version__} (+https://github.com/rinadelph/hoja)",
            },
        )

    async def __aenter__(self) -> "LinearClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Core request method
    # ------------------------------------------------------------------

    async def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        operation: str | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query/mutation. Returns the `data` portion.

        Raises:
            AuthError: 401
            RateLimitError: 429
            APIError: GraphQL errors in response
            NetworkError: transport-level failures
        """
        payload: dict[str, Any] = {"query": query}
        if variables:
            # Drop None values — Linear's API rejects explicit nulls in many inputs
            payload["variables"] = _strip_none(variables)
        if operation:
            payload["operationName"] = operation

        if self._verbose:
            logger.info(
                "POST %s [token=%s] op=%s vars=%s",
                self._base_url,
                redact_token(self._token),
                operation or "<anon>",
                json.dumps(payload.get("variables", {}), default=str)[:200],
            )

        try:
            resp = await self._http.post(self._base_url, json=payload)
        except httpx.TimeoutException as e:
            raise NetworkError(f"Request timed out: {e}") from e
        except httpx.RequestError as e:
            raise NetworkError(f"Network error: {e}") from e

        # Handle HTTP-level errors
        if resp.status_code == 401:
            raise AuthError(
                "Linear rejected the token (HTTP 401)",
                hint="Generate a new token at https://linear.app/settings/api",
            )
        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After")
            raise RateLimitError(
                "Linear API rate limit exceeded",
                retry_after=int(retry) if retry and retry.isdigit() else None,
            )
        if resp.status_code >= 500:
            raise APIError(
                f"Linear API returned HTTP {resp.status_code}",
                hint="This is usually transient — retry in a few seconds",
            )
        if resp.status_code >= 400:
            raise APIError(
                f"HTTP {resp.status_code}: {resp.text[:500]}",
            )

        try:
            body = resp.json()
        except json.JSONDecodeError as e:
            raise APIError(f"Non-JSON response from Linear: {e}") from e

        # GraphQL-level errors
        if errors := body.get("errors"):
            messages = "; ".join(e.get("message", "<unknown>") for e in errors)
            raise APIError(messages, errors=errors)

        data = body.get("data")
        if data is None:
            raise APIError("Linear returned empty data")
        return data

    # ------------------------------------------------------------------
    # Typed helpers
    # ------------------------------------------------------------------

    async def execute_as(
        self,
        model: type[T],
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        path: list[str] | None = None,
        operation: str | None = None,
    ) -> T:
        """Execute and validate the response against a Pydantic model.

        path: list of dict keys to drill down to before parsing.
            e.g. ["viewer"] turns {"viewer": {...}} into the viewer object.
        """
        data = await self.execute(query, variables, operation=operation)
        node: Any = data
        for key in path or []:
            if not isinstance(node, dict) or key not in node:
                raise ValidationError(
                    f"Expected key {key!r} in response path "
                    f"{'.'.join(path or [])}",
                )
            node = node[key]
        try:
            return model.model_validate(node)
        except Exception as e:
            raise ValidationError(
                f"Response did not match {model.__name__}: {e}",
            ) from e

    async def execute_list(
        self,
        model: type[T],
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        path: list[str],
        operation: str | None = None,
    ) -> list[T]:
        """Execute and validate a connection.nodes list response.

        path should end at the dict containing 'nodes'.
        """
        data = await self.execute(query, variables, operation=operation)
        node: Any = data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                raise ValidationError(
                    f"Expected key {key!r} at path {'.'.join(path)}",
                )
            node = node[key]
        if not isinstance(node, dict) or "nodes" not in node:
            raise ValidationError(
                "Expected a connection object with 'nodes' at path "
                f"{'.'.join(path)}"
            )
        try:
            return [model.model_validate(item) for item in node["nodes"]]
        except Exception as e:
            raise ValidationError(
                f"List items did not match {model.__name__}: {e}",
            ) from e

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def paginate(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        path: list[str],
        page_size: int = 50,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every node across all pages of a connection."""
        cursor: str | None = None
        while True:
            vars_with_cursor = {**variables, "first": page_size}
            if cursor:
                vars_with_cursor["after"] = cursor
            data = await self.execute(query, vars_with_cursor)
            node: Any = data
            for key in path:
                node = node[key]
            for item in node.get("nodes", []):
                yield item
            page_info = node.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively remove None values from a dict for GraphQL variables."""
    if not isinstance(d, dict):
        return d
    out: dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict):
            stripped = _strip_none(v)
            if stripped:
                out[k] = stripped
        elif isinstance(v, list):
            out[k] = [_strip_none(item) if isinstance(item, dict) else item for item in v]
        else:
            out[k] = v
    return out
