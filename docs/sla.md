# SLAs

An SLA policy gives a task a clock: a first-response deadline, a resolution
deadline, or both. The clock is what the task page and the issue list show, and
what a breach is recorded against. Policies are workspace settings
(**Settings → SLAs**) and only an admin can change them.

## What the clock does

A clock starts when a task is created in a scope a policy covers, and when
`applyToOpen` is asked for. It is paused while the task sits in one of the
policy's pause states, it records the first comment by someone other than the
task's creator as the first response, and it stops on the event the policy
names. Paused time is added back to both deadlines, so a pause is a real pause
rather than a paused-looking number.

A breach is derived on read and recorded once: the first read that observes a
passed deadline stamps `breachedAt` and writes one `slaBreached` activity row.
Pulse and the inbox therefore show one breach, not one per page view.

## The settings

| Setting | Effect |
| --- | --- |
| First response, minutes | The target for the first reply by anybody but the creator. Empty means the policy does not measure a response. |
| Resolution, minutes | The target for finishing. Empty means the policy does not measure resolution. |
| Priorities | Which priorities the policy covers. Empty covers every priority. |
| Scope | Whole workspace, or one team. A team's own policy wins over the workspace one for a task in that team. |
| Pause while in | States that pause the clock. A `Backlog` state here is what makes "waiting on somebody" not count against the target. |
| Resolution follows the cycle | When set and the task is in a cycle, the resolution deadline is the cycle's end plus paused time. A task without a cycle falls back to `Resolution, minutes`. |
| Count weekdays only | When set, minutes are counted Monday to Friday in the team's timezone (UTC when the team has none). The weekend contributes no minutes: a target raised on Saturday starts counting on Monday at the same local time. |
| Stops on | `Completed` (the default), `A started state` (the policy measures picking work up), or `A priority arrives` (a triage policy: an untriaged task must get a priority within the target). |
| Start clocks for tasks that are already open | `applyToOpen`. Starts a clock for every open task the policy covers, at the moment it runs. |

The same `applyToOpen` behaviour has a mutation of its own,
`slaPolicyApply(id)`, so an admin can apply a policy to the open backlog later
without saving it again.

## Re-targeting

A clock follows its task. When a task's priority, team or cycle changes, the
policy is matched again inside the same transaction as the write:

1. A deadline the clock should already have met is stamped as a breach first, so
   moving a task to the next cycle after its cycle ended is a breach and never
   an erasure of one.
2. A triage clock (`Stops on: a priority arrives`) resolves, and the policy for
   the new priority starts a new clock from that moment.
3. A different policy takes over, a cycle-bound deadline moves to the new
   cycle's end, or — when no policy matches any more, for instance because the
   only one was archived — the clock is detached and its state reads `detached`.

Each of those writes an activity row, so the task's history explains why its
deadline moved.

## The cycle preset

**Cycle preset** creates five workspace-wide policies in one press, all pausing
in every `Backlog` state:

| Policy | Targets |
| --- | --- |
| Urgent | 120 minutes to first response, 1440 minutes to resolution |
| High | 1440 minutes to first response; resolution follows the cycle, else 7 days |
| Medium | Resolution follows the cycle, else 14 days |
| Low | Resolution follows the cycle, else 28 days |
| No priority | A triage clock: get a priority within 2 days |

Nothing is applied until a policy exists, so a workspace that never opens this
pane has exactly the behaviour it had before.
