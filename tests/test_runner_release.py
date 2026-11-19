"""The runner daemon's wheel, served by the app it belongs to (HOJA-712).

The enroll screen used to print a command beginning with a bare ``hoja-runnerd``, on the
assumption that the daemon was on the operator's ``PATH``. It was on nobody's: the package
is not published anywhere, and the live run in ``artifacts/runs/hoja-602/live-runner``
had to install it editable from a checkout before the printed command could even fail for
a different reason. So the deployment now serves the artifact at
``/downloads/<wheel>``, and this file proves the three things that makes true:

=========================================================  ==============================
test                                                       acceptance
=========================================================  ==============================
the wheel is served without a session                      "one-liner install on a clean
                                                           machine", i.e. no credential
the pointer names the newest wheel, and the one-liner      the screen prints a version that
  names the same URL                                       can change without the screen
                                                           changing
only the wheels in the directory are reachable             a public unauthenticated route
                                                           reads nothing else
=========================================================  ==============================

No database and no tenant: these routes are deliberately outside the session and tenant
paths, so the fixture is a bare Starlette app holding exactly the routes ``create_app``
mounts. ``test_runner_enrollment_admin.py`` proves the same URL arrives at the screen.
"""
from __future__ import annotations

import zipfile

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from hoja_server import runner_release

WHEEL_NAME = "hoja_runnerd-9.9.9-py3-none-any.whl"


def _wheel(directory, name: str = WHEEL_NAME) -> str:
    """A file with the right name; it is never unpacked, only served."""
    path = directory / name
    path.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    return name


@pytest.fixture
def served(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    root.mkdir()
    _wheel(root)
    monkeypatch.setenv("HOJA_DOWNLOAD_ROOT", str(root))
    return root


@pytest.fixture
def client(served):
    return TestClient(Starlette(routes=runner_release.downloads_routes()))


def test_the_wheel_is_served_without_a_session(client, served):
    """No cookie, no bearer token, no tenant header: the installing machine has none yet."""
    response = client.get(f"/downloads/{WHEEL_NAME}")
    assert response.status_code == 200, response.text
    # An attachment, and a content type that cannot be sniffed into a document on the app
    # origin: this route is public, so what it serves must not be rendered as a page.
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == f'attachment; filename="{WHEEL_NAME}"'
    assert response.content == (served / WHEEL_NAME).read_bytes()


def test_install_commands_on_a_clean_machine(client, served):
    """The URL in the one-liner is the one the route answers, unauthenticated."""
    pointer = client.get("/downloads/hoja-runnerd/latest")
    assert pointer.status_code == 200, pointer.text
    body = pointer.json()
    assert body["version"] == "9.9.9"
    assert body["filename"] == WHEEL_NAME
    assert body["url"].endswith(f"/downloads/{WHEEL_NAME}")
    assert body["install"] == f"pipx install {body['url']}"
    assert body["requiresPython"] == runner_release.REQUIRES_PYTHON
    # The pointer is the one place the one-liner is composed, so the URL the screen prints
    # and the URL the pointer publishes cannot drift apart.
    assert client.get("/downloads/" + body["filename"]).content == \
        (served / WHEEL_NAME).read_bytes()


def test_the_pointer_names_the_newest_wheel_not_the_first_one_found(tmp_path, monkeypatch):
    """Text order would pick 0.9.1 over 0.10.0; the screen must not go backwards."""
    root = tmp_path / "downloads"
    root.mkdir()
    for version in ("0.9.1", "0.10.0", "0.9.10"):
        _wheel(root, f"hoja_runnerd-{version}-py3-none-any.whl")
    monkeypatch.setenv("HOJA_DOWNLOAD_ROOT", str(root))
    newest = runner_release.latest_wheel()
    assert newest.name == "hoja_runnerd-0.10.0-py3-none-any.whl"
    assert runner_release.wheel_version(newest) == "0.10.0"


def test_only_the_wheels_in_the_directory_are_reachable(client, served):
    """A public route: what it reads is a list read from disk, never a path from the request."""
    (served / "secret.txt").write_text("not an artifact")
    (served / "hoja_runnerd-9.9.9.tar.gz").write_bytes(b"not served")
    for path in ("/downloads/secret.txt", "/downloads/hoja_runnerd-9.9.9.tar.gz",
                 "/downloads/../pyproject.toml", "/downloads/hoja_runnerd-9.9.9-py3-none-any.whl.bak"):
        assert client.get(path).status_code == 404, path


def test_a_deployment_that_published_no_wheel_says_so(tmp_path, monkeypatch):
    """The null the enrollment mutation returns instead of a URL that would 404."""
    monkeypatch.setenv("HOJA_DOWNLOAD_ROOT", str(tmp_path / "never-built"))
    empty = TestClient(Starlette(routes=runner_release.downloads_routes()))
    assert empty.get("/downloads/hoja-runnerd/latest").status_code == 404
    assert runner_release.download_url("https://hoja.example") is None


def test_the_python_floor_is_the_one_the_package_declares():
    """Three copies of "3.11" would drift: pyproject, this module and the README."""
    import pathlib
    import re

    runner = pathlib.Path(__file__).resolve().parents[2] / "runner" / "pyproject.toml"
    declared = re.search(r'^requires-python\s*=\s*">=([0-9.]+)"',
                         runner.read_text(), re.MULTILINE)
    assert declared, "runner/pyproject.toml declares no requires-python"
    assert declared.group(1) == runner_release.REQUIRES_PYTHON


def test_a_built_wheel_carries_the_profiles_and_the_entry_point():
    """A wheel is the *whole* distribution: the TOMLs are package data, not checkout files.

    Skips when no wheel has been built here — `app/scripts/build-runner-wheel.sh` writes
    one into `app/downloads`, and a plain CI checkout has none. When one is present this
    reads the artifact rather than the source tree, which is the difference that matters:
    `--print-profiles` on an installed wheel is what an operator runs.
    """
    import pathlib

    app = pathlib.Path(__file__).resolve().parents[1]
    built = sorted((app / "downloads").glob("hoja_runnerd-*.whl"))
    if not built:
        pytest.skip("no wheel in app/downloads; build one with app/scripts/build-runner-wheel.sh")
    names = zipfile.ZipFile(built[-1]).namelist()
    for profile in ("pi", "claude-code", "codex", "gemini", "generic", "opencode"):
        assert f"hoja_runnerd/profiles/{profile}.toml" in names, names
    assert any(name.endswith(".dist-info/entry_points.txt") for name in names), names
    assert "hoja_client/client.py" in names, "hoja_client ships in the same distribution"
