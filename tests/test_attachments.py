"""Attachment uploads: what a stored blob is, and what the client is told it is.

These cover the parts of the pipeline a broken render depends on: the URL keeps the
file's extension, the resolved media type is recorded on the attachment row, documents
people actually attach are accepted, and a served blob can never execute as a document
on the app origin (an SVG attachment used to run its script there).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server import uploads
from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

TOKEN = "attachment-probe-token"
CREATE_ISSUE = ("mutation($i: IssueCreateInput!) { issueCreate(input: $i) "
                "{ success issue { id identifier } } }")
CREATE_ATTACHMENT = (
    "mutation($i: AttachmentCreateInput!) { attachmentCreate(input: $i) "
    "{ success attachment { id url title contentType } } }")
ISSUE_DETAIL = ("query($id: String!) { issue(id: $id) "
                "{ attachments(first: 10) { nodes { id url title contentType } } } }")

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00"
       b"\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    import hoja_server.db as db_module

    monkeypatch.setattr(db_module, "default_db_dir", lambda: tmp_path)
    db_path = tmp_path / "attachments.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(engine, org_name="Attach", org_url_key="attach",
                         user_email="ada@attach.test", token=TOKEN, demo_issues=False)
    app = create_app(str(db_path), open_mode=False)
    with TestClient(app) as client:
        made = client.post("/graphql", headers={"Authorization": f"Bearer {TOKEN}"},
                           json={"query": CREATE_ISSUE,
                                 "variables": {"i": {"title": "Has files", "teamId": seeded["team_id"]}}})
        issue = made.json()["data"]["issueCreate"]["issue"]
    return {"engine": engine, "client_app": app, "org": seeded["org_id"], "issue": issue,
            "dir": tmp_path}


def _gql(client, query, variables=None):
    body = client.post("/graphql", headers={"Authorization": f"Bearer {TOKEN}"},
                       json={"query": query, "variables": variables or {}}).json()
    assert not body.get("errors"), body
    return body["data"]


def _upload(client, name, content_type, body):
    return client.post("/api/uploads", headers={"Authorization": f"Bearer {TOKEN}"},
                       files={"file": (name, body, content_type)})


def test_upload_url_keeps_the_extension(world) -> None:
    """A screenshot's URL must end in its extension: every renderer keys off it."""
    with TestClient(world["client_app"]) as client:
        r = _upload(client, "shot.png", "image/png", PNG)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"].startswith("/api/uploads/")
    assert body["url"].endswith(".png"), body
    assert body["contentType"] == "image/png"


def test_served_blob_answers_with_and_without_extension(world) -> None:
    """Both the current `<id>.png` and the legacy `<id>` form resolve to the bytes."""
    with TestClient(world["client_app"]) as client:
        url = _upload(client, "shot.png", "image/png", PNG).json()["url"]
        with_ext = client.get(url, headers={"Authorization": f"Bearer {TOKEN}"})
        legacy = client.get(url.rsplit(".", 1)[0], headers={"Authorization": f"Bearer {TOKEN}"})
    assert with_ext.status_code == 200, with_ext.text
    assert with_ext.headers["content-type"] == "image/png"
    assert legacy.status_code == 200
    assert legacy.content == with_ext.content


def test_documents_people_attach_are_accepted(world) -> None:
    """CSV, Markdown and JSON are documents, not errors; a .html file still is not."""
    cases = [("data.csv", "text/csv", b"id,name\n1,alpha\n"),
             ("notes.md", "text/markdown", b"# Notes\n"),
             # curl and older clients send octet-stream; the extension decides.
             ("notes.md", "application/octet-stream", b"# Notes\n"),
             ("payload.json", "application/json", b'{"a":1}')]
    with TestClient(world["client_app"]) as client:
        for name, ctype, body in cases:
            r = _upload(client, name, ctype, body)
            assert r.status_code == 200, (name, ctype, r.text)
        refused = _upload(client, "page.html", "text/html", b"<script>alert(1)</script>")
    assert refused.status_code == 415, refused.text


def test_svg_is_never_served_as_a_document(world) -> None:
    """An SVG attachment downloads; its script must not run on the app origin."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>document.title="pwned"</script></svg>'
    with TestClient(world["client_app"]) as client:
        url = _upload(client, "logo.svg", "image/svg+xml", svg).json()["url"]
        served = client.get(url, headers={"Authorization": f"Bearer {TOKEN}"})
    assert served.status_code == 200
    assert served.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in served.headers["content-security-policy"]
    assert served.headers["x-content-type-options"] == "nosniff"


def test_attachment_records_the_media_type_for_the_client(world) -> None:
    """`contentType` on the attachment is how the detail panel picks a renderer."""
    with TestClient(world["client_app"]) as client:
        url = _upload(client, "shot.png", "image/png", PNG).json()["url"]
        created = _gql(client, CREATE_ATTACHMENT,
                       {"i": {"issueId": world["issue"]["id"], "url": url, "title": "shot.png"}})["attachmentCreate"]
        assert created["success"] is True
        assert created["attachment"]["contentType"] == "image/png"
        nodes = _gql(client, ISSUE_DETAIL, {"id": world["issue"]["id"]})["issue"]["attachments"]["nodes"]
    assert [n["contentType"] for n in nodes] == ["image/png"]


def test_external_link_has_no_invented_type(world) -> None:
    """An external URL is not fetched, so its type stays unknown instead of guessed."""
    with TestClient(world["client_app"]) as client:
        created = _gql(client, CREATE_ATTACHMENT,
                       {"i": {"issueId": world["issue"]["id"],
                              "url": "https://example.test/design.png", "title": "design"}})["attachmentCreate"]
    assert created["success"] is True
    assert created["attachment"]["contentType"] is None


def test_oversize_upload_is_refused_before_it_touches_disk(world) -> None:
    with TestClient(world["client_app"]) as client:
        r = _upload(client, "huge.png", "image/png", b"\x00" * (uploads.MAX_BYTES + 1))
    assert r.status_code == 413, r.text
    assert not list((world["dir"] / "uploads").glob("*.png"))


def test_a_json_document_survives_its_own_sidecar(world) -> None:
    """Blobs live beside, not among, their metadata: a `.json` upload returns its bytes."""
    payload = b'{"id": 7, "note": "not the server\'s metadata"}'
    with TestClient(world["client_app"]) as client:
        url = _upload(client, "payload.json", "application/json", payload).json()["url"]
        served = client.get(url, headers={"Authorization": f"Bearer {TOKEN}"})
    assert served.status_code == 200, served.text
    assert served.content == payload
    assert served.headers["content-type"] == "application/json"


def test_a_blob_stored_in_the_older_layout_still_serves(world, tmp_path) -> None:
    """Files written before blobs moved to `uploads/blobs/` must keep working."""
    legacy_dir = world["dir"] / "uploads"
    legacy_dir.mkdir(exist_ok=True)
    file_id = "cafebabecafebabecafebabecafebabe"
    (legacy_dir / f"{file_id}.txt").write_text("old bytes")
    (legacy_dir / f"{file_id}.json").write_text(
        json.dumps({"org": world["org"], "filename": "old.txt",
                    "content_type": "text/plain", "size": 9}))
    with TestClient(world["client_app"]) as client:
        plain = client.get(f"/api/uploads/{file_id}", headers={"Authorization": f"Bearer {TOKEN}"})
        with_ext = client.get(f"/api/uploads/{file_id}.txt", headers={"Authorization": f"Bearer {TOKEN}"})
    assert plain.status_code == 200, plain.text
    assert with_ext.status_code == 200, with_ext.text
    assert plain.text == "old bytes"
