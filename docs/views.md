# Views

A view is a saved query: it stores a scope, a filter tree, and the display options the
results are rendered with, so the same selection can be reopened, shared with a workspace
or a team, and reproduced from a URL. Hoja keeps views on the server (`saved_view`), not
in one browser: a private view belongs to its owner, a shared view belongs to the
workspace, and the two are the same entity with a different `visibility`.

The GraphQL surface lives in `hoja_server/views_api.py`; validation and compilation live in
`hoja_server/views.py`. Both follow the extension subsystem's conventions
([extensions](extensions.md)): a declarative document validated field by field, one error
type that names the offending path, and updates that replace the stored document inside a
single transaction.

## GraphQL

```graphql
query { views(kind: "issue") { id name scope visibility favorite filters display } }
query { view(id: "…") { id name issues(first: 50) { nodes { identifier title } } } }

mutation($input: ViewCreateInput!) { viewCreate(input: $input) { success view { id slugId } } }
mutation($id: String!, $input: ViewUpdateInput!) { viewUpdate(id: $id, input: $input) { success } }
mutation($id: String!) { viewDelete(id: $id) { success } }
mutation($id: String!) { viewFavoriteToggle(id: $id) { success favorite view { id favorite } } }
```

| Field | Meaning |
| --- | --- |
| `id` | Stable identifier. |
| `name` | Display name, 1–80 characters. |
| `description`, `icon`, `color` | Optional presentation carried with the view. |
| `slugId` | URL fragment, unique inside the organization; generated from the name and id on create. |
| `kind` | `issue`, `project`, or `initiative` — which entity the filter addresses. |
| `scope` | `workspace`, `team`, `project`, or `initiative` — what the view is attached to. |
| `visibility` | `private`, `workspace`, or `team` — who may read it. |
| `shared` | `true` unless `visibility` is `private`. |
| `owner` | The member who owns a private view; also its creator. |
| `filters` | The filter tree, in the grammar below. Stored as `{}` for "everything". |
| `display` | The display options, in the table below. Every view carries its own defaults. |
| `favorite` | Whether the *requesting user* favorited this view. |
| `issues` / `projects` / `initiatives` | The items the view selects, compiled and executed server-side. |
| `archivedAt` | Set by `viewDelete`; the row is kept so a delete can be reversed. |

`viewDelete` archives rather than erases. `viewUpdate` with `archived: false` restores it, so
an accidental delete is reverts exactly like an extension uninstall does.

## Kinds

| `kind` | Filter addresses | Executed by |
| --- | --- | --- |
| `issue` | `IssueFilter` properties | `Store.issues` (team visibility + the existing predicate compiler) |
| `project` | `ProjectFilter` properties | `Store.projects` |
| `initiative` | `InitiativeFilter` properties | `Store.initiatives` |

A view's kind is fixed at creation; `viewUpdate` cannot move filters between kinds.

## Scope

| `scope` | Requires | Meaning |
| --- | --- | --- |
| `workspace` | nothing | Spans every team the viewer can see, i.e. Linear's "all teams". |
| `team` | `teamId` | Attached to one team's Tasks and Projects pages, scoped to that team. |
| `project` | `projectId` | Attached to one project, rendered as a tab next to its Tasks tab. |
| `initiative` | `initiativeId` | Attached to one initiative. |

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
| `includesAny`, `includesEither` | `labels` | `in` |
| `includesNone`, `includesNeither` | `labels` | `nin` |

Every other operator/property pair is rejected with `BAD_USER_INPUT` and `field` naming
the path, for example `filters.and[0].state.before`.

### Task properties

| Property | Value | Operators |
| --- | --- | --- |
| `team` | team id or key | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `state` | state name | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `assignee`, `creator` | user id, email, or display name | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `project` | project id or name | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `cycle` | cycle id or name | `is`, `isNot`, `isEitherOf`, `isNeitherOf` |
| `parent` | task id | `is`, `isNot` |
| `priority` | `0`–`4` | `is`, `isNot`, `isGreaterThan`, `isGreaterThanOrEqual`, `isLessThan`, `isLessThanOrEqual`, `isEitherOf`, `isNeitherOf` |
| `number` | integer | `is`, `isNot`, `isGreaterThan`, `isGreaterThanOrEqual`, `isLessThan`, `isLessThanOrEqual`, `isEitherOf`, `isNeitherOf` |
| `identifier`, `title`, `description` | text | `is`, `isNot`, `contains`, `containsIgnoreCase`, `equalsIgnoreCase` |
| `createdAt`, `updatedAt`, `dueDate` | ISO date or date-time | `is`, `before`, `after`, `onOrBefore`, `onOrAfter` |
| `labels` | label name | `is`, `isNot`, `isEitherOf`, `isNeitherOf`, `includesAny`, `includesEither`, `includesNone`, `includesNeither` |

`labels.includesAll` is not accepted: Hoja's label predicate is one join against
`issue_label_link`, so "has every one of these labels" needs one predicate per label. Write
`{"and": [{"labels": {"is": "a"}}, {"labels": {"is": "b"}}]}` instead.

### Project and initiative properties

`kind: "project"` accepts `name` (`is`, `isNot`, `contains`), `slugId` (`is`, `isNot`), `state`
and `team` (`is`, `isNot`, `isEitherOf`, `isNeitherOf`). `Store.projects` has no lead, health,
or date predicate, so those properties are rejected rather than silently ignored.

`kind: "initiative"` accepts `status` only (`is`, `isEitherOf`), the only initiative predicate
`Store.initiatives` implements. Values are `active`, `planned`, `completed`, `canceled`.

## Display options

Each view stores the options its results render with. These are the view's *defaults*: opening
the view restores them, and editing them through `viewUpdate` changes what the view means.
Per-user overrides of a shared view (Linear's `ViewPreferences.type: "user"`) are not
implemented — see below.

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
  "showEmptyGroups": true
}
```

| Key | Values | Default |
| --- | --- | --- |
| `layout` | `list`, `board`, `timeline` | `list` |
| `grouping` | `none`, `status`, `assignee`, `creator`, `priority`, `project`, `cycle`, `labels`, `team` | `status` |
| `subGrouping` | same values as `grouping` (the swimlane axis; `none` means no lanes) | `none` |
| `ordering` | `manual`, `priority`, `updated`, `created`, `title`, `dueDate` | `updated` |
| `reverse` | boolean | `false` |
| `visibleProperties` | unique subset of the kind's property list | all properties |
| `timelineZoom` | number, `0.25`–`8` | `1` |
| `completedWindow` | `none`, `week`, `month`, `all` | `week` |
| `boardColumnOrder` | list of group keys, at most 50 | `[]` |
| `showEmptyGroups` | boolean | `true` |

Visible properties: for tasks `identifier`, `title`, `status`, `assignee`, `creator`,
`priority`, `labels`, `project`, `cycle`, `team`, `dueDate`, `estimate`, `createdAt`,
`updatedAt`; for projects `name`, `status`, `lead`, `teams`, `startDate`, `targetDate`,
`health`, `progress`; for initiatives `name`, `status`, `owner`, `targetDate`, `projects`.

Only `ordering` values `updated` and `created` are pushed down to SQL, because
`Store.issues` orders by those two columns; the other orderings are stored for the client to
apply. `View.issues` therefore returns rows ordered by the display's ordering when it is one
of those two and by `updatedAt` otherwise.

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
Row-level security (`tenant_context`) applies unchanged: `saved_view` is not in the
enumerated system paths, so a request without tenant context reads no rows.

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

### Not supported yet

| Linear capability | Why not |
| --- | --- |
| Per-user display overrides on a shared view | `ViewPreferences.type: "user"` needs a preference row per (view, user); Hoja stores one `display` document per view. |
| Feed-item views | `CustomView.feedItemFilterData` addresses updates; Hoja's `Store` has no feed-item read model. |
| Favorite folders and nested children | Linear's `Favorite.children` is a tree; Hoja's favorite is a flat per-user set. |
| `filters` (deprecated legacy shape) | Only `filterData`'s shape is stored; the deprecated `filters` field is not accepted. |
| Saved initiative views as an Enterprise feature | Hoja has no plan tiers; initiative views follow the same visibility rules as every other kind. |
| Filter mirroring in the URL | The client is responsible; the server stores the canonical filters the URL is built from. |
| `labels.includesAll` | One join per predicate, see above. |
| Project lead/health/date predicates, initiative name/owner predicates | `Store` has no compiler for them yet. |

## Validation errors

Validation failures are `BAD_USER_INPUT` with a `field` extension naming the exact path
— `filters.and[0].state.before`, `display.timelineZoom`, `scope` — the same contract
`parse_manifest` gives for `hooks[0].path`.
