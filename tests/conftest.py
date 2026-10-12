"""Shared test configuration.

Session cookies are marked Secure except for plain-http localhost (the dev
exemption), so in-process TestClients default to http://localhost.
"""

from __future__ import annotations

try:
    from starlette import testclient as _testclient
except ImportError:  # pragma: no cover - backend extras not installed
    _testclient = None

if _testclient is not None:
    _original_init = _testclient.TestClient.__init__

    def _localhost_init(self, *args, base_url: str = "http://localhost", **kwargs):
        _original_init(self, *args, base_url=base_url, **kwargs)

    _testclient.TestClient.__init__ = _localhost_init
