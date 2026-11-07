"""Hoja — self-hosted, agent-native work management."""

from pathlib import Path

#: PyPI distribution that ships this package. The project cannot be named
#: ``hoja`` on PyPI (that name belongs to unrelated software), so it is
#: published as ``hoja-cli``. Anything that talks to PyPI — the ``hoja update``
#: command, install instructions, release tooling — must use this name. The
#: commands stay ``hoja``, ``hoja-mcp``, and ``hoja-serve``.
DISTRIBUTION_NAME = "hoja-cli"


def _read_version() -> str:
    """Read version from VERSION file at the repo root.

    Falls back to a hardcoded string if the file is missing (e.g. when
    installed from a wheel where VERSION isn't packaged).
    """
    try:
        version_file = Path(__file__).resolve().parent.parent / "VERSION"
        if version_file.is_file():
            return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return "0.7.0"


__version__ = _read_version()
__all__ = ["DISTRIBUTION_NAME", "__version__"]
