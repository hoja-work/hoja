"""Builds the Hoja agent system prompt per request: fixed working examples plus a live workspace snapshot."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

WORKING_EXAMPLES = """## How to work: worked examples

Every example below is valid against the schema. Copy field names exactly.

### Find my open issues
1. `query { viewer { id } }`
2. `query { issues(filter: { assignee: { id: { eq: "<viewerId>" } }, state: { type: { nin: ["completed", "canceled"] } } }, first: 25) { nodes { identifier title priorityLabel dueDate state { name } } } }`
3. Reply with a short list. Link each identifier.

### Summarize a team's work
1. Resolve the team key from the snapshot or `query { teams(first: 20) { nodes { id key name } } }`.
2. `query { issues(filter: { team: { key: { eq: "ENG" } }, state: { type: { nin: ["completed", "canceled"] } } }, first: 50) { nodes { identifier title state { name } assignee { displayName } } } }`
3. Group by state, lead with counts, and list at most 10 items. Say how many were left out.

### Find an existing issue before creating one
`query { searchIssues(term: "login page", first: 5) { nodes { identifier title state { name } } } }`
If a match exists, tell the user instead of creating a duplicate.

### Create an issue
1. Get the team id from the snapshot or `teams`. Ask if more than one team fits.
2. `mutation { issueCreate(input: { title: "Login page returns 500", teamId: "<teamId>", description: "Acceptance: ...", priority: 2 }) { success issue { id identifier url } } }`
   - Add `projectId` only when the user named a project. Get it from `projects` first.
   - Priority: 0 none, 1 urgent, 2 high, 3 medium, 4 low.
3. Read it back: `query { issue(id: "ENG-7") { identifier title team { key } project { name } } }`.

### Change an issue's state
1. `query { issue(id: "ENG-7") { id identifier state { name } team { id } } }`
2. `query { workflowStates(filter: { team: { key: { eq: "ENG" } } }) { nodes { id name type } } }`
3. Pick the state by `name` or `type` (`unstarted`, `started`, `completed`, `canceled`). Never guess ids.
4. `mutation { issueUpdate(id: "<issueId>", input: { stateId: "<stateId>" }) { success issue { identifier state { name } } } }`

### Assign an issue
Confirm the person is a team member first: `query { team(id: "<teamId>") { members(first: 100) { nodes { id displayName } } } }`. Then `issueUpdate(id: "<issueId>", input: { assigneeId: "<userId>" }) { success }`.

### Comment on an issue
`mutation { commentCreate(input: { issueId: "<issueId>", body: "Blocked on the API review." }) { success } }`

### Active cycle status
Use the active cycle in the snapshot. For another cycle:
`query { cycles(filter: { team: { key: { eq: "ENG" } }, isActive: { eq: true } }) { nodes { name number progress endsAt } } }`
Report progress as a percentage with the end date.

### Recent activity
`query { myIssueActivity(first: 10) { nodes { eventType createdAt issue { identifier title } } } }`

### Unassigned bugs
The filter cannot match "no assignee", so fetch the team's open bugs and drop the ones with no assignee in your reply:
`query { issues(filter: { team: { key: { eq: "ENG" } }, labels: { name: { eq: "Bug" } }, state: { type: { nin: ["completed", "canceled"] } } }, first: 50) { nodes { identifier title priorityLabel assignee { id } } } }`
"""

SnapshotFetch = Callable[[str, dict], Awaitable[dict]]

SNAPSHOT_QUERY = """query AgentSnapshot($viewerId: ID!) {
  viewer { id name displayName }
  teams(first: 20) { nodes { id key name activeCycle { id name number endsAt progress } } }
  myIssueActivity(first: 5) { nodes { eventType createdAt issue { identifier title } } }
  open: issues(filter: { assignee: { id: { eq: $viewerId } }, state: { type: { nin: ["completed", "canceled"] } } }, first: 15) {
    nodes { identifier title priorityLabel dueDate state { name type } team { key } }
  }
}"""


def _clean(text: object, limit: int = 120) -> str:
    """Flatten user-controlled text to one short line so it cannot inject prompt structure."""
    return " ".join(str(text or "").split())[:limit]


def _snapshot_text(data: dict) -> str:
    viewer = data.get("viewer") or {}
    teams = (data.get("teams") or {}).get("nodes") or []
    activity = (data.get("myIssueActivity") or {}).get("nodes") or []
    open_issues = (data.get("open") or {}).get("nodes") or []

    lines = [f"Signed-in user: {_clean(viewer.get('displayName') or viewer.get('name') or 'unknown')} (id {viewer.get('id')})"]
    lines.append("Teams:")
    for t in teams:
        cycle = t.get("activeCycle")
        cycle_text = (
            f"active cycle {cycle.get('name') or '#' + str(cycle.get('number'))}, "
            f"{round((cycle.get('progress') or 0) * 100)}% done, ends {str(cycle.get('endsAt'))[:10]}"
            if cycle else "no active cycle"
        )
        lines.append(f"- {_clean(t['key'], 20)} (id {t['id']}) {_clean(t['name'])}: {cycle_text}")
    lines.append(f"Open issues assigned to you ({len(open_issues)} shown):")
    for i in open_issues:
        state = (i.get("state") or {}).get("name") or "?"
        due = f", due {str(i['dueDate'])[:10]}" if i.get("dueDate") else ""
        lines.append(f"- {i['identifier']} [{state}, {i.get('priorityLabel') or 'no priority'}{due}] {_clean(i['title'])}")
    if not open_issues:
        lines.append("- none")
    if activity:
        lines.append("Recent activity on your issues:")
        for a in activity:
            issue = a.get("issue") or {}
            lines.append(f"- {issue.get('identifier')} {a.get('eventType')} at {str(a.get('createdAt'))[:16]}")
    return "\n".join(lines)


async def workspace_snapshot(fetch: SnapshotFetch, viewer_id: str | None) -> str:
    """Return a bounded text snapshot of the user's workspace, or a note if it cannot be loaded."""
    if not viewer_id:
        return "Workspace snapshot unavailable: no signed-in user."
    data = await fetch(SNAPSHOT_QUERY, {"viewerId": viewer_id})
    if not data:
        return "Workspace snapshot unavailable: the snapshot query failed. Do not assume the user has no open issues."
    return _snapshot_text(data)


def compose_prompt(base: str, snapshot: str) -> str:
    """Assemble the system prompt: base rules, worked examples, then the live snapshot."""
    return "\n\n".join([base, WORKING_EXAMPLES, "## Live workspace snapshot\n" + snapshot])
