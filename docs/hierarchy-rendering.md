# Hierarchy rendering (parent tasks, sub-tasks, tree layout)

One derivation feeds both task surfaces: `orderTree` in
`app/frontend/src/graph/hierarchy.ts`. The list and the board call the same function, so they
cannot drift apart — an earlier revision had the board nesting while the list rendered flat,
because each surface carried its own copy of the walk.

## Data

- `app/frontend/src/main.tsx` selects `parent { id identifier title }` in `issueFields`; the
  `Issue` type carries `parent?: {id, identifier, title} | null`.
- The server owns validity: `hoja_server/writer.py` requires the parent to be in the same
  organisation and team, accepts an id or an identifier, and rejects self-parenting and
  ancestor cycles.
- Tasks carry **no icon field**. `icon` exists only on `team`, `project`, `project_template`
  and `saved_view` (`hoja_server/db.py`), and holds an emoji string.

## Derivation

`orderTree(items, collapsed)` returns, for every task: `node`, `depth`, `isLast`,
`openRails` (depths of ancestors whose rail continues past this row), `childCount`,
`doneCount` and `parentOutside`.

- Roots are tasks whose parent is absent, filtered out, or outside the loaded set. A parent
  that cannot be nested keeps the `↳IDENT` chip and is flagged `parentOutside` rather than
  reading as an orphan.
- Children are ordered **oldest first**, so a sub-task never renders above the task that
  created it even though the list itself sorts newest first.
- A collapsed parent drops its descendants from the output, so the rendered row count falls.

## List layout (the default)

- Depth is communicated by a **24px step** (`SIBLING_STEP`) off a **42px** inset
  (`ROW_INSET`), drawn as **2px `bg-line` rails**: one per open ancestor, then the row's own
  connector — a T-junction (`through` rail plus a 10px arm) for a middle child, a
  `rounded-bl-[6px]` corner for the last. The corner closes an only-child too.
- The leftmost cell of a row is a **16px reserved disclosure gutter** so columns line up
  whether or not a row has children; the chevron renders only when `childCount > 0`.
- The status control renders `StateGlyph` (`app/frontend/src/graph/stateGlyph.tsx`): Phosphor
  filled glyphs mapped from the workflow state type, imported per icon so the bundle carries
  only the five that are used. Empty states stay outline; started/completed/canceled are
  solid. The glyph sits inside the existing `StatusPicker`, so the status menu still works.
- A parent appends a `SubIssuePill` (`done/total`) inside its title cell.
- Collapse state is per-surface and deliberately not persisted: a collapsed epic in one view
  must not hide work in another.

## Board (`Kanban`)

Same `orderTree` call, then the board applies its own presentation: 18px per level with a 2px
rail, and `parentIdent` when `parentOutside`. The board rollup spans every column (a done
sub-task sits under Done even when its parent is in Todo), so it is computed over the whole
grouped set rather than per column; like the list it counts loaded children.

## Task page (`/workspace/task/<IDENT>`)

The breadcrumb carries the parent (`lin-crumb-parent`, clickable, so a child navigates up).
The Sub-tasks section renders each child with the same rail geometry — one branch closing on
the last child — plus `StateGlyph` and the title stacked over the first line of the
description. Identifiers and counts come from the server payload; nothing is synthesised in
the client.

## What is not true

- Rollups are not server aggregates: a parent whose children fall outside the loaded set shows
  the ratio for what is loaded.
- **Grandchildren are not loaded on the task page.** `subs` is one query,
  `issues(first:100, filter:{parent:{id:{eq:detail.id}}})`, so the tree there is depth 1 by
  construction. Deeper nesting needs a second bounded fetch.
- Identifiers are still flat and per-team (`ENG-42`), including for sub-tasks: the recursive
  `TEAM-NUMBER-LETTER-NUMBER` scheme is an allocator change, not a rendering one.
- Keeping a family together across *status* sections is a grouping change, not a rendering
  one; today such a child stays a root in its own section with the `↳IDENT` chip.
