# Cycle metrics

What a cycle's numbers mean, why canceled work is reported next to delivery
instead of being deleted from the denominator, and how to check the numbers
without trusting the UI.

## Definition of record

Every issue currently attached to a cycle is in the cycle's **scope**. An issue
leaves scope in exactly two ways, and each has its own number:

| Term | Meaning | Code |
| --- | --- | --- |
| scope | issues attached to the cycle now | `cycleStats().scope` |
| delivered | `state.type === 'completed'` | `cycleStats().completed` |
| canceled | `state.type === 'canceled'` | `cycleStats().canceled` |
| open | scope − delivered − canceled | `cycleStats().open` |
| kept | scope − canceled: the work that was still expected at close | `cycleStats().kept` |

Three ratios, never substituted for each other:

| Ratio | Formula | Answers |
| --- | --- | --- |
| deliveryRate | delivered / kept | of the work we kept, how much landed |
| dropoutRate | canceled / scope | how much of the commitment we dropped |
| scopeCompletion | delivered / scope | did we do what we committed to |

`deliveryRate` is `null`, not `1.0`, when `kept === 0`: a cycle where everything
was canceled has no delivery rate, and must never render as 100%.

## Why canceled is not simply removed from the denominator

The tempting fix for "canceled issues are counting against me" is
`completed / (scope - canceled)`. That makes the number gameable at the exact
moment it is read: cancel the leftovers and the cycle becomes 100%.

For example, a closed cycle can have these totals:

```
issues in scope            204
delivered (completed)      162 · 79% of scope
canceled                   42 · 21% of scope
open                       0

old UI number              79% success (completed / scope)
delivery of kept work      100% (completed / 162 kept)
```

Both statements are true, and the pair is the honest answer: *every issue that
survived was delivered; one in five was dropped.* Reporting only the first
number rewards the dropping. Canceled issues remain visible instead of being
treated as noise or removed from the historical scope.

Different cancellation *reasons* (duplicate, superseded, no longer needed,
abandoned) deserve different treatment, but that distinction does not exist in
the data model yet: `issue.state.type` is a single `canceled` bucket. Until a
canceled-reason field exists, the metric keeps all of them visible and lets the
reader judge.

## What the UI shows

- Cycle list rows: `N% of scope` ring, `N completed`, `N canceled · N%`,
  and `N% of what it kept` when anything was canceled.
- Cycle progress legend: Scope / Delivered / Open / Canceled, all with the same
  denominator (scope), so the percentages add up.
- Burndown: an issue leaves the remaining line when it is delivered **or**
  canceled, at the time it happened. A fully closed cycle reaches zero.
- Breakdown rows (assignee, label, priority, project): `done` over kept work,
  with the bucket's canceled count appended.

## Checks

```bash
node scripts/test-cycle-metrics.mjs          # contract: definitions, adversarial cases, burndown
HOJA_API_URL=... HOJA_TOKEN=... \
  node scripts/check-cycle-metrics-live.mjs 3   # reconcile one real cycle against the server
```

The contract test pins the numbers by hand, including the adversarial case
("cancel everything") that any future simplification must keep failing.

## Known gaps

- `cycle.progress` on the server is written as `0` when a cycle is created and
  never recomputed (`hoja_server/cadence.py:206`), while
  `hoja_server/agent_prompt.py:86` tells agents to report it — so any agent
  asked for cycle progress reports 0%. The web UI ignores the field and computes
  locally; the API value is still wrong.
- Scope is the set of issues attached *now*. Issues added mid-cycle and issues
  rolled over into the next cycle are not separated here, so
  `scopeCompletion` compares delivery against the final scope rather than the
  scope the cycle opened with.
