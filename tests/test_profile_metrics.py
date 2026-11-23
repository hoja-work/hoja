"""Member profiles, daily activity heatmaps, and token-burner leaderboards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from hoja_server import logging_setup, usage_ledger
from hoja_server.app import create_app
from hoja_server.db import (
    agent_session,
    issue,
    issue_activity,
    new_id,
    seed_tenant,
    team_member,
    user,
)
from tests import rls_mode


def _post(client: TestClient, token: str, query: str, variables: dict | None = None) -> dict:
    return client.post(
        "/graphql",
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {token}"},
    ).json()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOJA_SECRET_KEY", "profile-metrics-secret")
    monkeypatch.setenv("HOJA_LEASE_REAPER", "0")
    monkeypatch.setenv("HOJA_WEBHOOK_WORKER", "0")
    logging_setup.forget_secrets()
    engine, kwargs = rls_mode.workspace(tmp_path, "profile-metrics")
    alpha = seed_tenant(
        engine,
        org_name="Alpha",
        org_url_key="alpha",
        user_email="luis@alpha.test",
        token="alpha-admin",
        demo_issues=True,
    )
    bravo = seed_tenant(
        engine,
        org_name="Bravo",
        org_url_key="bravo",
        team_key="BRV",
        user_email="other@bravo.test",
        token="bravo-admin",
        demo_issues=True,
    )
    app = create_app(
        kwargs.get("db_path"),
        database_url=kwargs.get("database_url"),
        open_mode=False,
    )
    yield {"engine": engine, "client": TestClient(app), "alpha": alpha, "bravo": bravo}
    logging_setup.forget_secrets()


def _create_member(world, *, name: str, email: str) -> tuple[str, str]:
    """Create a non-admin workspace member and return (user_id, api_token)."""
    alpha = world["alpha"]
    body = _post(
        world["client"],
        alpha["token"],
        """
        mutation Invite($input: MemberInviteInput!) {
          memberInvite(input: $input) { success user { id name email } }
        }
        """,
        {"input": {"name": name, "email": email, "teamIds": [alpha["team_id"]]}},
    )
    assert "errors" not in body, body
    uid = body["data"]["memberInvite"]["user"]["id"]
    from hoja_server.store import Store

    created = Store(world["engine"]).create_api_key(alpha["org_id"], uid, "member-key", "read_write")
    assert created is not None
    _row, raw_token = created
    return uid, raw_token


def _create_agent(world, *, name: str) -> str:
    alpha = world["alpha"]
    body = _post(
        world["client"],
        alpha["token"],
        """
        mutation AgentCreate($name: String!) {
          agentMemberCreate(input: { name: $name }) {
            success agentMember { id userId }
          }
        }
        """,
        {"name": name},
    )
    assert "errors" not in body, body
    return body["data"]["agentMemberCreate"]["agentMember"]["userId"]


def _seed_usage_and_activity(world):
    alpha = world["alpha"]
    engine = world["engine"]
    member_id, member_token = _create_member(world, name="Kevin", email="kevin@alpha.test")
    agent_id = _create_agent(world, name="Coder")

    now = datetime.now(timezone.utc)
    today = now.isoformat()
    yesterday = (now - timedelta(days=1)).isoformat()

    with engine.begin() as conn:
        issues = conn.execute(
            issue.select().where(issue.c.organization_id == alpha["org_id"])
        ).mappings().all()
        assert len(issues) >= 2
        i1 = dict(issues[0])
        i2 = dict(issues[1])

        # Assign i1 to admin (completed today), i2 to Kevin (still open).
        conn.execute(
            issue.update()
            .where(issue.c.id == i1["id"])
            .values(assignee_id=alpha["user_id"], delegate_id=agent_id, completed_at=today)
        )
        conn.execute(
            issue.update()
            .where(issue.c.id == i2["id"])
            .values(assignee_id=member_id, delegate_id=agent_id, completed_at=None)
        )

        # Two agent sessions: admin delegated i1 (heavy tokens), Kevin delegated i2 (lighter).
        s1 = new_id()
        s2 = new_id()
        conn.execute(
            agent_session.insert().values(
                id=s1,
                organization_id=alpha["org_id"],
                issue_id=i1["id"],
                agent_user_id=agent_id,
                creator_id=alpha["user_id"],
                status="complete",
                state="done",
                trigger="delegation",
                created_at=yesterday,
                updated_at=today,
                ended_at=today,
            )
        )
        conn.execute(
            agent_session.insert().values(
                id=s2,
                organization_id=alpha["org_id"],
                issue_id=i2["id"],
                agent_user_id=agent_id,
                creator_id=member_id,
                status="active",
                state="running",
                trigger="delegation",
                created_at=today,
                updated_at=today,
            )
        )

        usage_ledger.record_report(
            conn,
            organization_id=alpha["org_id"],
            run_id="run-heavy",
            agent_user_id=agent_id,
            session_id=s1,
            usage={
                "model": "anthropic/claude-sonnet-4-5",
                "input_tokens": 80_000,
                "output_tokens": 20_000,
                "cache_read_tokens": 50_000,
                "run_seconds": 600,
                "auth_mode": "byo-key",
                "compute": "customer",
            },
            at=yesterday,
        )
        usage_ledger.record_report(
            conn,
            organization_id=alpha["org_id"],
            run_id="run-light",
            agent_user_id=agent_id,
            session_id=s2,
            usage={
                "model": "openai/gpt-5-codex",
                "input_tokens": 10_000,
                "output_tokens": 5_000,
                "cache_read_tokens": 0,
                "run_seconds": 180,
                "auth_mode": "byo-key",
                "compute": "customer",
            },
            at=today,
        )

        # Activity rows for streak & recent feed.
        conn.execute(
            issue_activity.insert().values(
                id=new_id(),
                organization_id=alpha["org_id"],
                issue_id=i1["id"],
                actor_id=alpha["user_id"],
                event_type="stateChange",
                changes="{}",
                created_at=yesterday,
            )
        )
        conn.execute(
            issue_activity.insert().values(
                id=new_id(),
                organization_id=alpha["org_id"],
                issue_id=i1["id"],
                actor_id=alpha["user_id"],
                event_type="comment",
                changes="{}",
                created_at=today,
            )
        )

        # Ensure admin is also a team member for profile teams assertion.
        existing_tm = conn.execute(
            team_member.select().where(
                (team_member.c.team_id == alpha["team_id"])
                & (team_member.c.user_id == alpha["user_id"])
            )
        ).first()
        if not existing_tm:
            conn.execute(
                team_member.insert().values(team_id=alpha["team_id"], user_id=alpha["user_id"])
            )
        conn.execute(
            user.update().where(user.c.id == alpha["user_id"]).values(name="Luis")
        )

    return {
        "member_id": member_id,
        "member_token": member_token,
        "agent_id": agent_id,
        "i1": i1,
        "i2": i2,
    }


def test_leaderboard_and_profiles_readable_by_non_admin_member(world):
    seeded = _seed_usage_and_activity(world)
    alpha = world["alpha"]

    # Non-admin member reads the workspace leaderboard.
    lb_resp = _post(
        world["client"],
        seeded["member_token"],
        """
        query LB {
          activityLeaderboard(days: 30, heatmapDays: 30, limit: 10) {
            days
            heatmapDays
            totalTokens
            totalInputTokens
            totalOutputTokens
            totalCacheReadTokens
            totalRunMinutes
            totalRuns
            totalCompletedTasks
            totalActions
            daily { date tokens completedTasks actions intensity }
            people { userId name tokens runMinutes runs completedTasks openTasks actions }
            tasks { issueId identifier title tokens runMinutes runs }
            agents { agentUserId name tokens runMinutes runs completedRuns }
          }
        }
        """,
    )
    assert "errors" not in lb_resp, lb_resp
    lb = lb_resp["data"]["activityLeaderboard"]
    assert lb["totalTokens"] == 165_000
    assert lb["totalInputTokens"] == 90_000
    assert lb["totalOutputTokens"] == 25_000
    assert lb["totalCacheReadTokens"] == 50_000
    assert lb["totalRunMinutes"] == 13
    assert lb["totalRuns"] == 2
    assert lb["totalCompletedTasks"] >= 1
    assert len(lb["daily"]) == 30
    assert any(d["intensity"] > 0 for d in lb["daily"])

    # Luis (150k tokens) ranks #1 ahead of Kevin (15k tokens).
    assert lb["people"][0]["userId"] == alpha["user_id"]
    assert lb["people"][0]["tokens"] == 150_000
    assert lb["people"][1]["userId"] == seeded["member_id"]
    assert lb["people"][1]["tokens"] == 15_000

    # Top task is i1 with 150k tokens.
    assert lb["tasks"][0]["issueId"] == seeded["i1"]["id"]
    assert lb["tasks"][0]["tokens"] == 150_000

    # Agent leaderboard lists Coder with 165k tokens and 1 completed run.
    assert lb["agents"][0]["agentUserId"] == seeded["agent_id"]
    assert lb["agents"][0]["tokens"] == 165_000
    assert lb["agents"][0]["completedRuns"] == 1

    # Member profile for Luis (read by Kevin).
    prof_resp = _post(
        world["client"],
        seeded["member_token"],
        """
        query Prof($uid: ID!) {
          memberProfile(userId: $uid, days: 30, heatmapDays: 14) {
            user { id name email admin }
            teams { id key name }
            totalTokens
            runMinutes
            runs
            completedTasks
            currentStreak
            longestStreak
            rankByTokens
            rankByCompleted
            daily { date intensity tokens }
            topTasks { issueId identifier tokens }
            recentActivity { id eventType issueIdentifier }
          }
        }
        """,
        {"uid": alpha["user_id"]},
    )
    assert "errors" not in prof_resp, prof_resp
    prof = prof_resp["data"]["memberProfile"]
    assert prof["user"]["name"] == "Luis"
    assert len(prof["teams"]) >= 1
    assert prof["totalTokens"] == 150_000
    assert prof["runMinutes"] == 10
    assert prof["runs"] == 1
    assert prof["completedTasks"] >= 1
    assert prof["currentStreak"] >= 2
    assert prof["longestStreak"] >= 2
    assert prof["rankByTokens"] == 1
    assert prof["rankByCompleted"] == 1
    assert len(prof["daily"]) == 14
    assert prof["topTasks"][0]["issueId"] == seeded["i1"]["id"]
    assert len(prof["recentActivity"]) == 2


def test_task_usage_by_id_and_identifier(world):
    seeded = _seed_usage_and_activity(world)
    for ref in (seeded["i1"]["id"], seeded["i1"]["identifier"]):
        resp = _post(
            world["client"],
            seeded["member_token"],
            """
            query TU($ref: String!) {
              taskUsage(issueId: $ref) {
                issueId
                tokens
                inputTokens
                outputTokens
                cacheReadTokens
                runMinutes
                runs
                breakdown { agentUserId agentName model tokens runMinutes runs }
              }
            }
            """,
            {"ref": ref},
        )
        assert "errors" not in resp, resp
        tu = resp["data"]["taskUsage"]
        assert tu["issueId"] == seeded["i1"]["id"]
        assert tu["tokens"] == 150_000
        assert tu["runMinutes"] == 10
        assert tu["runs"] == 1
        assert tu["breakdown"][0]["model"] == "anthropic/claude-sonnet-4-5"
        assert tu["breakdown"][0]["agentName"] == "Coder"


def test_cross_tenant_isolation(world):
    seeded = _seed_usage_and_activity(world)
    alpha = world["alpha"]
    bravo = world["bravo"]

    # Bravo must never see Alpha's tokens, tasks, or Luis's profile.
    lb_resp = _post(
        world["client"],
        bravo["token"],
        "{ activityLeaderboard(days: 30) { totalTokens tasks { issueId } people { userId } } }",
    )
    assert "errors" not in lb_resp, lb_resp
    lb = lb_resp["data"]["activityLeaderboard"]
    assert lb["totalTokens"] == 0
    assert lb["tasks"] == []
    assert all(p["userId"] != alpha["user_id"] for p in lb["people"])

    prof_resp = _post(
        world["client"],
        bravo["token"],
        "query($uid: ID!) { memberProfile(userId: $uid) { user { id } } }",
        {"uid": alpha["user_id"]},
    )
    assert "errors" not in prof_resp, prof_resp
    assert prof_resp["data"]["memberProfile"] is None

    tu_resp = _post(
        world["client"],
        bravo["token"],
        "query($ref: String!) { taskUsage(issueId: $ref) { tokens } }",
        {"ref": seeded["i1"]["id"]},
    )
    assert "errors" not in tu_resp, tu_resp
    assert tu_resp["data"]["taskUsage"] is None
