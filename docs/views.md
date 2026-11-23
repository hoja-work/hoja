# Views

A view is a saved query: it stores a scope, a filter tree, and the display options the
results are rendered with, so the same selection can be reopened, shared with a workspace
or a team, and reproduced from a URL. Hoja keeps views on the server (`saved_view`), not
in one browser: a private view belongs to its owner, a shared view belongs to the
workspace, and the two are the same entity with a different `visibility`.

A view always runs on the server. The web client, the CLI (`hoja issue list --view`), the
CSV export and Linear-shaped clients (`customView`) all read the same `View.issues` /
`View.projects` result, so a view selects the same rows wherever it is opened. Built-in task
lists (a team's Tasks tabs, My tasks, a project's tasks) share the same filter grammar and
display options, kept per page as [display preferences](#display-preferences).

| Topic | Section |
| --- | --- |
| API, kinds, scope | [GraphQL](#graphql), [Kinds](#kinds), [Scope](#scope) |
| Filter grammar, `"me"`, relative dates, relations | [Filters](#filters) |
| Display options and their layers | [Display options](#display-options), [Display preferences](#display-preferences) |
| Notifications and export | [Subscriptions](#subscriptions), [CSV export](#csv-export) |
| How results are computed | [Execution](#execution) |
| Linear clients | [Linear-compatible API](#linear-compatible-api), [Linear parity](#linear-parity) |
| Command line | [Command line](#command-line) |
| Manual order, label groups, plain-language filters | [Manual order](#manual-order), [Label groups](#label-groups), [Plain-language filters](#plain-language-filters) |
| Web pages, routes and shortcuts | [Web client](#web-client), [Routes and shortcuts](#routes-and-shortcuts) |

The GraphQL surface lives in `hoja_server/views_api.py`; validation and compilation live in
`hoja_server/views.py`. Both follow the extension subsystem's conventions
([extensions](extensions.md)): a declarative document validated field by field, one error
type that names the offending path, and updates that replace the stored document inside a
single transaction.

## GraphQL

```graphql
query { views(kind: "issue") { id name scope visibility favorite filters display } }
query { view(id: "…") { id name issues(first: 50) { nodes { identifier title } totalCount pageInfo { hasNextPage endCursor } } } }
query { viewPreview(kind: "issue", filters: {priority: {is: 1}}, scope: "team", teamId: "…") { issues { nodes { title } totalCount } } }

mutation($input: ViewCreateInput!) { viewCreate(input: $input) { success view { id slugId } } }
mutation($id: String!, $input: ViewUpdateInput!) { viewUpdate(id: $id, input: $input) { success } }
mutation($id: String!) { viewDelete(id: $id) { success } }
mutation($id: String!) { viewFavoriteToggle(id: $id) { success favorite view { id favorite } } }
mutation($id: String!) { viewDuplicate(id: $id, name: "Copy") { success view { id visibility } } }
mutation($id: String!) { viewPin(id: $id, pinned: true) { success view { pinned sortOrder } } }
```

| Field | Meaning |
| --- | --- |
| `id` | Stable identifier. |
| `name` | Display name, 1–80 characters. |
| `description`, `icon`, `color` | Optional presentation carried with the view. |
| `slugId` | URL fragment, unique inside the organization; generated from the name and id on create. |
| `url` | `<workspace origin>/<urlKey>/view/<slugId>`. The slug is fixed at creation, so renaming a view keeps its link; `view(id:)` accepts the id or the slug. |
| `kind` | `issue`, `project`, or `initiative` — which entity the filter addresses. |
| `scope` | `workspace`, `team`, `project`, or `initiative` — what the view is attached to. |
| `visibility` | `private`, `workspace`, or `team` — who may read it. |
| `shared` | `true` unless `visibility` is `private`. |
| `owner` | The member who owns the view; its creator until `viewUpdate(input: {ownerId})` hands it over. |
| `pinned`, `sortOrder` | A team or project view shown as a tab on that page, in `sortOrder` (revision 90). `viewPin` sets both; any member who can read the view may pin it, because tabs belong to the page rather than to the view's owner. A new pin goes after the existing ones, a workspace view cannot be pinned, and moving a view to another team or project drops its pin. |
| `filters` | The filter tree, in the grammar below. Stored as `{}` for "everything". |
| `display` | The display options, in the table below. Every view carries its own defaults. |
| `favorite` | Whether the *requesting user* favorited this view. |
| `preferences` | The caller's display layers; see [Display preferences](#display-preferences). |
| `subscription`, `subscriberCount` | The caller's subscription (null when not following) and how many members follow the view; see [Subscriptions](#subscriptions). |
| `issues` / `projects` / `initiatives` | The items the view selects, compiled and executed server-side; see [Execution](#execution). |
| `archivedAt` | Set by `viewDelete`; the row is kept so a delete can be reversed. |

`viewDelete` archives rather than erases. `viewUpdate` with `archived: false` restores it, so
an accidental delete reverts exactly like an extension uninstall does. The web client has no
restore control; restore through the API (`views(includeArchived: true)` lists archived rows).

`viewUpdate` also moves a view ("Move to"): `scope` with its `teamId`/`projectId`, and
`visibility` (`private` is a personal view, `workspace` and `team` are shared), under the same
rules as create. `ownerId` hands the view to another active member who can see the team or
project the view is placed in; only the owner or a workspace admin may change a view at all,
so only they can hand it over. `viewUpdate` and `viewDelete` accept the id or the slug, and a
view the caller cannot read (someone else's private view, a team view of a team they cannot
see) answers `NOT_FOUND`, never `FORBIDDEN`, so a write never confirms that it exists.

`viewDuplicate(id, name, visibility)` copies a view the caller can read — filters, display,
scope, description, icon and color — with the caller as owner. The copy is `private` unless
`visibility` is given and is named "<name> (copy)" unless `name` is given.

## Kinds

| `kind` | Filter addresses | Executed by |
| --- | --- | --- |
| `issue` | `IssueFilter` properties | `Store.view_issues` (team visibility + the shared task predicate compiler) |
| `project` | `ProjectFilter` properties | `Store.view_projects` (project visibility + the project predicate compiler) |
| `initiative` | `InitiativeFilter` properties | `Store.view_initiative_rows`, then the derived predicates in Python |

A view's kind is fixed at creation; `viewUpdate` cannot move filters between kinds.

## Scope

| `scope` | Requires | Meaning |
| --- | --- | --- |
| `workspace` | nothing | Spans every team the viewer can see, i.e. Linear's "all teams". |
| `team` | `teamId` | Attached to one team's Tasks and Projects pages, scoped to that team. |
| `project` | `projectId` | Attached to one project, rendered as a tab next to its Tasks tab. |
| `initiative` | `initiativeId` | Attached to one initiative. |

Scope is part of what a view selects: results are the saved filters AND the scope. A team
scope covers the team and its sub-teams; an initiative scope covers the projects of the
initiative and its sub-initiatives (for an task view, the tasks in those projects; for an
initiative view, the initiative and its sub-initiatives); a project scope covers that
project. Scope never widens visibility: rows outside the viewer's teams stay invisible.

Placing a view on a team, project, or initiative requires that the caller can see that
target: a team through `Store.team_visible_for`, a project through `Store.project_visible`,
an initiative by organization. A scope mismatch (for example a `project` scope without a
`projectId`) is rejected with the input path.

## Filters

`filters` is one JSON object: any number of property predicates, plus `and` and `or` lists of
the same shape for grouping.

```json
{
  "and": [
    { "state": { "isNot": "Canceled" } },
    { "or": [ { "priority": { "is": 1 } }, { "labels": { "includesAny": ["bug"] } } ] }
  ]
}
```

Limits, enforced by the parser: 5 levels of nesting, 200 predicates in total, at most 100
values in a list, and non-empty strings of at most 200 characters.

### Operators

| Linear operator | Accepted for | Compiles to Hoja's cmp key |
| --- | --- | --- |
| `is` | every property | `eq` |
| `isNot` | every property | `neq` |
| `isEitherOf` | id, name, enum and number properties | `in` |
| `isNeitherOf` | id, name, enum and number properties | `nin` |
| `contains` | text properties | `contains` (case-insensitive) |
| `containsIgnoreCase` | text properties | `containsIgnoreCase` |
| `equalsIgnoreCase` | text properties | `eqIgnoreCase` |
| `before` | date properties | `lt` |
| `after` | date properties | `gt` |
| `onOrBefore` | date properties | `lte` |
| `onOrAfter` | date properties | `gte` |
| `isGreaterThan`, `isGreaterThanOrEqual` | number properties | `gt`, `gte` |
| `isLessThan`, `isLessThanOrEqual` | number properties | `lt`, `lte` |
| `includesAny`, `includesEither` | `labels` | `labels.some` |
| `includesNone`, `includesNeither` | `labels` | `labels.none` |
| `includesAll` | `labels` | one `labels.some` per value |

`null` is a value for the properties that can be empty — `assignee`, `creator`, `subscriber`,
`project`, `cycle`, `parent`, `labels`, `estimate`, `dueDate`, `startedAt`, `completedAt`,
`canceledAt`, and on projects `lead`, `creator`, `members`, `health`, `startDate`,
`targetDate`, `initiative` — and means "No assignee", "No project", "No labels"
and so on: `{"assignee": {"is": null}}`, `{"labels": {"isEitherOf": [null, "Bug"]}}`. A
negative predicate on one of those properties keeps the empty rows, as Linear does:
`{"assignee": {"isNot": "…"}}` includes unassigned tasks. A list may mix ids and names; the
values are ORed for a positive operator and ANDed for a negative one.

Every other operator/property pair is rejected with `BAD_USER_INPUT` and `field` naming
the path, for example `filters.and[0].state.before`. `null` is only a value for the identity
operators (`is`, `isNot`, `isEitherOf`, `isNeitherOf`, `includesAny`, `includesNone`):
`{"estimate": {"isGreaterThan": null}}` and `{"labels": {"includesAll": [null]}}` are refused,
and the web client's operator menu does not offer them while a chip holds an empty value.

### Values resolved when the view runs

A view is stored as written and resolved each time it runs, for whoever runs it:

| Value | Accepted on | Means |
| --- | --- | --- |
| `"me"` | `assignee`, `creator`, `subscriber`; project `lead`, `creator`, `members` | The viewer. Also as the operator `{"isMe": true}` (`false` is "not me"). |
| `"myTeams"` | `team` | The teams the viewer is a member of. |
| ISO-8601 duration, e.g. `"-P7D"`, `"P2W"`, `"-P1M"`, `"-PT12H"` | every date property | Now plus the duration. Without a time part it resolves to a UTC calendar date, so `{"createdAt": {"after": "-P7D"}}` is "after the day a week ago"; with hours, minutes or seconds it is an instant. |
| `"today"`, `"yesterday"`, `"tomorrow"`, `"now"` | every date property | The UTC date (or, for `now`, the instant). |
| `"overdue"` | `dueDate`, project `targetDate`, with `is` | Before today. |

So one shared "Assigned to me, created this week" view shows each member their own tasks,
and still means this week next month. Free-text dates ("next tuesday") are rejected.

### Task properties

| Property | Value | Operators |
| --- | --- | --- |
| `team` | team id or key, or `"myTeams"` | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `state` | state id, state name (every team's state of that name), or the web client's `Name|type` key | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `stateType` | `triage`, `backlog`, `unstarted`, `started`, `completed`, `canceled` | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `assignee`, `delegate`, `creator` | user id, email, display name, or `"me"` | `is`, `isNot`, `isEitherOf`, `isNeitherOf`, `isMe` |
| `project` | project id or name | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `cycle` | cycle id or name | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `subscriber` | user id, email, display name, or `"me"` | `is`, `isNot`, `isEitherOf`, `isNeitherOf`, `includesAny`, `includesNone`, `includesAll`, `isMe` |
| `parent` | task id; `isNull: true` keeps top-level tasks only | `is`, `isNot`, `isNull` |
| `hasSubIssues`, `isSubIssue` | boolean | `is` |
| `estimate` | number | `is`, `isNot`, `isGreaterThan`, `isGreaterThanOrEqual`, `isLessThan`, `isLessThanOrEqual`, `isEitherOf`, `isNeitherOf` |
| `priority` | `0`–`4` | `is`, `isNot`, `isGreaterThan`, `isGreaterThanOrEqual`, `isLessThan`, `isLessThanOrEqual`, `isEitherOf`, `isNeitherOf` |
| `number` | integer | `is`, `isNot`, `isGreaterThan`, `isGreaterThanOrEqual`, `isLessThan`, `isLessThanOrEqual`, `isEitherOf`, `isNeitherOf` |
| `identifier`, `title`, `description` | text | `is`, `isNot`, `contains`, `containsIgnoreCase`, `equalsIgnoreCase` |
| `createdAt`, `updatedAt`, `dueDate`, `startedAt`, `completedAt`, `canceledAt` | ISO date or date-time, or a relative value | `is`, `before`, `after`, `onOrBefore`, `onOrAfter` |
| `labels` | label id or name | `is`, `isNot`, `isEitherOf`, `isNeitherOf`, `includesAny`, `includesEither`, `includesNone`, `includesNeither`, `includesAll` |

A date-only value on `createdAt` or `updatedAt` names the whole UTC day: `is` matches any
time that day, `before` and `onOrAfter` start at its midnight, `after` and `onOrBefore` end
at the next midnight. A date-time value is compared as an instant (any offset or `Z`).
`dueDate` is a calendar date, so a date-time value compares by its day. `labels.is` means
"has this label" and `labels.isNot` "does not have it".

Relations are the parent/child hierarchy: `parent` (a task id, or `isNull: true` for top-level
tasks), `hasSubIssues` ("is a parent") and `isSubIssue` ("is a sub-task"). The web client's
Relations filter writes exactly these three.

Properties Linear has and Hoja cannot execute are rejected with the reason rather than
ignored: `blocked`, `blocking`, `hasRelations`, `related` and `duplicate` (Hoja does not track
task-to-task relations), `triagedAt`, `autoClosedAt`, `milestone`, `customer` and `sla`.

### Project and initiative properties

| Project property | Value | Operators |
| --- | --- | --- |
| `id` | project id | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `name`, `summary` | text (`summary` is the project's description) | `is`, `isNot`, `contains`, `containsIgnoreCase`, `equalsIgnoreCase` |
| `slugId` | text | `is`, `isNot` |
| `state` | the stored status string, exactly | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `status` | a configured project status name, case-insensitively | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `team` | team id or key, or `"myTeams"` | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `lead`, `creator` | user id, email, display name, or `"me"` | `is`, `isNot`, `isEitherOf`, `isNeitherOf`, `isMe` |
| `members` | user id, email, display name, or `"me"` | `is`, `isNot`, `isEitherOf`, `isNeitherOf`, `includesAny`, `includesNone`, `includesAll`, `isMe` |
| `health` | `onTrack`, `atRisk`, `offTrack`, `none` (the latest project update's) | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `startDate`, `targetDate` | ISO date, or a relative value | `is`, `before`, `after`, `onOrBefore`, `onOrAfter` |
| `initiative` | initiative id or name; `null` is "in no initiative" | `is`, `isNot`, `isEitherOf`, `isNeitherOf`, `includesAny`, `includesNone`, `includesAll` |

A project can belong to several teams: `team isNot X` drops every project linked to X and
keeps projects with no team. Projects have no priority, labels or milestones in Hoja, so
those properties are rejected.

`kind: "initiative"` accepts `status`, `health`, `owner`, `leadTeam` and `label` (`is`,
`isEitherOf`), combined with `and`/`or`. Status values are `active`, `planned`, `completed`, `canceled`.

## Display options

Each view stores the options its results render with. These are the view's *defaults*: opening
the view restores them, and editing them through `viewUpdate` changes what the view means.
Each member can lay their own options over them; see [Display preferences](#display-preferences).

```json
{
  "layout": "board",
  "grouping": "assignee",
  "subGrouping": "priority",
  "ordering": "priority",
  "reverse": false,
  "visibleProperties": ["identifier", "title", "status", "assignee"],
  "timelineZoom": 1,
  "completedWindow": "month",
  "boardColumnOrder": ["Todo", "In Progress", "Done"],
  "showEmptyGroups": true,
  "showSubIssues": true,
  "nestedSubIssues": "matching",
  "orderCompletedByRecency": true,
  "hiddenColumns": ["Canceled"],
  "collapsedGroups": ["Backlog"]
}
```

| Key | Values | Default |
| --- | --- | --- |
| `layout` | `list`, `board`, `timeline` | `list` |
| `grouping` | `none`, `status`, `statusType`, `assignee`, `creator`, `priority`, `project`, `cycle`, `labels`, `team`; for project lists also `lead`, `members`, `health`, `startDate`, `targetDate` | `status` |
| `subGrouping` | same values as `grouping` (the swimlane axis; `none` means no lanes) | `none` |
| `ordering` | `manual`, `priority`, `updated`, `created`, `title`, `dueDate`, `status`, `estimate`, `name`, `startDate`, `targetDate` | `updated` (tasks), `manual` (projects) |
| `reverse` | boolean | `false` |
| `visibleProperties` | unique subset of the kind's property list | all properties |
| `timelineZoom` | number, `0.25`–`8` | `1` |
| `completedWindow` | `none`, `day`, `week`, `month`, `quarter` (91 days), `halfYear` (182 days), `all` | `week` |
| `boardColumnOrder` | list of group keys, at most 50 | `[]` |
| `showEmptyGroups` | boolean | `true` |
| `showSubIssues` | boolean; `false` lists only top-level tasks | `true` |
| `nestedSubIssues` | `all`, `matching`, `hide` — how a client nests children under a parent | `matching` (Linear's default: only sub-tasks that match the filters nest under a parent) |
| `orderCompletedByRecency` | boolean; a client orders completed rows by completion time | `false` |
| `hiddenColumns` | board group keys hidden from the board, at most 50 | `[]` |
| `collapsedGroups` | list group keys rendered collapsed, at most 50 | `[]` |

Visible properties: for tasks `identifier`, `id`, `title`, `status`, `assignee`, `creator`,
`priority`, `labels`, `project`, `milestone`, `cycle`, `team`, `dueDate`, `estimate`, `links`,
`timeInStatus`, `pullRequests`, `createdAt`, `updatedAt`, `created`, `updated`; for projects `name`, `status`, `lead`, `teams`, `startDate`, `targetDate`,
`health`, `progress`, `id`, `summary`, `members`, `issues`, `createdAt`, `updatedAt`,
`completedAt`; for initiatives `name`, `status`, `owner`, `targetDate`, `projects`.

Grouping is applied by the client that renders the list; the server validates it and stores it.
`name` orders by title (tasks) or name (projects); `targetDate` orders tasks by due date and
projects by target date; `startDate` orders projects by start date, empty last (tasks fall back
to the default ordering).

## Display preferences

Display options come in layers, as Linear's `ViewPreferences` do:

| Layer | Stored in | Who changes it |
| --- | --- | --- |
| default | a saved view's `display`; for a built-in page, an organization row in `view_preference` | see below |
| user | one `view_preference` row per (member, view or page) | that member |
| effective | the default with the user layer laid over it, key by key | — |

```graphql
query { viewPreferences(viewId: "…") { default user effective hasUserOverrides } }
query { viewPreferences(viewType: "team:<teamId>:active") { effective } }
mutation { viewPreferencesSet(viewId: "…", values: {ordering: "priority", layout: null}) { effective } }
mutation { viewPreferencesSet(viewId: "…", values: {grouping: "assignee"}, scope: "default") { default } }
mutation { viewPreferencesReset(viewId: "…") { effective } }
```

A preference target is either a saved view (`viewId`, id or slug) or a built-in page
(`viewType`): `my-issues`, `team:<teamId>:active`, `team:<teamId>:backlog`,
`project:<projectId>` and so on (letters, digits, `-`, `_`, `.`, up to five `:` segments). A page
whose segments include `projects` or `initiatives` takes that kind's display document; every
other page is a task list. A page that names a team or project is only reachable by members who
can see it.

`values` is a partial display document validated like `display`; it is laid over the existing
layer, and a `null` value removes that key. `scope: "user"` (the default) writes the caller's
layer. `scope: "default"` is "Set default for everyone": it writes a saved view's `display`
(owner or admin only), or a built-in page's organization row (any member who can see the team
or project for a team or project page; an admin for any other page), and removes the keys it set
from the caller's own layer so they see the new default. `viewPreferencesReset` deletes the
caller's layer.

`View.issues`, `View.projects`, `View.initiatives` and the CSV export run with the caller's
effective display, so two members can order and window the same shared view differently.

## Subscriptions

```graphql
mutation { viewSubscribe(viewId: "…", onAdded: true, onCompleted: true) { subscription { onAdded onCompleted } } }
mutation { viewUnsubscribe(viewId: "…") { success } }
```

A member who can read a task view can follow it (`view_subscription`) for two events, Linear's
"An task is added to the view" and "An task is marked completed or canceled". With neither
flag both are on; a subscription with both off is removed. When a task is created or changed
(`issueCreate`, `issueUpdate`, an agent verifier's state move), the writer compares which
followed views the task matched before and after, in the same transaction:

| Event | When | Inbox `kind` |
| --- | --- | --- |
| added | the task did not match the view and now does | `viewIssueAdded` |
| completed | the task matched the view and moved to a completed or canceled state | `viewIssueCompleted` |

The notification is an ordinary inbox row about the task, with `data.view` naming the view
(`id`, `name`, `slugId`) and, for completions, `data.stateType`. A view is matched for each
follower: `"me"` is the follower, their team visibility applies, the view's scope applies, and
`showSubIssues: false` is honoured; the completed-tasks window is not. The member who made the
change is never notified, and a follower who can no longer read the view hears nothing. Both
kinds can be turned off in notification preferences like every other kind. Only views with
followers in the organization are evaluated, so a workspace without subscriptions pays one
indexed query per task write. A view's filter runs once per write for all its followers unless
it is personal (`"me"`, `"myTeams"`), and each follower's team visibility is looked up once, so
the cost grows with the number of followed views and followers, not with their product.

Listing views reads `preferences`, `subscription` and `subscriberCount` (and the caller's
layer that `issues` runs with) in one query per table for the whole list, not one per view.

## CSV export

`GET /api/views/<id or slug>/export.csv`, authenticated like the GraphQL endpoint, returns a
task view's rows as CSV with the columns `identifier`, `title`, `status`, `priority`,
`assignee`, `labels`, `project`, `cycle`, `dueDate`, `createdAt`, `updatedAt`, `url`. The view
runs exactly as `View.issues` runs for the caller (filters, scope, team visibility, effective
display and order). Members get at most 250 rows and admins 2,000; `X-Total-Count` gives the
full match count and `X-Export-Limit` the cap applied. A view the caller cannot read, or a
project or initiative view, is `404`. Cells that start with `=`, `+`, `-` or `@` are prefixed
with `'` so a spreadsheet does not evaluate them.

## Execution

`View.issues`, `View.projects` and `View.initiatives` run the view on the server, and so does
`viewPreview` for a view that is not saved yet. Both take the same arguments:

| Argument | Meaning |
| --- | --- |
| `first` | Page size, default 50, capped at 250. |
| `after` | The `pageInfo.endCursor` of the previous page. |
| `filter` | An extra filter tree in the same grammar, ANDed with the saved one. |
| `display` | A partial display document merged over the saved one for this request. |
| `sort`, `reverse` | Shorthand for `display.ordering` and `display.reverse`. |

Results are the saved filters AND `filter` AND the scope, within the teams the viewer can
see. Task results also apply the display's `completedWindow` (completed and canceled tasks
finished inside the window, by `completedAt`/`canceledAt`) and `showSubIssues`. The
connection carries `totalCount`, the number of rows across every page.

Every ordering is executed in SQL:

| `ordering` | Tasks | Projects |
| --- | --- | --- |
| `updated`, `created` | newest first | newest first |
| `priority` | Urgent, High, Medium, Low, then No priority; ties by most recently updated | creation order |
| `title` | case-insensitive A–Z | name, case-insensitive A–Z |
| `dueDate` | earliest first, no due date last | target date, earliest first, none last |
| `status` | triage, started, unstarted, backlog, completed, canceled, then workflow position | started, planned, paused, backlog, completed, canceled |
| `estimate` | largest first, unestimated last | creation order |
| `manual` | creation order, oldest first (tasks carry no manual rank yet) | creation order, oldest first |

`reverse` inverts the sort in SQL, so "oldest first" pages from the oldest row; empty values
(no priority, no due date, no estimate) stay last in both directions. Paging is keyset
paging: the opaque cursor holds the last row's full sort key and id, so rows never repeat or
go missing across pages. A cursor from a different ordering or direction is refused with
`BAD_USER_INPUT` on `after`, and so is a cursor whose keys are not plain scalars.

Initiative views take the initiatives list's orderings (`display.ordering` or `sort`):
`manual` (the dragged rank, unranked last), `name`, `targetDate` (none last), `created`
(newest first), `health` (off track, at risk, on track, none last) and `priority` (Urgent to
Low, no priority last); ties keep newest first. They filter in Python, because health and
labels are derived, over one light query of the organization's initiatives with `and`/`or`
evaluated, and serialize only the returned page.

`viewPreview(kind, filters, display, scope, teamId, projectId, initiativeId, first, after)`
validates its documents and scope exactly as `viewCreate` does and returns a `ViewPreview`
with the same `issues`/`projects`/`initiatives` fields, so a draft renders through the same
engine as the saved view it will become.

## Manual order

Order is stored on the **task**, not on the view, as on Linear (`Issue.sortOrder`): one drag
reorders every manual list that shows both tasks. Two ranks exist (revision 96):

| Rank | Linear field | Used when |
| --- | --- | --- |
| `issue.sort_order` (`Task.rank`) | `sortOrder` | `display.ordering: "manual"` |
| `issue.priority_sort_order` (`Task.priorityRank`) | `prioritySortOrder` | `display.ordering: "priority"`, within one priority |

Ranks are fractional-index keys (`hoja_server/ranking.py`, a port of rocicorp's
fractional-indexing; the browser half is `frontend/src/views/ranking.ts` and both are checked
against each other). They compare **byte-wise**: PostgreSQL orders them with `COLLATE "C"`,
because a default collation such as `en-US` sorts `a0` before `B0` and would scramble the order.
A move writes one row; nothing is ever renumbered. Tasks without a rank (an import that wrote
rows directly) sort after ranked ones, newest first, and get a rank the first time a move names
them.

`taskMove(id, input: {afterId, beforeId, field, ...})` (alias `issueMove`) drops a task between
two others and may set the grouped field in the same write (a drag into another group):
`stateId`, `assigneeId`, `priority`, `projectId`, `cycleId` or `labelIds`. The key is computed
on the server under a per-workspace lock, so two people dropping into the same gap never get
the same key; "after X" wins when the client's "before" is stale.

Where tasks land without a drag, as Linear places them:

- a new task goes on top of its **status** (and its priority rank is appended);
- a task whose status changes outside a drag follows the team's
  `issueSortOrderOnStateChange` — `first` (default: top of the new status), `last`, or
  `noAction` — set in Settings › Teams › Workflow.

## Label groups

A task label can be a group (`isGroup`), and labels can sit in one (`parentId`). A group cannot
be applied to a task; an `exclusive` group (the default) allows one of its labels per task, and
a second is refused with Linear's own message ("The label 'ui' is in the same group as 'api'.
Only one label in a group can be applied to an task."). A label filter on a group matches the
group's labels, in Linear's stored shape (`{labels: {or: [{name}, {parent: {name}}]}}`), so
`{"labels": {"includesAny": ["area"]}}` selects tasks labelled `api` or `ui`. Group a list by a
label group with `display.grouping: "labelGroup"` and `display.groupingLabelGroupId`; a drag
between its groups swaps the task's label in that group and keeps the others.

## Plain-language filters

`viewFilterSuggestion(prompt, kind, teamId)` turns a request ("unassigned bugs due this week",
"overdue tasks assigned to me") into view filters; Linear's `issueFilterSuggestion` answers the
same in Linear's filter shape. The filter menu offers it as its last row, `AI filter "…"`, and
the result replaces the chips, which stay ordinary and editable.

It answers in two tiers (`hoja_server/ai_filter.py`): a parser that knows the workspace's own
label, status, member and project names and answers in milliseconds; and, for words it could
not read, the workspace's configured model (the provider Agent Chat uses), whose answer must
pass the same validator as a saved view, with one retry carrying the validator's message. A
request that names nothing concrete gets no filter and a message, never a guess: any unread
word — a negation above all — makes the parser abstain. Evaluation: 54 written cases
(`tests/fixtures/ai_filter_eval.json`, all pass) and a 60-case set written without access to
the parser (right 44, abstained 13, wrong 3 — two of the three are alternative readings).

## Sharing and visibility

| `visibility` | Who may read | Who may change or delete |
| --- | --- | --- |
| `private` | only `owner` | only `owner` |
| `workspace` | every member of the organization | `owner`, workspace admins |
| `team` | members of the view's team | `owner`, workspace admins |

Knowing a view's id or URL grants nothing: `view(id:)` resolves through the same visibility
predicate as `views`, so a private view is `NOT_FOUND` for anyone but its owner, and the id
never appears in their `views` list. `visibility: "team"` requires `scope: "team"`, so a
team-shared view always names its team.

Favorites are per user (`saved_view_favorite`): two members can favorite the same shared
view independently, and unfavoriting it for one leaves the other untouched.

## Browser-local views

Before this backend existed the browser kept views in `localStorage` under
`hoja_saved_views_<organizationId>` (workspace views) and `hoja_project_views_<projectId>`
(project views). On the first load of a workspace, the frontend posts each stored row once
through `viewCreate` — workspace rows as `kind: "issue"`, project rows as `scope: "project"` —
then records `hoja_views_migrated_<organizationId>` and removes the legacy keys. The marker
makes the migration run exactly once; a row that fails validation is skipped, not retried on
every load, and never deletes the server rows it did not create.

## Multi-tenancy

Every row carries `organization_id` and every query filters on it, exactly like the rest of
`store.py`; a view cannot reference a team, project, or initiative from another organization
because those references are validated against `organization_id` before the row is written.
Row-level security (`tenant_context`) applies unchanged: `saved_view`, `view_preference` and
`view_subscription` carry `organization_id` and are not in the enumerated system paths, so a
request without tenant context reads no rows.

## Linear-compatible API

Linear clients can use Linear's names; they read and write the same `saved_view` rows:

| Linear | Hoja |
| --- | --- |
| `customView(id)` | `view(id)` (id or slug) |
| `customViews(first, after, includeArchived)` | `views`, as a connection (cursor is a view id) |
| `customViewCreate(input)` / `customViewUpdate(id, input)` / `customViewDelete(id)` | `viewCreate` / `viewUpdate` / `viewDelete` |
| `customViewHasSubscribers(id)` | `View.subscriberCount > 0` |
| `CustomView.modelName` (`Issue`, `Project`, `Initiative`) | `View.kind` |
| `CustomView.filterData` / `projectFilterData` | `View.filters`, translated |
| `CustomView.shared` | `visibility != "private"`; on input, `true` shares with the workspace, or with the team when `teamId` is set |
| `CustomView.viewPreferencesValues` | `View.preferences.effective` |
| `CustomView.organizationViewPreferences` / `userViewPreferences` | the default and user layers, as `{id, type, viewType, preferences}` |
| `CustomView.issues(filter:)` / `projects(filter:)` | `View.issues` / `View.projects`, the filter translated |

`CustomViewCreateInput` takes `name`, `description`, `icon`, `color`, `teamId`, `projectId`,
`initiativeId`, `ownerId`, `shared`, `filterData` and `projectFilterData`; a `projectFilterData`
makes a project view. Setting `teamId`, `projectId` or `initiativeId` to null on update moves the
view to the workspace.

Filters are translated by `hoja_server/view_linear.py`. Comparators map as `eq`→`is`,
`neq`→`isNot`, `in`→`isEitherOf`, `nin`→`isNeitherOf`, `lt`/`lte`/`gt`/`gte`→the number or date
comparisons, `contains`, `containsIgnoreCase`, `eqIgnoreCase`→`equalsIgnoreCase`, and `null`→`is`
/ `isNot null`; references take `id`, `key`, `name`, `email`, `displayName` and `isMe`;
`state.type` becomes `stateType`; `labels`, `subscribers`, project `members`, `initiatives`
and `accessibleTeams` take `some`/`none`; `children: {some: {}}` is `hasSubIssues`;
`team: {members: {some: {isMe: {eq: true}}}}` is `"myTeams"`; `and`/`or` nest. Anything else
— `startsWith`, `notContains`, `every`, team `name`, relation filters — is refused with
`BAD_USER_INPUT` naming the path, so a translated view never selects more than the original.
Reading back is exact in meaning: an alternative that spans fields (`"me"` or nobody, a
label by name or by id) becomes an `or`, `includesAll` becomes one `some` per value under
`and`, and translating the result back into Hoja's grammar selects the same rows.

## Linear parity

| Linear capability | Linear field | Hoja |
| --- | --- | --- |
| View kinds | `CustomView.modelName` (`Issue`, `Project`, `Initiative`) | `View.kind` (`issue`, `project`, `initiative`) |
| Task filter | `CustomView.filterData` | `View.filters` for `kind: "issue"` |
| Project filter | `CustomView.projectFilterData` | `View.filters` for `kind: "project"` |
| Initiative filter | `CustomView.initiativeFilterData` | `View.filters` for `kind: "initiative"` |
| Team scope | `CustomView.team` | `View.scope: "team"` + `View.team` |
| Project/initiative attachment | `CustomViewCreateInput.projectId` / `initiativeId` | `View.scope: "project"` / `"initiative"` + `View.project` / `View.initiative` |
| Workspace / all teams | no team | `View.scope: "workspace"` |
| Sharing | `CustomView.shared` | `View.visibility` (`private`, `workspace`, `team`) |
| Owner | `CustomView.owner` | `View.owner` |
| Creator, last editor | `CustomView.creator` / `updatedBy` | `View.creator` / `View.updatedBy` |
| Name, description | `CustomView.name` / `description` | same |
| Icon, color | `CustomView.icon` / `color` | same |
| URL slug | `CustomView.slugId` | `View.slugId` |
| Created/updated/archived | `createdAt` / `updatedAt` / `archivedAt` | same |
| Results | `CustomView.issues` / `projects` / `initiatives` | same connections |
| Display defaults | `ViewPreferences(type: "organization").preferences` | `View.display` |
| Layout, grouping, sub-grouping | `layout`, `issueGrouping`, `issueSubGrouping` | `display.layout`, `display.grouping`, `display.subGrouping` |
| Ordering and direction | `viewOrdering`, `viewOrderingDirection` | `display.ordering`, `display.reverse` |
| Visible properties | `issueField*` booleans | `display.visibleProperties` |
| Timeline zoom | `timelineZoomScale` | `display.timelineZoom` |
| Completed window | `showCompletedIssues` / `showCompletedProjects` | `display.completedWindow` |
| Board column order | `columnOrderBoard` / `columnOrderList` | `display.boardColumnOrder` |
| Favorite | `Favorite` node | `View.favorite` for the caller (`saved_view_favorite`) |
| Per-user display | `ViewPreferences(type: "user")` | `viewPreferences` / `viewPreferencesSet` (`view_preference`) |
| Subscribe to a view | `notificationSubscriptionCreate(customViewId)` | `viewSubscribe` (`view_subscription`) |
| Export as CSV | view menu → Export | `GET /api/views/<id>/export.csv` |
| Manual order | `Issue.sortOrder`, `prioritySortOrder` | `Task.rank`, `Task.priorityRank`; `taskMove` ([Manual order](#manual-order)) |
| Placement on status change | `Team.setIssueSortOrderOnStateChange` | `Team.issueSortOrderOnStateChange` |
| Label groups | `IssueLabel.isGroup`, `parent`, `children` | same fields on `TaskLabel` / `IssueLabel` ([Label groups](#label-groups)) |
| Group by label group | `issueGrouping: "labelGroup"`, `issueGroupingLabelGroupId` | `display.grouping: "labelGroup"`, `display.groupingLabelGroupId` |
| AI filter | `issueFilterSuggestion` | `viewFilterSuggestion`, and `issueFilterSuggestion` in Linear's shape |
| Duplicate | view menu → Duplicate… (editor draft, same name and visibility, pinned after the source) | same; `viewDuplicate` for API clients |

### Not supported yet

| Linear capability | Why not |
| --- | --- |
| Views in the open-source `hoja` server | The OSS server has the tables but no views API; its web client keeps views in `localStorage`. Everything on this page is the Cloud app. |
| Creating initiative views from the views list | A saved initiative view has a page (shared chips and Display panel over `View.initiatives`), but the views list, palette and editor still create task and project views only. |
| Grouping or ordering by agent, milestone, customer, SLA or time in status | Hoja has no milestones, customers or per-task agent column to group by; `display` rejects those keys. |
| Insights panel, in-view `Cmd/Ctrl F` search | Not built. |
| A `Float` `sortOrder` on tasks | Hoja's ranks are fractional-index strings (`rank`); a Linear client writing `sortOrder` floats is not accepted. |
| A favorited view as the custom home page | Not built; the home page is fixed. |
| Team "active tasks" notifications (the bell on a team's Tasks page) | Not built; follow a team view instead ([Subscriptions](#subscriptions)). |
| Pinned-view management permission | Any member who can read a team view may pin it; there is no separate permission. |
| `viewPreferencesCreate/Update/Delete`, `notificationSubscriptionCreate(customViewId)` | Hoja's `viewPreferencesSet`/`Reset` and `viewSubscribe` cover the same ground under Hoja names. |
| Task relation filters | Hoja does not track blocked/blocking/related/duplicate relations. |
| Feed-item views | `CustomView.feedItemFilterData` addresses updates; Hoja's `Store` has no feed-item read model. |
| Favorite folders and nested children | Linear's `Favorite.children` is a tree; Hoja's favorite is a flat per-user set. |
| Slack notifications per view | Hoja has no Slack connector for view events; subscriptions notify in the inbox. |
| `filters` (deprecated legacy shape) | Only `filterData`'s shape is stored; the deprecated `filters` field is not accepted. |
| Saved initiative views as an Enterprise feature | Hoja has no plan tiers; initiative views follow the same visibility rules as every other kind. |
| Initiative name predicates | The initiative grammar covers status, health, owner, lead team and label only. |

## Web client

Every task list in the web app — a team's Tasks tabs, My tasks, a project's tasks and the
workspace board — shares one filter bar and one Display panel (`frontend/src/views/`).

- **Filters** are chips over the grammar above: `F` opens a type-ahead menu of categories
  (status, status type, assignee, creator, priority, labels, relations, dates, project, cycle,
  estimate, subscribers, team, content) with per-value counts over the loaded rows; a chip's
  operator and values stay clickable; "Advanced filter" edits nested `and`/`or` groups.
  Dates take presets, an ISO date, or typed text ("10 days ago", "2 weeks", "next month"),
  stored as ISO-8601 durations. `filterTreeToChips` and `chipsToFilterTree`
  (`views/filterModel.ts`) are the only translation, and a predicate the bar cannot edit is
  kept verbatim. The list evaluates the same grammar over the local store
  (`views/filterEval.ts`).
- **The URL** carries the current filter as `?filter=<base64url of the JSON tree>`, so an
  unsaved filtered list can be shared; opening the URL restores it, switching tabs keeps it,
  and Back/Forward restore the filter of that history entry. Display options are not in the
  URL. Picking a date preset (or "No due date") for a field that already has a date chip
  replaces that chip rather than adding a second one.
- **Display options** (`Shift V`) are stored with `viewPreferences` under the page's
  `viewType`: `team:<teamId>:all|active|backlog`, `my-issues:assigned|created|subscribed|activity`,
  `project:<projectId>`, `board:all|active|backlog`, and for the Projects page `projects`
  (workspace) or `team:<teamId>:projects` (its layout, grouping, ordering, closed projects
  and summary). My tasks' Created and Subscribed tabs default to every completed task; the
  other task lists start at "Past week". Edits are debounced and written to the page they
  were made on, even when the tab changes before the write. The browser keeps the last answer
  in `localStorage` (`hoja_view_prefs:<viewType>`) only as an offline cache. A dot on the Display
  button marks options that differ from the default; "Reset" drops the viewer's layer and
  "Set default for everyone" writes the default.
- **Save as view** (the layers button beside the tabs, or `Alt V`) opens the new-view editor
  with the list's filters and display. `F`, `Shift V` and `Alt V` do nothing while a modal
  dialog is open. `Cmd/Ctrl B` switches list and board, and `T` collapses every group (or
  opens them all again).

### View pages

Saved views never filter in the browser: the editor previews a draft with `viewPreview`, and
a saved view runs `View.issues` / `View.projects`, both rendered through the same list and
board as the team pages, paged with `after` as the list scrolls.

- **Views list** (`/workspace/views/issues|projects`, a team's `/workspace/team/<key>/views/…`):
  Tasks and Projects tabs; rows grouped into "Personal views · Only visible to you"
  (`visibility: "private"`), "Workspace", one section per team and per project; Name, Owner,
  Created and Updated columns. Its own Display menu (ordering, direction, visible columns) is
  kept in this browser (`hoja_views_list_display`).
- **New view** (`…/new`): a full page with the icon and colour picker (icon set or emoji, nine
  swatches and a custom colour), name and description in place, "Save to" (Personal, Workspace,
  or one of the person's teams; a team's editor defaults to the team and its "Personal" is a
  private view of that team, so the preview stays on the team's tasks; a project's offers
  Personal or shared). A section's "+" on the views list opens the editor saving to that
  section; Cancel / Create view, and below it the live result with the filter bar,
  Display panel and Tasks/Projects tabs. While the name is untouched the first chip names the
  draft (`suggestViewIdentity`): "Priority is Urgent" gives "Urgent tasks", "Tasks with urgent
  priority" and the urgent icon.
- **Saved view** (`/workspace/view/<slugId>`; old `/workspace/views/issues/<id>` links and the
  `url` field's `/<urlKey>/view/<slugId>` redirect there): icon (changeable by the owner or an
  admin), name (click to rename), star (`Alt F`), and the "…" menu: Edit…, Duplicate…, Move to
  ▸ (Personal / Workspace / a team), Subscribe ▸ (a task is added; a task is completed or
  canceled), Pin to team tabs (team views), Copy link, Export tasks as CSV, Delete (after a
  confirm). The header shows the matching count. The details panel (toggle beside Display)
  shows visibility and owner (an owner or admin can hand it over) and an Assignees / Labels /
  Projects breakdown of the loaded rows; a row adds that filter.
- **Unsaved changes**: editing a saved view's filters re-runs it as a preview in the view's
  scope and shows "Clear" and "Save ▾" ("Save to this view", `Alt S`, owner or admin only;
  "Create new view…", `Alt V`). The edits are kept per person and view in this browser
  (`hoja_view_edits:<userId>:<viewId>`) until cleared or saved. "Export tasks as CSV" exports
  the saved view and says so while edits are pending. Display changes always go to the
  person's own preference layer; the owner's "Set default for everyone" (or saving the view
  with display changes pending) writes the view's `display`.
- **Team tabs**: a team's Tasks page shows All tasks, Active and Backlog, then the team's
  pinned views (`pinned`, in `sortOrder`); "Add new view" opens the editor inline and pins
  the view it creates (a view saved to the workspace from there opens on its own page instead).
  Keys `1`–`9` select a tab. Pinned tabs reorder by dragging, or with `Alt ←` / `Alt →` on a
  focused tab; the move writes `viewPin(sortOrder:)` (only the moved tab when every tab has an
  order, otherwise all of them, numbered 1..n).
- **Project views** (`/workspace/project/<id>/view`, `…/view/new`, `…/view/<slugId>`) use the
  same list, editor and page, scoped to the project.
- **Navigation**: the sidebar's Favorites lists each favorited view by icon and name (and the
  tasks starred in this browser); the command palette lists "Views" and matching views, and
  `O` then `V` opens it as a view switcher. The shared list behind them belongs to one person in
  one workspace and is dropped on a workspace switch or a new sign-in. Initiative views have no
  page here yet and are left out of these lists.

## Routes and shortcuts

| Path | Page |
| --- | --- |
| `/workspace/views/issues`, `/workspace/views/projects` | Views list (Tasks / Projects tabs) |
| `/workspace/views/issues/new`, `/workspace/views/projects/new` | New view editor |
| `/workspace/view/<slugId>` | A saved view; `/workspace/views/issues/<id>` and `/<urlKey>/view/<slugId>` redirect here |
| `/workspace/team/<key>/views/…` | A team's views list and editor |
| `/workspace/project/<id>/view`, `…/view/new`, `…/view/<slugId>` | A project's views |
| `/api/views/<id or slug>/export.csv` | CSV export (authenticated) |

| Key | Where | Action |
| --- | --- | --- |
| `F` | any task list or view | Open the filter menu |
| `Shift V` | any task list or view | Open the Display panel |
| `Alt V` | any task list or view | Save as a new view ("Create new view…") |
| `Alt S` | a saved view with unsaved edits | Save to this view (owner or admin) |
| `Alt F` | a saved view | Favorite or unfavorite |
| `Alt D` | a saved view | Duplicate (opens the editor on a draft) |
| `Cmd/Ctrl Enter` | the view editor | Create the view |
| `J` / `K`, `↓` / `↑` | a task list | Move the focus between tasks; `Enter` opens the focused one |
| `Alt ↑` / `Alt ↓` | a focused task, manual or priority order | Move it one place — across a group edge too, setting the grouped field |
| `Alt Shift ↑` / `Alt Shift ↓` | a focused task, manual or priority order | Move it to the top or bottom of its group |
| `Cmd/Ctrl B` | any task list or view | Switch list and board |
| `T` | any task list or view, list layout | Collapse every group, or open them all |
| `1`–`9` | a team's Tasks page | Select a tab, pinned views included |
| `Alt ←` / `Alt →` | a focused pinned tab | Move the tab |
| `O` then `V` | anywhere | Open the view switcher |

None of these fire while a modal dialog is open or the focus is in a text field.

## Command line

`hoja issue list --view <name|slug|id>` runs a saved task view on the server and prints the
rows like any other list:

```bash
hoja issue list --view "Urgent tasks"                  # name, case-insensitive
hoja issue list --view urgent-tasks-3f2a9c1b -n 200    # slug (or id); pages past 250 rows
hoja issue list --view "Urgent tasks" --assignee me    # other flags narrow the view
```

The CLI reads `views(kind: "issue")` to find the view (an exact id or slug first, then a
unique name; two views with the same name ask for the slug) and pages
`customView(id).issues(filter:)` until `--limit`. The view's filters, scope and the caller's
effective display (ordering, completed window, sub-tasks) apply, and `"me"` is the person
the token belongs to. Every other list flag is sent as the query-time `filter`, ANDed with the
saved one. `--cycle current`, `--cycle next` and a cycle given by part of its name are first
resolved to ids through the `cycles` query (only cycles of teams you can see), so they match
exactly what the plain list matches. A flag the view grammar cannot express (an assignee or
creator given by part of a display name) is refused as a usage error naming the predicate,
never dropped. `--project` must name one project (UUID, slug or exact name).

## Tests

`tests/test_views.py`, `tests/test_views_exec.py` and `tests/test_views_parity.py` cover the
API, execution and the parity features; `tests/test_views_depth_order.py` (ranks, moves, random
and concurrent move sequences), `tests/test_views_depth_labels.py`, `tests/test_views_depth_dates.py`
(pinned clocks across midnight, DST and month ends, browser vs server) and
`tests/test_ai_filter.py` cover manual order, label groups, relative dates and plain-language
filters; `tests/test_cli_issue_view.py` drives
`hoja issue list --view` against a live server; `tests/test_view_filter_contract.py` serializes
every chip the web filter bar offers and checks the server grammar accepts it.
`npm run test:views` runs the filter, display and view-page models' table tests
(`frontend/scripts/test-view-filters.mjs`); `node scripts/test-view-pages.cjs` and
`node scripts/test-view-filter-bar.cjs` drive the view pages and the filter bar in a browser
against a seeded server.

## Validation errors

Validation failures are `BAD_USER_INPUT` with a `field` extension naming the exact path
— `filters.and[0].state.before`, `display.timelineZoom`, `scope` — the same contract
`parse_manifest` gives for `hooks[0].path`.
