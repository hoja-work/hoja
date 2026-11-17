"""`hoja issue similar` — ranking, gate exit codes, and paging (HOJA-208).

Two layers:

- the scorer is pure, so tokenising, stemming, weighting and the empty corpus
  are unit-tested without a server;
- the command is driven through Typer's runner against a real uvicorn process
  on a disposable SQLite database, because the contract that matters (a
  clearly-duplicate issue is the top match, and the pre-create gate exits
  non-zero) is the one a harness depends on.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")
uvicorn = pytest.importorskip("uvicorn")
httpx = pytest.importorskip("httpx")

from typer.testing import CliRunner  # noqa: E402

from hoja.cli import typer_app  # noqa: E402
from hoja.commands.task import _fetch_documents  # noqa: E402
from hoja.similarity import Corpus, Document, stem, tokenize  # noqa: E402
from hoja_server.app import create_app  # noqa: E402
from hoja_server.db import make_engine, migrate, seed_tenant  # noqa: E402

DUPLICATE_TITLE = "Sign-out does not let you choose a different Google account"
DUPLICATE_BODY = (
    "Signing out of a hosted workspace and clicking Continue to sign in signs the same "
    "person straight back in as the Google account they just left. There is no way to "
    "choose a different Google account."
)

# ---- scorer unit tests -----------------------------------------------------


def test_tokenize_drops_stop_words_and_short_tokens() -> None:
    assert tokenize("The sign-in is not working") == ["sign", "work"]


def test_stem_leaves_short_words_alone() -> None:
    # "used", "ping", "ring" and "need" must survive: stripping their tails
    # would stop them matching themselves.
    for word in ("used", "ping", "ring", "need"):
        assert stem(word) == word


def test_stem_collapses_plural_and_ing_forms() -> None:
    assert stem("accounts") == "account"
    assert stem("signing") == "sign"
    assert stem("stopped") == "stopp"
    assert tokenize("accounts account") == ["account", "account"]


def test_title_weighs_more_than_body() -> None:
    corpus = Corpus(
        [
            Document(identifier="A-1", title="sidebar collapse"),
            Document(identifier="A-2", title="unrelated", description="sidebar collapse sidebar"),
        ]
    )
    hits = corpus.rank("sidebar collapse")
    assert [hit.identifier for hit in hits] == ["A-1", "A-2"]
    assert hits[0].score > hits[1].score


def test_empty_corpus_and_empty_draft_rank_nothing() -> None:
    assert Corpus([]).rank("anything at all") == []
    corpus = Corpus([Document(identifier="A-1", title="sidebar collapse")])
    assert corpus.rank("   ") == []


def test_ranking_is_deterministic() -> None:
    corpus = Corpus(
        [
            Document(identifier="B-2", title="same words here"),
            Document(identifier="B-1", title="same words here"),
        ]
    )
    first = [hit.identifier for hit in corpus.rank("same words")]
    second = [hit.identifier for hit in corpus.rank("same words")]
    assert first == second == ["B-1", "B-2"]


def test_strength_penalises_terms_the_corpus_has_never_seen() -> None:
    corpus = Corpus([Document(identifier="C-1", title="google account sign")])
    known = corpus.rank("google account sign")[0]
    novel = corpus.rank("google account sign webauthn hardware key")[0]
    assert known.strength == 1.0
    assert novel.strength < known.strength


# ---- paging ----------------------------------------------------------------


class _FakeClient:
    """Returns fixed pages so paging can be tested without 250 real issues."""

    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.calls: list[dict] = []

    async def execute(self, query, variables, *, operation=None):
        self.calls.append(variables)
        index = len(self.calls) - 1
        page = self.pages[min(index, len(self.pages) - 1)]
        return {"issues": page}


def _node(identifier: str) -> dict:
    return {
        "id": identifier,
        "identifier": identifier,
        "title": f"issue {identifier}",
        "description": "body",
        "state": {"name": "Todo", "type": "unstarted"},
    }


def test_fetch_documents_walks_every_page() -> None:
    """A one-page implementation must fail this: 250 issues is the API ceiling."""
    client = _FakeClient(
        [
            {"nodes": [_node(f"A-{i}") for i in range(250)],
             "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"}},
            {"nodes": [_node(f"B-{i}") for i in range(7)],
             "pageInfo": {"hasNextPage": False, "endCursor": None}},
        ]
    )
    documents, pages, complete = _run(_fetch_documents(client, None))
    assert pages == 2
    assert complete is True
    assert len(documents) == 257
    assert client.calls[1]["after"] == "cursor-1"


def test_fetch_documents_reports_an_incomplete_scan() -> None:
    client = _FakeClient(
        [{"nodes": [_node("A-1")], "pageInfo": {"hasNextPage": True, "endCursor": ""}}]
    )
    documents, _pages, complete = _run(_fetch_documents(client, None))
    assert len(documents) == 1
    assert complete is False


def _run(coro):
    import asyncio

    # asyncio.run(), not get_event_loop(): the latter only works while no earlier
    # test in this process has already closed or unset the main thread's loop,
    # which made these two tests pass or fail depending on what ran before them.
    # Ordering is not a property a test should depend on, and under pytest-xdist
    # the order is a scheduling result.
    return asyncio.run(coro)


# ---- CLI against a real server --------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Cli:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token
        self.runner = CliRunner()

    def invoke(self, *args: str, stdin: str | None = None):
        return self.runner.invoke(typer_app, list(args), input=stdin)

    def ok(self, *args: str, stdin: str | None = None) -> str:
        result = self.invoke(*args, stdin=stdin)
        assert result.exception is None, (result.output, result.exception)
        assert result.exit_code == 0, result.output
        return result.output

    def json(self, *args: str, stdin: str | None = None):
        # CliRunner merges the diagnostic line written to stderr ("scanned N
        # issues") into the captured output; the payload starts at the first
        # bracket.
        output = self.ok("-o", "json", *args, stdin=stdin)
        start = min(i for i in (output.find("["), output.find("{")) if i != -1)
        return json.loads(output[start:])

    def create(self, title: str, description: str = "") -> dict:
        return self.json("issue", "create", "--team", "ENG", "--title", title,
                         "--description", description)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    db = tmp_path / "cli-similar.db"
    engine = make_engine(db)
    migrate(engine)
    seeded = seed_tenant(
        engine, org_name="CliSimilar", org_url_key="cli-similar", team_key="ENG",
        user_name="Cli Admin", user_email="cli-admin@example.test", demo_issues=False,
    )
    port = _free_port()
    app = create_app(db_path=str(db))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
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


def test_duplicate_is_the_top_match(cli) -> None:
    duplicate = cli.create(DUPLICATE_TITLE, DUPLICATE_BODY)
    cli.create("Dark mode toggle for the settings page", "The settings page needs a dark theme switch.")
    cli.create("CSV export button missing from the issue table", "I cannot export my filtered view.")

    rows = cli.json("issue", "similar",
                    "after I log out and click continue to sign in it signs me back "
                    "into the same google account")
    assert rows, "expected at least one candidate"
    assert rows[0]["identifier"] == duplicate["identifier"]
    assert rows[0]["coverage"] >= 0.5
    assert "google" in rows[0]["matchedTerms"]


def test_fail_on_duplicate_exit_codes(cli) -> None:
    cli.create(DUPLICATE_TITLE, DUPLICATE_BODY)
    duplicate = cli.invoke("issue", "similar",
                           "after I log out and click continue to sign in it signs me back "
                           "into the same google account",
                           "--fail-on-duplicate")
    assert duplicate.exit_code == 1, duplicate.output

    novel = cli.invoke("issue", "similar",
                       "keyboard shortcut to create a new issue from anywhere like cmd shift n",
                       "--fail-on-duplicate")
    assert novel.exit_code == 0, novel.output


def test_fail_on_duplicate_in_a_separate_process(cli, tmp_path) -> None:
    """The gate is for hooks, so prove the exit status from a real process."""
    cli.create(DUPLICATE_TITLE, DUPLICATE_BODY)
    env = dict(os.environ)
    env.update({"HOJA_TOKEN": cli.token, "HOJA_API_URL": cli.url,
                "HOJA_CONFIG": str(tmp_path / "no-such-config.toml")})
    proc = subprocess.run(
        [sys.executable, "-m", "hoja", "issue", "similar",
         "signs me back into the same google account after logging out",
         "--fail-on-duplicate"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert "duplicate likely" in proc.stderr


def test_draft_from_file_and_stdin(cli, tmp_path) -> None:
    cli.create(DUPLICATE_TITLE, DUPLICATE_BODY)
    draft = tmp_path / "draft.md"
    draft.write_text(DUPLICATE_BODY, encoding="utf-8")

    from_file = cli.json("issue", "similar", "--description-file", str(draft))
    assert from_file[0]["identifier"] == "ENG-1"

    from_stdin = cli.json("issue", "similar", stdin=DUPLICATE_BODY)
    assert from_stdin[0]["identifier"] == "ENG-1"


def test_missing_draft_is_a_usage_error(cli) -> None:
    result = cli.invoke("issue", "similar")
    assert result.exit_code != 0
    assert "No draft text" in str(result.exception) or "No draft text" in result.output


def test_empty_corpus_reports_nothing_instead_of_crashing(cli) -> None:
    rows = cli.json("issue", "similar", "anything at all")
    assert rows == []


def test_json_output_is_stable(cli) -> None:
    cli.create(DUPLICATE_TITLE, DUPLICATE_BODY)
    first = cli.ok("-o", "json", "issue", "similar", "google account sign out")
    second = cli.ok("-o", "json", "issue", "similar", "google account sign out")
    assert first == second
