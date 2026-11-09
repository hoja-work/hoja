"""Deployment configuration and the hard bounds shared by client, tools and docs.

Grafana has no app-side credential: the customer supplies their own instance URL and a Viewer
service-account token. The only deployment requirement is the sealing key that protects the stored
token, so `from_env` fails closed when it is absent. The bounds below are product limits, not
provider limits: the provisioning run measured reads at ~100 req/s and confirmed `/api/ds/query`
executes with the data source's own credentials, which is why every query is range- and
point-capped.
"""

from __future__ import annotations

import os

from grafana.errors import ConnectorConfigError

SECRET_ENV = "HOJA_SECRET_KEY"

# An annotation or query range wider than this is refused rather than silently clipped.
MAX_ANNOTATION_RANGE_DAYS = 30
MAX_QUERY_RANGE_HOURS = 24
# `/api/ds/query` is a data-exfiltration surface: the response is bounded before it reaches a model.
MAX_DATA_POINTS = 1000
DEFAULT_DATA_POINTS = 300
MAX_SEARCH_RESULTS = 100
MAX_ANNOTATIONS = 200
MAX_TEXT_CHARS = 2000
REQUEST_TIMEOUT_SECONDS = 15.0


class GrafanaConfig:
    """The connector's deployment configuration. Holds no provider secret."""

    def __init__(self, *, secret_key: str) -> None:
        self.secret_key = secret_key

    @classmethod
    def from_env(cls) -> GrafanaConfig:
        key = os.environ.get(SECRET_ENV, "").strip()
        if not key:
            raise ConnectorConfigError(
                f"{SECRET_ENV} must be set: it seals the stored Grafana token.",
                hint="Set the same value on the Hoja server that serves Settings and the connector MCP service.",
            )
        return cls(secret_key=key)
