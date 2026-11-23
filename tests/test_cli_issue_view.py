"""``hoja issue list --view``: the CLI runs a saved view on the server.

Driven through the real Typer command tree against a real uvicorn process and a
disposable SQLite database, like ``test_cli_task_parent.py``. Checked here:

- a view is found by name (case-insensitively), slug, or id;
- the rows are the server's ``View.issues`` result: the saved filter, the view's
  ordering, and paging past one server page when ``--limit`` asks for it;
- other list flags narrow the view (ANDed server-side), and a flag the view
  grammar cannot express is refused as a usage error instead of being dropped;
- an unknown view is ``NotFoundError``; two views with one name ask for the slug.
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
from hoja_server.db import (  # noqa: E402
    cycle,
    issue,
    make_engine,
    migrate,
    new_id,
    now_iso,
    seed_tenant,
    team,
)

CREATE_VIEW = """
mutation($input: ViewCreateInput!) {
  viewCreate(input: $input) { success view { id slugId name } }
}
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Cli:
    """Invoke the real CLI command tree and read the server back."""

    def __init__(self, url: str, token: str, engine=None, seeded: dict | None = None) -> None:
        self.url = url
        self.token = token
        self.engine = engine
        self.seeded = seeded or {}
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

    def view(self, name: str, filters: dict, display: dict | None = None,
             visibility: str = "workspace") -> dict:
        return self.gql(CREATE_VIEW, {"input": {
            "name": name, "kind": "issue", "visibility": visibility,
            "filters": filters, "display": display or {},
        }})["viewCreate"]["view"]

    def listed(self, *args: str) -> list[str]:
        return [row["title"] for row in self.json("issue", "list", *args)]

    def create(self, title: str, *extra: str) -> dict:
        return self.json("issue", "create", "--team", "ENG", "--title", title, *extra)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    db = tmp_path / "cli-views.db"
    engine = make_engine(db)
    migrate(engine)
    seeded = seed_tenant(
        engine, org_name="CliViews", org_url_key="cli-views", team_key="ENG",
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
        yield Cli(url, seeded["token"], engine, seeded)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_cli_list_view_by_name_slug_and_id(cli) -> None:
    cli.create("Urgent one", "--priority", "1")
    cli.create("Low one", "--priority", "4")
    cli.create("Urgent two", "--priority", "1")
    view = cli.view("Urgent tasks", {"priority": {"is": 1}},
                    {"ordering": "title"})

    by_name = cli.listed("--view", "urgent TASKS")
    assert by_name == ["Urgent one", "Urgent two"]  # the view's title ordering
    assert cli.listed("--view", view["slugId"]) == by_name
    assert cli.listed("--view", view["id"]) == by_name


def test_cli_list_view_narrows_and_pages(cli) -> None:
    for n in range(7):
        cli.create(f"Bug {n}", "--priority", "2")
    cli.create("Not a bug", "--priority", "3")
    cli.view("High", {"priority": {"is": 2}}, {"ordering": "title"})

    assert cli.listed("--view", "High", "--limit", "3") == [
        "Bug 0", "Bug 1", "Bug 2",
    ]
    assert len(cli.listed("--view", "High", "--limit", "100")) == 7
    # Another flag is ANDed with the saved filter on the server.
    assert cli.listed("--view", "High", "--contains", "Bug 5") == ["Bug 5"]
    assert cli.listed("--view", "High", "--priority", "3") == []


def test_cli_list_view_refuses_what_it_cannot_run(cli) -> None:
    cli.view("Everything", {})
    message = cli.rejected(UsageError, "issue", "list", "--view", "Everything",
                           "--assignee", "Some Name")
    assert "--view" in message
    message = cli.rejected(NotFoundError, "issue", "list", "--view", "No such view")
    assert "No such view" in message


def test_cli_list_view_ambiguous_name_asks_for_slug(cli) -> None:
    first = cli.view("Twins", {})
    second = cli.view("twins", {})
    message = cli.rejected(UsageError, "issue", "list", "--view", "Twins")
    assert "2 task views" in message
    assert cli.json("issue", "list", "--view", second["slugId"]) == []
    assert first["slugId"] != second["slugId"]


def test_cli_list_view_resolves_cycle_flags(cli) -> None:
    """--cycle current / next / part of a name select the cycles the plain list would."""
    org, team_id = cli.seeded["org_id"], cli.seeded["team_id"]
    now_cycle, next_cycle = new_id(), new_id()
    with cli.engine.begin() as conn:
        for cycle_id, number, name in ((now_cycle, 1, "Sprint Alpha"),
                                       (next_cycle, 2, "Sprint Beta")):
            conn.execute(cycle.insert().values(
                id=cycle_id, organization_id=org, team_id=team_id, number=number,
                name=name, created_at=now_iso(), updated_at=now_iso()))
        conn.execute(team.update().where(team.c.id == team_id)
                     .values(active_cycle_id=now_cycle))
    placed = {"In alpha": now_cycle, "In beta": next_cycle, "No cycle": None}
    for title, cycle_id in placed.items():
        created = cli.create(title, "--priority", "2")
        with cli.engine.begin() as conn:
            conn.execute(issue.update().where(issue.c.id == created["id"])
                         .values(cycle_id=cycle_id))
    cli.view("Highs", {"priority": {"is": 2}}, {"ordering": "title"})

    assert cli.listed("--view", "Highs", "--cycle", "current") == ["In alpha"]
    assert cli.listed("--view", "Highs", "--cycle", "next") == ["In beta"]
    assert cli.listed("--view", "Highs", "--cycle", "beta") == ["In beta"]
    assert cli.listed("--view", "Highs", "--cycle", now_cycle) == ["In alpha"]
    # The same flags without --view select the same rows.
    assert cli.listed("--cycle", "current") == ["In alpha"]
    assert cli.listed("--view", "Highs", "--cycle", "No such sprint") == []


UUID = "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize("flags", [
    {"team": "ENG"}, {"team": UUID}, {"state": "Todo,In Progress"}, {"not_state": "Done"},
    {"state_type": "started"}, {"assignee": "me", "viewer_id": UUID},
    {"assignee": "a@example.test"}, {"creator": UUID}, {"project": UUID}, {"cycle": UUID},
    {"parent": UUID}, {"no_parent": True}, {"label": "Bug,UI"}, {"priority": 2},
    {"contains": "auth"}, {"updated_after": "-P7D", "updated_before": "2026-01-01"},
    {"due_before": "2026-01-01", "due_after": "2025-01-01"},
])
def test_list_flags_translate_into_the_view_grammar(flags) -> None:
    """Every flag --view accepts survives the server's Linear-to-view translation."""
    from hoja.filters import build_issue_filter
    from hoja_server import view_linear
    from hoja_server import views as viewdocs

    translated = view_linear.to_hoja("issue", build_issue_filter(**flags))
    assert translated
    assert viewdocs.parse_filters("issue", translated) == translated


@pytest.mark.parametrize("flags", [
    {"cycle": "current"}, {"cycle": "next"}, {"cycle": "Sprint 4"},
    {"assignee": "Some Name"},
])
def test_list_flags_the_view_grammar_refuses(flags) -> None:
    """Substring and "current cycle" flags are refused by path, never dropped."""
    from hoja.filters import build_issue_filter
    from hoja_server import view_linear
    from hoja_server import views as viewdocs

    with pytest.raises(viewdocs.ViewError):
        view_linear.to_hoja("issue", build_issue_filter(**flags))


def test_cli_list_view_with_no_parent_and_project(cli) -> None:
    parent = cli.create("Top task", "--priority", "2")
    cli.create("Nested task", "--priority", "2", "--parent", parent["identifier"])
    cli.view("Highs", {"priority": {"is": 2}}, {"ordering": "title"})
    assert cli.listed("--view", "Highs") == ["Nested task", "Top task"]
    assert cli.listed("--view", "Highs", "--no-parent") == ["Top task"]
    message = cli.rejected(NotFoundError, "issue", "list", "--view", "Highs",
                           "--project", "No such project")
    assert "No such project" in message
