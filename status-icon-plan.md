# Plan: Clickable status icon with hover status history

## Current state (from code)
- `frontend/src/main.tsx`
  - `StatusIcon` (line ~196): static SVG, no click handling.
  - `StatusPicker` (line ~27): clickable trigger, dropdown, and "In status for X" tooltip. Used by the generic `IssueList` and by board cards in `Kanban`.
  - `MyIssuesContent` list rows (line ~230): status is a plain `<span>` around `StatusIcon`. Not clickable.
  - `timeInStatus` uses `stateChangedAt || updatedAt || createdAt`. `stateChangedAt` is already in `issueFields`.
  - `formatDuration` (line 25).
- Backend
  - `writer.py` ~L507-528: a `stateId` change records `issueUpdated` with `changes.stateId = {from, to}` in `issue_activity`. Only real changes are recorded.
  - `schema.graphql` L324: `Issue.activity(first, after)` returns `IssueActivity { eventType, actor, changes, createdAt }`. Resolver at `resolvers.py` L561.
  - Limitation: activity exists only from the event-log migration onward. Older status changes will not have history.

## Decisions (recommended defaults)
1. **Clickable icon in list rows.** Wrap the status icon in `StatusPicker` (reuse, do not duplicate) in both `IssueList` and `MyIssuesContent` rows. Board cards already have a picker; make the icon the trigger.
2. **Hover popover.** Replace the one-line tooltip with a compact popover on hover and focus containing:
   - Current status name and "In status for {formatDuration}" (from `stateChangedAt`).
   - Last 5 transitions as `from -> to · actor · relative time`.
   - Resolve state IDs to names and colors from the in-memory `teams` states. Fall back to "Unknown status".
3. **History data.** Fetch lazily on first hover, not in the Dashboard query (avoids N+1 across 100 issues).
   - Query `issue(id){ activity(first:20){ nodes{ eventType createdAt actor{name} changes } } }`, keep `issueUpdated` events with `changes.stateId`, and take the first 5.
   - Cache per issue ID in a `useRef` map. Clear on `revision` change.
   - No backend change required for v1.
4. **Accessibility.** Trigger keeps `aria-haspopup="listbox"` and `aria-label`. Popover uses `role="tooltip"`, opens on focus as well as hover, and Escape closes it.
5. **Touch.** Tap opens the picker. Hover is desktop only. History also appears in the picker header.
6. **Click isolation.** Trigger calls `stopPropagation` so clicking it does not open the issue.

## Steps
1. `main.tsx`: extend `StatusPicker` with an optional history loader and popover. Keep existing props working.
2. `main.tsx`: add `useIssueStatusHistory(issueId)` with a per-issue cache.
3. `main.tsx`: in `IssueList` and `MyIssuesContent` rows, replace the plain status span with `StatusPicker`. Pass `teams`.
4. `main.tsx`: wire `MyIssuesContent` status changes through the existing `moveIssueState`.
5. `styles.css`: compact popover (about 260px wide, small text, up to 5 rows).

## Verification
- `cd frontend && npm run build` (type check and build).
- Manual: click the icon in list and board, change status, confirm the row updates and the picker closes.
- Manual: hover the icon, confirm duration and transitions appear. Confirm an issue with no history shows an empty state.
- Manual: keyboard. Tab to icon, Enter opens picker, Escape closes.
- Manual: confirm clicking the icon does not open the issue.

## Risks
- **Nested buttons.** `my-issue-row` is a `<button>` and `StatusPicker` renders a `<button>`. Nesting is invalid HTML. Fix: make the row a `div` with a title button, as `IssueList` already does. This is a structural change to `MyIssuesContent` rows.
- Older issues have no transition events, so their history is empty.

## Open question for you
- History only covers changes since the event-log migration. Is that acceptable for v1, or do you want a server-side `stateHistory` field backfilled from `issue.state_changed_at`? (Recommended: v1 as planned.)
