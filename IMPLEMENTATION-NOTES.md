# Implementation notes — departments, single-org mode, private-team enforcement

Branch `feat/departments-single-org`, based on `7d5fa8a`. Every decision in
`IMPLEMENTATION-BRIEF.md` is implemented as written; the one place the brief left the
mechanism open ("upcoming cycles snap to the nearest parent cycle") is called out in
*Interpretations* below.

## Baseline vs. result

| | passed | skipped | failed |
|---|---|---|---|
| baseline (recorded, re-measured at 7d5fa8a) | 338 | 6 | 1 |
| after this work | 353 | 6 | 0 |

The pre-existing failure `tests/test_security_lane_a.py::test_security_headers_on_api_and_html`
was **not** touched. It is environment-dependent, not fixed by this branch: it fails
whenever `frontend/dist` is absent and passes once the frontend has been built (the test
asserts CSP headers on an HTML error page, and the served HTML differs).

    $ mv frontend/dist /tmp/dist-backup
    $ .venv/bin/python -m pytest -q tests/test_security_lane_a.py::test_security_headers_on_api_and_html
    1 failed
    $ mv /tmp/dist-backup frontend/dist
    $ .venv/bin/python -m pytest -q tests/test_security_lane_a.py::test_security_headers_on_api_and_html
    1 passed

## Files

| file | change |
|---|---|
| `cliniar_server/db.py` | revision 35: `team.visibility`, `team.parent_team_id` (+index), `team_member.role`; `private` removed from the DDL and migrated |
| `cliniar_server/store.py` | `visibility_holds()` as the single rule; `visible_team_clause`/`visible_team_ids`/`visibility_map` derive from it; cascading membership; scoped cycle-by-id, uploads, activity |
| `cliniar_server/writer.py` | `team_create`/`team_update` accept visibility and parent; depth/self-parent/cycle validation; inherited label release on un-nest; cycle alignment; seat cascade |
| `cliniar_server/resolvers.py` | `Team.parent`/`Team.children`; `teamMemberAdd(role)`; `HOJA_SINGLE_ORG` gates on `workspaceCreate`/`workspaceSwitch`; cycle-by-id scoped |
| `cliniar_server/deployment.py` | new: `single_org_mode()` |
| `cliniar_server/app.py` | upload serving checks issue visibility; `/onboarding/status` reports `singleOrg` |
| `cliniar_server/schema.graphql` | `Team.visibility/parentTeamId/parent/children`, inputs updated, `teamMemberAdd(role)` |
| `cliniar/graphql/fragments.py`, `cliniar/models/team.py` | client asks for `visibility`/`parentTeamId` |
| `frontend/src/main.tsx` | single-org mode hides "Create workspace" and the switch list |
| `docs/DESIGN.md` | `--workspace` removed; single-org and department rules documented |
| `tests/test_team_hierarchy.py` | new, 11 tests (written first, 9 of the first 10 failed; the cycle test in item E was added later and also verified failing) |
| `tests/test_team_visibility_audit.py` | new, 3 read-path probes (all failed before their fix) |
| `tests/test_realtime.py`, `tests/test_server_backend.py` | updated for the intentional API change |

## Read-path audit (item C)

`visibility_holds()` is the only definition of "may this viewer see this team":

```sql
team.organization_id = :org
AND ( team.visibility = 'public'
   OR team.id IN (SELECT team_id FROM team_member WHERE user_id = :viewer)
   OR (team.visibility = 'restricted'
       AND team.parent_team_id IN (SELECT team_id FROM team_member WHERE user_id = :viewer)) )
```

`restricted` looks at the **direct** parent only; that is sound because the membership
cascade keeps an explicit row on every ancestor, so a grandparent member is already a
parent member.

| read path | how it is scoped | verdict |
|---|---|---|
| issue list / board / my issues (`store.issues`) | `_scope_teams(issue.team_id)` | safe |
| issue by id or identifier | `_scope_teams(issue.team_id)` | safe |
| search (`store.search_issues`) | `_scope_teams(issue.team_id)` | safe |
| labels (`store.labels`) | `_scope_teams(nullable)` on the label's team | safe |
| workflow states / cycles list | `_scope_teams` | safe |
| teams / team by id-or-key / team states / team members / team cycles | `_scope_teams` / `team_visible` | safe |
| project issue activity | `_scope_teams(issue.team_id)` | safe |
| **personal activity (`store.my_issue_activity`)** | **was unscoped → `changes` leaked titles** | **leaking, fixed** (`_scope_teams`) |
| **cycle by id (`store.cycle_by_id`, `Query.cycle`)** | **was unscoped** | **leaking, fixed** (`_scope_teams`) |
| **uploaded files (`GET /api/uploads/{id}`)** | **was org-scoped only** | **leaking, fixed** (`store.upload_visible`) |
| comments / attachments / subscribers | only reachable through an already-scoped `Issue` parent | safe by construction |
| inbox notifications | `recipient_id = viewer`, and the nested `issue` field goes through the scoped loader | safe |
| sync bootstrap/delta (`sync.py`) | `visible_team_clause`, and the ETag includes a visibility hash | safe |
| realtime / change_event (`realtime.py`) | `visibility_map`, asserted equal to `visible_team_ids` in `test_realtime.py` | safe |
| `mcp_tools.py` | every tool runs GraphQL through the same resolvers | safe by delegation |
| GraphQL `Issue.*` field resolvers (state, cycle, parent, labels, team) | parent object is already scoped | safe by construction |
| projects / initiatives / project updates | org-scoped, no team dimension in the model | unchanged (not team-visible by design) |

Each fixed leak has a test that failed before the fix and passes after; the
`my_issue_activity` fix was verified by deleting the added line and re-running
(the feed then returns a node whose non-nullable `issue` resolves to `null`).

## Interpretations

* **"upcoming cycles snap to the nearest parent cycle"** — implemented as *boundary
  alignment*: each not-yet-completed cycle of the moved subtree copies
  `starts_at`/`ends_at`/`cadence_boundary_at` from the parent cycle with the nearest
  number, and the in-flight cycle is completed. The alternative reading (move the
  sub-team's issues onto the parent's cycle row) was rejected because it would break the
  invariant that `issue.cycle_id` belongs to `issue.team_id`, which every issue write
  path validates.
* **Un-nesting and membership** — un-nesting leaves `team_member` rows alone. The brief
  lists only labels and cycles for un-nest, and removing people silently is exactly what
  decision 5 forbids.
* **`team.private` column** — removed from the ORM/DDL but left in place in already
  migrated databases (SQLite cannot drop columns with a dependent FK; nothing reads it).

## Gates

### Gate 1 — `.venv/bin/python -m pytest -q`

```

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
353 passed, 6 skipped, 1 warning in 76.57s (0:01:16)
```

### Gate 2 — GraphQL introspection, base vs. this branch

Type-system introspection of `cliniar_server/schema.graphql` at `7d5fa8a` and at `HEAD`
(plus the unchanged provider/conversation SDL), `diff -u`:

```diff
@@ -324,7 +324,7 @@
   teamCreate(input: TeamCreateInput!): TeamPayload
   teamCycleCadenceEnable(teamId: String!, weekday: Int!): TeamCycleCadence!
   teamDelete(id: String!, deleteIssues: Boolean): DeletePayload
-  teamMemberAdd(teamId: String!, userId: String!): UserPayload
+  teamMemberAdd(teamId: String!, userId: String!, role: String): UserPayload
   teamMemberRemove(teamId: String!, userId: String!): UserPayload
   teamUpdate(id: String!, input: TeamUpdateInput!): TeamPayload
   userPreferenceSet(key: String!, value: String!): UserPreferencesPayload!
@@ -499,6 +499,7 @@
   autoAddBacklog(): Boolean
   autoAddCompleted(): Boolean
   autoAddStarted(): Boolean
+  children(first: Int): TeamConnection
   color(): String
   createdAt(): DateTime
   cycles(first: Int): CycleConnection
@@ -508,10 +509,12 @@
   key(): String!
   members(first: Int): UserConnection
   name(): String!
-  private(): Boolean
+  parent(): Team
+  parentTeamId(): ID
   states(first: Int): WorkflowStateConnection
   timezone(): String
   updatedAt(): DateTime
+  visibility(): String!
 OBJECT TeamConnection
   nodes(): [Team!]!
   pageInfo(): PageInfo!
@@ -522,8 +525,9 @@
   key: String!
   memberIds: [String!]
   name: String!
-  private: Boolean
+  parentTeamId: String
   timezone: String
+  visibility: String
 OBJECT TeamCycleCadence
   activeCycle(): Cycle
   enabled(): Boolean!
@@ -545,6 +549,8 @@
   description: String
   key: String
   name: String
+  parentTeamId: String
+  visibility: String
 OBJECT User
   active(): Boolean
   admin(): Boolean
```

Nothing else changed: no type added or removed, no other field or argument touched.

### Gate 3 — `cliniar` CLI against a fresh sqlite database

```
$ cliniar-serve seed --db /tmp/gate3/gate3.db --org "Gate Three" --org-key gate-three \
    --team-key ENG --team-name Engineering --user "Gate Admin" --email gate@example.test \
    --token gate3-token --no-demo
Seeded tenant 'default' at /tmp/gate3/gate3.db
  org      : Gate Three
  team     : ENG
  user     : Gate Admin <gate@example.test>
  TOKEN    : gate3-token

$ export LINEAR_TOKEN=gate3-token LINEAR_API_URL=http://127.0.0.1:8799/graphql
$ cliniar -o plain me
d86f88fd-8ea6-400e-bf06-42fc223a3a09	Gate Admin	gate@example.test

$ cliniar -o json raw query 'mutation { teamCreate(input: {name: "Platform", key: "PLT", visibility: "public"}) {
    success team { id key visibility parentTeamId } } }'
{
  "teamCreate": {
    "success": true,
    "team": { "id": "e79aa506-c1dc-4884-8d5c-d9cc49e53e00", "key": "PLT",
              "visibility": "public", "parentTeamId": null }
  }
}

$ cliniar -o json raw query 'mutation { teamCreate(input: {name: "Apps", key: "APX",
    visibility: "restricted", parentTeamId: "e79aa506-..."}) { success team { key visibility parentTeamId parent { key } } } }'
{
  "teamCreate": {
    "success": true,
    "team": { "key": "APX", "visibility": "restricted",
              "parentTeamId": "e79aa506-...", "parent": { "key": "PLT" } }
  }
}

# a fourth level is refused
$ cliniar -o json raw query 'mutation { teamCreate(input: {name: "TooDeep", key: "DP5",
    parentTeamId: "03d5c5c2-..."}) { success team { key } } }'
  "extensions": { "code": "BAD_USER_INPUT", "field": "parentTeamId" }
  # message: Departments can nest at most 3 levels deep (max depth 3)

# self-parenting is refused
$ cliniar -o json raw query 'mutation { teamUpdate(id: "e79aa506-...", input: {parentTeamId: "e79aa506-..."}) ... }'
  "extensions": { "code": "BAD_USER_INPUT", "field": "parentTeamId" }
  # message: A team cannot be its own parent

$ cliniar -o json issue create --title "Department issue" --team APX
{ "id": "...", "identifier": "APX-1", "team": { "key": "APX", "name": "Apps" }, "number": 1, ... }

$ cliniar -o plain team list
APX	Apps	65a98c8a-ebfb-4fc2-ab1a-1cb0f698b582
DP4	Deep	03d5c5c2-10cb-44db-b432-383d0af3eb44
ENG	Engineering	9a140818-d7d7-461e-a033-09d93f691b73
PLT	Platform	e79aa506-c1dc-4884-8d5c-d9cc49e53e00

$ cliniar -o plain issue list
APX-1	Todo	—	No priority	Department issue

$ cliniar -o json team get APX
{ "key": "APX", "visibility": "restricted", "parent_team_id": "e79aa506-...", ... }
```

This gate is what caught the client regression: before the last commit,
`cliniar issue create` failed with `Cannot query field 'private' on type 'Team'`.

Single-org gating over HTTP (`cliniar-serve serve` with `HOJA_SINGLE_ORG=1`):

```
$ curl -s http://127.0.0.1:8798/onboarding/status     # HOJA_SINGLE_ORG=1
{"initialized":true,"singleOrg":true}
$ curl -s http://127.0.0.1:8799/onboarding/status     # default
{"initialized":true,"singleOrg":false}

$ POST /graphql workspaceCreate(input: {name:"Second", urlKey:"second"})
{"data":{"workspaceCreate":null},"errors":[{"message":"Creating a workspace is unavailable:
 this instance runs in single-organization mode.","extensions":{"code":"SINGLE_ORG_MODE"}}]}

$ POST /graphql workspaceSwitch(organizationId: "whatever")
{"data":{"workspaceSwitch":null},"errors":[{"message":"Switching workspace is unavailable:
 this instance runs in single-organization mode.","extensions":{"code":"SINGLE_ORG_MODE"}}]}

$ POST /graphql issueCreate(input: {title:"Single-org issue", teamId:"ENG"})
{"data":{"issueCreate":{"success":true,"issue":{"identifier":"ENG-1"}}}}
$ POST /graphql issues(first: 5)
{"data":{"issues":{"nodes":[{"identifier":"ENG-1",...},{"identifier":"APX-1",...}]}}}
```

### Gate 4 — post-migration assertions

```

--- fresh (gate-3 database) ---
PASS  fresh (gate-3 database): exactly one organization row  [1 rows]
PASS  fresh (gate-3 database): every team.parent_team_id resolves  [[]]
PASS  fresh (gate-3 database): no team is its own parent  [[]]
PASS  fresh (gate-3 database): no team is its own ancestor  [[]]
PASS  fresh (gate-3 database): visibility in ['private', 'public', 'restricted']  [[]]
PASS  fresh (gate-3 database): team_member.role in ['member', 'owner']  [{'owner'}]
PASS  fresh (gate-3 database): schema revision is 35  [35]

--- legacy (rewound to revision 34, private=True) ---
PASS  legacy: database now looks like revision 34  [['active_cycle_id', 'archived_at', 'auto_add_backlog', 'auto_add_completed', 'auto_add_started', 'cadence_anchor_at', 'cadence_cooldown_weeks', 'cadence_duration_weeks', 'cadence_enabled', 'cadence_policy_version', 'cadence_upcoming_count', 'cadence_weekday', 'color', 'created_at', 'default_state_id', 'description', 'icon', 'id', 'issue_counter', 'key', 'name', 'organization_id', 'parent_team_id', 'private', 'timezone', 'updated_at']]
PASS  legacy: private=True migrated to visibility='private'  [[('ENG', 'private', None)]]

--- legacy ---
PASS  legacy: exactly one organization row  [1 rows]
PASS  legacy: every team.parent_team_id resolves  [[]]
PASS  legacy: no team is its own parent  [[]]
PASS  legacy: no team is its own ancestor  [[]]
PASS  legacy: visibility in ['private', 'public', 'restricted']  [[]]
PASS  legacy: team_member.role in ['member', 'owner']  [{'member'}]
PASS  legacy: schema revision is 35  [35]

all gate-4 assertions passed
```

## Follow-ups (review pass, after the worker finished)

Three defects found while reviewing this branch; each fixed with a test that failed first.

### 1. Project visibility — the audit's last row was wrong
The audit said projects have "no team dimension in the model". `project_team` *is* that
dimension, and `store.projects` filtered on organization only, so a project linked solely
to a private team was listed to every member (name, description, lead). `project_by_id`
and `projects_by_ids` had the same hole. Added `_scope_projects`: a project with no team
links keeps the historical org-wide behaviour, a project whose every linked team is
invisible disappears. The viewer is threaded through `Query.projects`, `Query.project`,
`Issue.project` and the projectCreate/projectUpdate payloads.

### 2. CSP depended on `frontend/dist`
`_SecurityHeaders` only attaches `Content-Security-Policy` to `text/html`, and the
unbuilt-bundle path answered JSON 503 — so `test_security_headers_on_api_and_html` failed
on a clean checkout. The unbuilt page is now HTML (`_UNBUILT_HTML`), so clickjacking
protection applies whether or not the SPA is built.

### 3. `projectUpdate` could not change teams
`ProjectUpdateInput` had no `teamIds`, so a project created without a team could never
join one through the API. `projectUpdate` now validates org scope, archive state and team
visibility, then replaces the `project_team` rows.

Evidence: `tests/test_project_visibility.py` — both tests fail with the source fixes
reverted (`2 failed`) and pass with them (`2 passed`). Full suite with `frontend/dist`
moved away: **355 passed, 6 skipped, 0 failed in 74.97s**.
