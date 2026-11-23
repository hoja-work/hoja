"""Tests for visual decisions, media uploads with TTL, and one-shot review requests."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from hoja_server import uploads
from hoja_server.app import create_app
from hoja_server.db import make_engine, migrate, seed_tenant

ADMIN_TOKEN = "visual-decisions-admin-token"
OTHER_TOKEN = "visual-decisions-other-token"

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00"
    b"\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")

CREATE_TASK = """
mutation($input: TaskCreateInput!) {
  taskCreate(input: $input) {
    success
    task { id identifier title state { id name } decisionNeeded }
  }
}
"""

UPLOAD_MEDIA = """
mutation($filename: String!, $contentType: String, $dataBase64: String!, $ttlDays: Int, $issueId: String, $title: String) {
  uploadMedia(filename: $filename, contentType: $contentType, dataBase64: $dataBase64, ttlDays: $ttlDays, issueId: $issueId, title: $title) {
    success
    url
    filename
    contentType
    size
    expiresAt
    attachment { id url title contentType }
  }
}
"""

TASK_DECISION_CREATE = """
mutation($input: TaskDecisionCreateInput!) {
  taskDecisionCreate(input: $input) {
    success
    decision {
      id
      question
      options
      typedOptions { key label description mediaUrl mediaType }
      context
      status
      askedByAgent
    }
  }
}
"""

TASK_REVIEW_REQUEST = """
mutation($input: ReviewRequestInput!) {
  taskReviewRequest(input: $input) {
    success
    attachmentUrls
    issue {
      id
      identifier
      decisionNeeded
      state { id name }
      comments(first: 10) { nodes { id body } }
      attachments(first: 10) { nodes { id url title contentType } }
    }
    decision {
      id
      question
      context
      status
      options
      typedOptions { key label description mediaUrl mediaType }
      askedByAgent
    }
  }
}
"""


@pytest.fixture()
def world(tmp_path, monkeypatch):
    import hoja_server.db as db_module

    monkeypatch.setattr(db_module, "default_db_dir", lambda: tmp_path)
    db_path = tmp_path / "visual_decisions.db"
    engine = make_engine(db_path)
    migrate(engine)
    seeded = seed_tenant(
        engine,
        org_name="VisualOrg",
        org_url_key="visual",
        team_key="ENG",
        user_email="admin@visual.test",
        token=ADMIN_TOKEN,
        demo_issues=False,
    )
    other = seed_tenant(
        engine,
        org_name="OtherOrg",
        org_url_key="other-visual",
        team_key="OTH",
        user_email="other@visual.test",
        token=OTHER_TOKEN,
        demo_issues=False,
    )
    app = create_app(str(db_path), open_mode=False)
    return {
        "engine": engine,
        "app": app,
        "org_id": seeded["org_id"],
        "user_id": seeded["user_id"],
        "team_id": seeded["team_id"],
        "other_org_id": other["org_id"],
        "dir": tmp_path,
    }


def _gql(client: TestClient, token: str, query: str, variables: dict | None = None) -> dict:
    return client.post(
        "/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "variables": variables or {}},
    ).json()


def test_uploads_ttl_expiry_410_prune_and_org_upload_bytes(world) -> None:
    org_id = world["org_id"]
    other_org_id = world["other_org_id"]

    # Save an expiring blob (0 days -> expires immediately) and a 30-day blob.
    expired_url, _ = uploads.save_blob(
        org_id, "expired.png", "image/png", PNG_BYTES, ttl_days=0
    )
    live_url, _ = uploads.save_blob(
        org_id, "live.png", "image/png", PNG_BYTES, ttl_days=30
    )
    uploads.save_blob(other_org_id, "other.png", "image/png", PNG_BYTES, ttl_days=14)

    assert uploads.org_upload_bytes(org_id) == len(PNG_BYTES) * 2
    assert uploads.org_upload_bytes(other_org_id) == len(PNG_BYTES)

    with TestClient(world["app"]) as client:
        resp_expired = client.get(
            expired_url, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
        )
        assert resp_expired.status_code == 410, resp_expired.text

        resp_live = client.get(
            live_url, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
        )
        assert resp_live.status_code == 200
        assert resp_live.content == PNG_BYTES

    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    pruned = uploads.prune_expired_blobs(now=future)
    assert pruned == 1
    assert uploads.org_upload_bytes(org_id) == len(PNG_BYTES)


def test_upload_media_mutation_attaches_and_rejects_invalid_content_type(world) -> None:
    with TestClient(world["app"]) as client:
        created = _gql(
            client,
            ADMIN_TOKEN,
            CREATE_TASK,
            {"input": {"teamId": "ENG", "title": "Screenshot target"}},
        )
        assert "errors" not in created, created
        task = created["data"]["taskCreate"]["task"]

        res = _gql(
            client,
            ADMIN_TOKEN,
            UPLOAD_MEDIA,
            {
                "filename": "design-option.png",
                "contentType": "image/png",
                "dataBase64": PNG_B64,
                "ttlDays": 14,
                "issueId": task["identifier"],
                "title": "Design Option A",
            },
        )
        assert "errors" not in res, res
        payload = res["data"]["uploadMedia"]
        assert payload["success"] is True
        assert payload["url"].startswith("/api/uploads/")
        assert payload["url"].endswith(".png")
        assert payload["contentType"] == "image/png"
        assert payload["size"] == len(PNG_BYTES)
        assert payload["expiresAt"] is not None
        assert payload["attachment"]["title"] == "Design Option A"
        assert payload["attachment"]["url"] == payload["url"]

        # Reject disallowed HTML uploads.
        bad = _gql(
            client,
            ADMIN_TOKEN,
            UPLOAD_MEDIA,
            {
                "filename": "exploit.html",
                "contentType": "text/html",
                "dataBase64": base64.b64encode(b"<script>alert(1)</script>").decode("ascii"),
            },
        )
        assert bad.get("errors"), bad
        assert "not allowed" in bad["errors"][0]["message"]


def test_visual_decision_supports_up_to_20_options_and_rejects_javascript_urls(world) -> None:
    with TestClient(world["app"]) as client:
        created = _gql(
            client,
            ADMIN_TOKEN,
            CREATE_TASK,
            {"input": {"teamId": "ENG", "title": "Pick visual direction"}},
        )
        task = created["data"]["taskCreate"]["task"]

        options = [
            {
                "key": f"opt-{i}",
                "label": f"Layout variation {i}",
                "description": f"Variation number {i}",
                "mediaUrl": f"/api/uploads/mockblob{i:02d}.png"
                if i % 2 == 0
                else f"https://example.test/video-{i}.mp4",
            }
            for i in range(1, 19)
        ]
        res = _gql(
            client,
            ADMIN_TOKEN,
            TASK_DECISION_CREATE,
            {
                "input": {
                    "issueId": task["identifier"],
                    "question": "Which of these 18 layout variations should we ship?",
                    "options": options,
                    "agent": "designer:v1",
                }
            },
        )
        assert "errors" not in res, res
        decision = res["data"]["taskDecisionCreate"]["decision"]
        assert len(decision["options"]) == 18
        assert len(decision["typedOptions"]) == 18
        assert decision["typedOptions"][0]["mediaType"] == "video"
        assert decision["typedOptions"][1]["mediaType"] == "image"

        # Reject javascript: scheme in mediaUrl.
        xss = _gql(
            client,
            ADMIN_TOKEN,
            TASK_DECISION_CREATE,
            {
                "input": {
                    "issueId": task["identifier"],
                    "question": "Bad media URL?",
                    "options": [
                        {
                            "label": "Malicious",
                            "mediaUrl": "javascript:alert(document.cookie)",
                        }
                    ],
                }
            },
        )
        assert xss.get("errors"), xss
        assert "mediaUrl" in xss["errors"][0]["message"]


def test_task_review_request_end_to_end_and_cross_tenant_isolation(world) -> None:
    with TestClient(world["app"]) as client:
        created = _gql(
            client,
            ADMIN_TOKEN,
            CREATE_TASK,
            {"input": {"teamId": "ENG", "title": "Landing hero redesign"}},
        )
        task = created["data"]["taskCreate"]["task"]

        review = _gql(
            client,
            ADMIN_TOKEN,
            TASK_REVIEW_REQUEST,
            {
                "input": {
                    "issueId": task["identifier"],
                    "summary": "Implemented 3 hero variations and captured responsive screenshots.",
                    "artifacts": [
                        {
                            "title": "Hero Variation A",
                            "filename": "hero-a.png",
                            "contentType": "image/png",
                            "dataBase64": PNG_B64,
                            "ttlDays": 30,
                        },
                        {
                            "title": "Preview deployment",
                            "url": "https://preview.example.test/hero",
                        },
                    ],
                    "question": "Which hero variation should we launch?",
                    "options": [
                        {
                            "key": "hero-a",
                            "label": "Hero A (Minimal)",
                            "description": "Clean typography with subtle border",
                            "mediaUrl": "https://preview.example.test/hero-a.png",
                        },
                        {
                            "key": "hero-b",
                            "label": "Hero B (Split)",
                            "description": "Two-column product preview",
                            "mediaUrl": "https://preview.example.test/hero-b.mp4",
                        },
                    ],
                    "moveToInReview": True,
                    "agent": "ui-agent:run-42",
                }
            },
        )
        assert "errors" not in review, review
        payload = review["data"]["taskReviewRequest"]
        assert payload["success"] is True
        assert len(payload["attachmentUrls"]) == 2
        assert payload["attachmentUrls"][0].startswith("/api/uploads/")
        assert payload["issue"]["state"]["name"] == "In Review"
        assert payload["issue"]["decisionNeeded"] is True
        assert any(
            "Implemented 3 hero variations" in c["body"]
            for c in payload["issue"]["comments"]["nodes"]
        )
        assert payload["decision"]["question"] == "Which hero variation should we launch?"
        assert [o["mediaType"] for o in payload["decision"]["typedOptions"]] == [
            "image",
            "video",
        ]

        # Cross-tenant isolation: OTHER_TOKEN cannot read the uploaded blob or review-request the task.
        uploaded_url = payload["attachmentUrls"][0]
        cross_blob = client.get(
            uploaded_url, headers={"Authorization": f"Bearer {OTHER_TOKEN}"}
        )
        assert cross_blob.status_code == 404

        cross_review = _gql(
            client,
            OTHER_TOKEN,
            TASK_REVIEW_REQUEST,
            {
                "input": {
                    "issueId": task["id"],
                    "summary": "Cross-tenant attempt",
                }
            },
        )
        assert cross_review.get("errors"), cross_review
        assert cross_review["errors"][0]["extensions"]["code"] == "NOT_FOUND"


def test_mcp_registers_upload_media_and_task_review_request() -> None:
    from hoja_server.mcp_tools import build_tools
    from hoja_server.resolvers import build_schema

    tools = {t.name: t for t in build_tools(build_schema(), lambda write: {})}
    assert "hoja_uploadMedia" in tools
    assert "hoja_taskReviewRequest" in tools
    assert "hoja_issueReviewRequest" in tools

    upload_props = tools["hoja_uploadMedia"].parameters["properties"]
    assert {"filename", "dataBase64", "contentType", "ttlDays", "issueId"} <= set(upload_props)

    review_props = tools["hoja_taskReviewRequest"].parameters["properties"]
    assert {"issueId", "summary", "artifacts", "question", "options", "moveToInReview"} <= set(
        review_props
    )
