"""Reusable GraphQL fragments.

These keep our queries DRY and our model deserialization predictable —
if a model expects a field, the fragment selects it.
"""

USER_CORE = """
fragment UserCore on User {
  id
  name
  displayName
  email
  active
  isMe
  avatarUrl
  avatarSeed
}
"""

TEAM_CORE = """
fragment TeamCore on Team {
  id
  name
  key
  description
  color
  icon
  visibility
  parentTeamId
  timezone
}
"""

STATE_CORE = """
fragment StateCore on WorkflowState {
  id
  name
  color
  description
  position
  type
}
"""

LABEL_CORE = """
fragment LabelCore on IssueLabel {
  id
  name
  color
  description
}
"""

PROJECT_CORE = """
fragment ProjectCore on Project {
  id
  name
  description
  slugId
  icon
  color
  state
  progress
  startDate
  targetDate
  url
}
"""

CYCLE_CORE = """
fragment CycleCore on Cycle {
  id
  name
  number
  startsAt
  endsAt
  completedAt
  progress
}
"""

ISSUE_CORE = """
fragment IssueCore on Issue {
  id
  identifier
  title
  priority
  priorityLabel
  estimate
  url
  branchName
  number
  createdAt
  updatedAt
  dueDate
  completedAt
  canceledAt
  startedAt
  state { ...StateCore }
  assignee { ...UserCore }
  creator { ...UserCore }
  team { id key name }
}
"""

ISSUE_FULL = """
fragment IssueFull on Issue {
  ...IssueCore
  description
  project { ...ProjectCore }
  cycle { ...CycleCore }
  parent { id identifier title }
  labels { nodes { ...LabelCore } }
  subscribers { nodes { ...UserCore } }
}
"""

PAGE_INFO = """
fragment PageInfoCore on PageInfo {
  hasNextPage
  hasPreviousPage
  startCursor
  endCursor
}
"""


def assemble(*fragments: str) -> str:
    """Join a set of fragments deduplicating by name."""
    seen: set[str] = set()
    parts: list[str] = []
    for frag in fragments:
        # crude fragment name extraction
        name = frag.split("fragment ", 1)[1].split(" ", 1)[0]
        if name in seen:
            continue
        seen.add(name)
        parts.append(frag.strip())
    return "\n\n".join(parts)

# A decision's readable shape, shared by every decision query and mutation so the model
# deserializes the same fields everywhere (docs/decisions.md).
DECISION_FIELDS = """
  id
  question
  options
  context
  status
  answered
  askedByAgent
  askedBy { id name email }
  answer
  chosenOption
  answeredBy { id name email }
  answeredAt
  createdAt
  updatedAt
"""
