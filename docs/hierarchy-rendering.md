# Hierarchy rendering (parent tasks, sub-tasks, tree layout)

Three surfaces render the `issue.parent` relation. They are deliberately different.

## Data

- `app/frontend/src/main.tsx` selects `parent { id identifier title }` in `issueFields`; the
  `Issue` type carries `parent?: {id, identifier, title} | null`.
- The server owns validity: `hoja_server/writer.py` requires the parent to be in the same
  organisation and team, accepts an id or an identifier, and rejects self-parenting and
  ancestor cycles. `Issue.children(first, after)` is a `(created_at, id)` cursor connection.

## List layout (the default)

The grouped list is unchanged: the same `my-issue-group` sections, headers, counts and spacing,
and each row keeps its full composition (status picker, priority, identifier, labels, assignee,
due, project, date). `treeOrder` only re-orders and offsets those rows, so a task nests under its
parent when both fall in the same group; a disclosure toggle sits in a 16px gutter that the row
reserves, and a completed/total rollup is appended inside the title cell. A child whose parent sits in another group keeps its `↳IDENT` chip
and does not drag the parent into that section (the parent backfill is gated behind
`fetchMissing` and off for grouped lists). It groups the same `visible` issues the board receives.

- Roots are issues whose parent is absent, filtered out, or outside the `issues(first:100)`
  snapshot. Missing parents are fetched by id (bounded to 40 per pass) and folded in, so a
  child nests under its parent even when the parent is not in the page. A parent that cannot
  be read is shown as a `↳IDENT` chip instead.
- Depth is communicated by a 22px indent step (10px at depth 0, 32px at depth 1, 54px at
  depth 2) and the disclosure chevron. There are no guide rails: previous versions drew
  `::before`/`::after` lines and they were removed on request.
- Collapsing a parent removes its descendants from the DOM (row count drops); state is
  per-surface and not persisted.
- The row rollup is `completed children / children` computed from the loaded children.

## Board (`Kanban`)

The board is unchanged by this work: it groups strictly by workflow state. A parent and a
child with the same status appear as two peers in that column, and a child in another status
appears in its own column. The board does not fetch or draw the parent relation, so it never
claims a nesting it cannot honour.

## What is not true

- The board does not fetch or show child counts; `GraphTeamIssues`' `showSubIssues` filter and
  `SubIssuePill` live in an unmounted module and are not part of the live surface.
- `a/b` rollups count loaded children only, not a server aggregate.
