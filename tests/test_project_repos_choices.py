"""Tests for projectRepositoryChoices (project repo picker)."""
from __future__ import annotations

import json

from hoja_server.db import api_key, github_link, new_id, now_iso, project, token_hash, user
from tests.agent.test_auto_verify_and_pools import (
    _gql,
    world,
)

pytest_plugins = []
_WORLD_FIXTURE = world


def _create_project(env, token: str, team_id: str, name: str, repository: str | None = None) -> str:
    body = _gql(
        env,
        token,
        f"""mutation {{ projectCreate(input: {{
            name: {json.dumps(name)}, teamIds: ["{team_id}"] }}) {{ project {{ id }} }} }}""",
    )
    assert "errors" not in body, body
    pid = body["data"]["projectCreate"]["project"]["id"]
    if repository:
        set_ = _gql(
            env,
            token,
            f"""mutation {{ projectRepositorySet(
                projectId: "{pid}", repository: {json.dumps(repository)}) {{ id repository }} }}""",
        )
        assert "errors" not in set_, set_
    return pid


def _create_issue(env, token: str, team_id: str, title: str = "Task") -> str:
    body = _gql(
        env,
        token,
        f"""mutation {{ issueCreate(input: {{
            teamId: "{team_id}", title: {json.dumps(title)} }}) {{ issue {{ id }} }} }}""",
    )
    assert "errors" not in body, body
    return body["data"]["issueCreate"]["issue"]["id"]


def _insert_github_link(env, org_id: str, issue_id: str, *, external_id: str, url: str) -> None:
    stamp = now_iso()
    with env["engine"].begin() as conn:
        conn.execute(
            github_link.insert().values(
                id=new_id(),
                organization_id=org_id,
                issue_id=issue_id,
                kind="pr",
                external_id=external_id,
                url=url,
                title=f"PR {external_id}",
                state="open",
                created_at=stamp,
                updated_at=stamp,
            )
        )


def test_choices_deduplicate_across_projects_and_github_links(world):
    alpha = world["alpha"]
    _create_project(world, alpha["token"], alpha["team_id"], "Web", "acme/web")
    _create_project(world, alpha["token"], alpha["team_id"], "API", "https://github.com/acme/api.git")
    _create_project(world, alpha["token"], alpha["team_id"], "Local", "/srv/repos/internal")
    issue_id = _create_issue(world, alpha["token"], alpha["team_id"])
    # Duplicate of acme/web (different case) plus a new repo only present in github_link.
    _insert_github_link(
        world,
        alpha["org_id"],
        issue_id,
        external_id="ACME/Web#42",
        url="https://github.com/ACME/Web/pull/42",
    )
    _insert_github_link(
        world,
        alpha["org_id"],
        issue_id,
        external_id="acme/mobile#7",
        url="https://github.com/acme/mobile/pull/7",
    )
    _insert_github_link(
        world,
        alpha["org_id"],
        issue_id,
        external_id="acme/mobile#8",
        url="https://github.com/acme/mobile/pull/8",
    )

    body = _gql(
        world,
        alpha["token"],
        "query { projectRepositoryChoices { fullName url source } }",
    )
    assert "errors" not in body, body
    choices = body["data"]["projectRepositoryChoices"]
    by_name = {c["fullName"]: c for c in choices}

    assert set(by_name) == {"acme/web", "acme/api", "acme/mobile", "/srv/repos/internal"}
    assert by_name["acme/web"] == {
        "fullName": "acme/web",
        "url": "https://github.com/acme/web",
        "source": "project",
    }
    assert by_name["acme/api"] == {
        "fullName": "acme/api",
        "url": "https://github.com/acme/api",
        "source": "project",
    }
    assert by_name["acme/mobile"] == {
        "fullName": "acme/mobile",
        "url": "https://github.com/acme/mobile",
        "source": "github_link",
    }
    assert by_name["/srv/repos/internal"] == {
        "fullName": "/srv/repos/internal",
        "url": None,
        "source": "project",
    }


def test_choices_are_org_scoped_and_accessible_to_members(world):
    alpha, bravo = world["alpha"], world["bravo"]
    _create_project(world, alpha["token"], alpha["team_id"], "Alpha App", "alpha-org/app")
    _create_project(world, bravo["token"], bravo["team_id"], "Bravo Secret", "bravo-org/secret")

    alpha_issue = _create_issue(world, alpha["token"], alpha["team_id"])
    bravo_issue = _create_issue(world, bravo["token"], bravo["team_id"])
    _insert_github_link(
        world,
        alpha["org_id"],
        alpha_issue,
        external_id="alpha-org/shared#1",
        url="https://github.com/alpha-org/shared/pull/1",
    )
    _insert_github_link(
        world,
        bravo["org_id"],
        bravo_issue,
        external_id="bravo-org/private#9",
        url="https://github.com/bravo-org/private/pull/9",
    )

    # Archived project repo should not be offered.
    archived_pid = _create_project(
        world, alpha["token"], alpha["team_id"], "Old", "alpha-org/retired"
    )
    with world["engine"].begin() as conn:
        conn.execute(
            project.update()
            .where(project.c.id == archived_pid)
            .values(archived_at=now_iso())
        )

    # Non-admin member in alpha can still query choices for alpha only.
    stamp, member_id = now_iso(), new_id()
    with world["engine"].begin() as conn:
        conn.execute(
            user.insert().values(
                id=member_id,
                organization_id=alpha["org_id"],
                name="Member",
                display_name="Member",
                email="member@alpha.test",
                active=True,
                admin=False,
                timezone="UTC",
                created_at=stamp,
                updated_at=stamp,
            )
        )
        conn.execute(
            api_key.insert().values(
                id=new_id(),
                token_hash=token_hash("alpha-member-tok"),
                label="member",
                user_id=member_id,
                organization_id=alpha["org_id"],
                created_at=stamp,
            )
        )

    alpha_res = _gql(
        world,
        "alpha-member-tok",
        "query { projectRepositoryChoices { fullName url source } }",
    )
    assert "errors" not in alpha_res, alpha_res
    alpha_names = {c["fullName"] for c in alpha_res["data"]["projectRepositoryChoices"]}
    assert alpha_names == {"alpha-org/app", "alpha-org/shared"}

    bravo_res = _gql(
        world,
        bravo["token"],
        "query { projectRepositoryChoices { fullName url source } }",
    )
    assert "errors" not in bravo_res, bravo_res
    bravo_names = {c["fullName"] for c in bravo_res["data"]["projectRepositoryChoices"]}
    assert bravo_names == {"bravo-org/secret", "bravo-org/private"}
