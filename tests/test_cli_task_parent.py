"""CLI contracts for parent/child attribution.

The CLI is the surface agents actually use, so these tests drive the real
``hoja`` commands through Typer's runner against a real uvicorn process and a
disposable SQLite database, then read the result back over GraphQL. They cover
the measured gap from HOJA-181: ``issue update --parent`` did not exist, and
neither did a parent filter on ``issue list``.

Checked here:

- ``issue update --parent`` sets, changes, and (via ``--no-parent`` or an empty
  value) clears a parent;
- a cycle (self-parent, or re-parenting under a descendant) is refused with a
  message naming both issues, and nothing is written;
- an unknown parent is refused precisely instead of surfacing the server's bare
  ``Invalid reference``;
- a parent from another team is refused with both team keys;
- ``issue list --parent`` returns exactly the children, ``--no-parent`` returns
  only orphans, and the two flags are mutually exclusive.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")
uvicorn = pytest.importorskip("uvicorn")
httpx = pytest.importorskip("httpx")

from typer.testing import CliRunner  # noqa: E402

from hoja.cli import typer_app  # noqa: E402
from hoja.errors import NotFoundError, UsageError  # noqa: E402
from hoja_server.app import create_app  # noqa: E402
from hoja_server.db import make_engine, migrate, seed_tenant  # noqa: E402

ONE_ISSUE = """
query($id: String!) {
  issue(id: $id) {
    id identifier title
    parent { id identifier }
    children(first: 50) { nodes { id identifier } }
  }
}
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Cli:
    """Invoke the real CLI command tree and read the server back."""

    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token
        self.runner = CliRunner()

    def invoke(self, *args: str):
        return self.runner.invoke(typer_app, list(args))

    def ok(self, *args: str) -> str:
        result = self.invoke(*args)
        assert result.exception is None, (result.output, result.exception)
        assert result.exit_code == 0, result.output
        return result.output

    def json(self, *args: str):
        return json.loads(self.ok("-o", "json", *args))

    def rejected(self, expected: type[Exception], *args: str) -> str:
        result = self.invoke(*args)
        assert isinstance(result.exception, expected), (
            f"expected {expected.__name__}, got {result.exception!r}\n{result.output}"
        )
        return str(result.exception)

    def gql(self, query: str, variables: dict | None = None) -> dict:
        response = httpx.post(
            self.url,
            headers={"Authorization": f"Bearer {self.token}"},
            json={"query": query, "variables": variables or {}},
            timeout=10,
        )
        body = response.json()
        assert "errors" not in body, body
        return body["data"]

    def issue(self, identifier: str) -> dict:
        return self.gql(ONE_ISSUE, {"id": identifier})["issue"]

    def create(self, title: str, *extra: str) -> dict:
        return self.json("issue", "create", "--team", "ENG", "--title", title, *extra)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    db = tmp_path / "cli-parent.db"
    engine = make_engine(db)
    migrate(engine)
    seeded = seed_tenant(
        engine, org_name="CliParents", org_url_key="cli-parents", team_key="ENG",
        user_name="Cli Admin", user_email="cli-admin@example.test", demo_issues=False,
    )

    port = _free_port()
    app = create_app(db_path=str(db))
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    # The lifespan must run before requests arrive; Server.run owns it, so the
    # thread starts it and the test waits for the ``started`` flag.
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        assert thread.is_alive(), "uvicorn exited before it started"
        time.sleep(0.02)
    assert server.started, "uvicorn did not start"

    url = f"http://127.0.0.1:{port}/graphql"
    monkeypatch.setenv("HOJA_CONFIG", str(tmp_path / "no-such-config.toml"))
    monkeypatch.delenv("LINEAR_TOKEN", raising=False)
    monkeypatch.delenv("LINEAR_API_URL", raising=False)
    monkeypatch.setenv("HOJA_TOKEN", seeded["token"])
    monkeypatch.setenv("HOJA_API_URL", url)
    try:
        yield Cli(url, seeded["token"])
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_cli_update_parent_sets_changes_and_clears(cli) -> None:
    first = cli.create("First parent")
    second = cli.create("Second parent")

    child = cli.create("Child step", "--parent", first["identifier"])
    assert child["parent"]["identifier"] == first["identifier"]
    assert [node["identifier"] for node in cli.issue(first["identifier"])["children"]["nodes"]] == [
        child["identifier"]
    ]

    # Change the parent. Identifiers and UUIDs are both accepted.
    moved = cli.json("issue", "update", child["identifier"], "--parent", second["identifier"])
    assert moved["parent"]["identifier"] == second["identifier"]
    assert cli.issue(first["identifier"])["children"]["nodes"] == []
    assert [node["identifier"] for node in cli.issue(second["identifier"])["children"]["nodes"]] == [
        child["identifier"]
    ]

    # --no-parent detaches...
    detached = cli.json("issue", "update", child["id"], "--no-parent")
    assert "parent" not in detached, detached
    assert cli.issue(child["identifier"])["parent"] is None
    assert cli.issue(second["identifier"])["children"]["nodes"] == []

    # ...and an explicit empty value does the same, for symmetry with --parent.
    relinked = cli.json("issue", "update", child["id"], "--parent", second["identifier"])
    assert relinked["parent"]["identifier"] == second["identifier"]
    cleared = cli.json("issue", "update", child["id"], "--parent", "")
    assert "parent" not in cleared, cleared
    assert cli.issue(child["identifier"])["parent"] is None

    # Passing neither flag must leave an existing parent alone.
    adopted = cli.create("Adopted step", "--parent", first["id"])
    renamed = cli.json("issue", "update", adopted["id"], "--title", "Renamed step")
    assert renamed["title"] == "Renamed step"
    assert renamed["parent"]["identifier"] == first["identifier"]


def test_cli_update_parent_refuses_unknown_parent(cli) -> None:
    issue = cli.create("Orphan to be parented")

    message = cli.rejected(NotFoundError, "issue", "update", issue["identifier"],
                           "--parent", "ENG-9999")
    assert "ENG-9999" in message
    assert "No issue" in message

    absent = "00000000-0000-4000-8000-000000000000"
    message = cli.rejected(NotFoundError, "issue", "update", issue["identifier"],
                           "--parent", absent)
    assert absent in message
    assert cli.issue(issue["identifier"])["parent"] is None


def test_cli_update_parent_refuses_cycles(cli) -> None:
    root = cli.create("Cycle root")
    child = cli.create("Cycle child", "--parent", root["identifier"])
    grandchild = cli.create("Cycle grandchild", "--parent", child["identifier"])

    message = cli.rejected(UsageError, "issue", "update", root["identifier"],
                           "--parent", grandchild["identifier"])
    assert "cycle" in message
    assert grandchild["identifier"] in message and root["identifier"] in message
    assert cli.issue(root["identifier"])["parent"] is None
    assert [n["identifier"] for n in cli.issue(child["identifier"])["children"]["nodes"]] == [
        grandchild["identifier"]
    ]

    message = cli.rejected(UsageError, "issue", "update", child["identifier"],
                           "--parent", child["identifier"])
    assert "own parent" in message and "ancestor" in message
    assert cli.issue(child["identifier"])["parent"]["identifier"] == root["identifier"]


def test_cli_parent_must_be_in_the_same_team(cli) -> None:
    cli.gql('mutation { teamCreate(input: {name: "Ops", key: "OPS"}) { success } }')
    foreign = cli.gql(
        'mutation($input: IssueCreateInput!) { issueCreate(input: $input) '
        "{ success issue { id identifier } } }",
        {"input": {"teamId": "OPS", "title": "Foreign root"}},
    )["issueCreate"]["issue"]
    local = cli.create("Local issue")

    message = cli.rejected(UsageError, "issue", "update", local["identifier"],
                           "--parent", foreign["identifier"])
    assert "OPS" in message and "ENG" in message
    assert cli.issue(local["identifier"])["parent"] is None

    message = cli.rejected(UsageError, "issue", "create", "--team", "ENG",
                           "--title", "Cross-team child", "--parent", foreign["identifier"])
    assert "OPS" in message and "ENG" in message


def test_cli_list_filters_by_parent_and_orphans(cli) -> None:
    parent = cli.create("List parent")
    first = cli.create("List child one", "--parent", parent["identifier"])
    second = cli.create("List child two", "--parent", parent["identifier"])
    orphan = cli.create("List orphan")

    children = cli.json("issue", "list", "--parent", parent["identifier"])
    assert {item["identifier"] for item in children} == {first["identifier"], second["identifier"]}

    # A UUID resolves to the same set.
    by_id = cli.json("issue", "list", "--parent", parent["id"])
    assert {item["identifier"] for item in by_id} == {first["identifier"], second["identifier"]}

    orphans = cli.json("issue", "list", "--no-parent")
    identifiers = {item["identifier"] for item in orphans}
    assert orphan["identifier"] in identifiers
    assert parent["identifier"] in identifiers
    assert first["identifier"] not in identifiers
    assert second["identifier"] not in identifiers

    message = cli.rejected(UsageError, "issue", "list",
                           "--parent", parent["identifier"], "--no-parent")
    assert "mutually exclusive" in message

    message = cli.rejected(NotFoundError, "issue", "list", "--parent", "ENG-4242")
    assert "ENG-4242" in message
