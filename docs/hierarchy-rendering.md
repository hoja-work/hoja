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

The board groups by workflow state first, then nests inside each column with the same
`treeOrder` the list uses: a sub-task whose parent is in the same column is indented under it
(`data-depth`, 18px per level, with a 2px rail) and the parent card carries the same rollup.
A sub-task whose parent lives in another column stays a root in its own column and keeps the
`↳IDENT` parent chip, so it is never read as an orphan. The board rollup spans every column
(a done sub-task sits under Done even when its parent is in Todo), which is why it is computed
over the whole `grouped` set rather than per column; like the list it counts loaded children.

## Task page (`/workspace/task/<IDENT>`)

The breadcrumb carries the parent (`lin-crumb-parent[data-parent-path]`, clickable, so a child
navigates up) and the Sub-tasks section lists each child with its server-issued identifier and
a `completed/total` pill. Both identifiers and counts come from the server payload; nothing is
synthesised in the client.

## What is not true

- Rollups are not server aggregates: a parent whose children fall outside the loaded set shows
  the ratio for what is loaded.
- Identifiers are still flat and per-team (`ENG-42`), including for sub-tasks: the recursive
  `TEAM-NUMBER-LETTER-NUMBER` scheme in HOJA-33 is an allocator change, not a rendering one,
  and is not implemented here (see the verdict in the HOJA-33 report).
- `a/b` rollups count loaded children only, not a server aggregate.
