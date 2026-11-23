"""Workspace templates that ship with Hoja (HOJA-742).

Each is an ordinary template document, so an admin can export one, edit it and
import the result. They only use what every workspace has: workflow state types,
the default project statuses and the notification kinds. They name no team,
person or project.
"""

from __future__ import annotations

DAY = 24 * 60

_AGENT_HOUSE_RULES = """\
These rules apply to every agent that works in this workspace.

1. **Write results, not progress.** Post one comment when a run ends, with what you found or \
changed and the evidence (links, commands, test output). Do not comment that a run started, \
stopped, resumed or was cancelled; that belongs in the run's activity, not in the thread people read.
2. **File real work only.** Do not create test, probe or smoke-test tasks in a team's board. \
Before filing, search for an open task with the same goal and add to it instead.
3. **Set a priority on every task you file.** A task with no priority sits in triage and \
counts against the triage SLA until a person accepts it.
4. **Ask people through decisions.** When you need an answer, open a decision on the task and \
say what you will do by default. Do not bury a question in a long comment.
5. **Never write secrets.** Not a token, not part of one, not its length, and not where it is \
stored. Say "the credential for X was rotated" instead.
6. **Do not notify people about your own bookkeeping.** Mention or assign a person only when \
they must act.
"""

_HUMAN_HOUSE_RULES = """\
How this team uses Hoja.

- Every task gets a priority when it leaves triage.
- One task per outcome; split work into sub-tasks instead of long checklists.
- Comments are for decisions and results. Status lives in the task's state.
"""

_SUPPORT_HOUSE_RULES = """\
How the support desk works.

- Reply to every new request within the first-response target, even if only to say who owns it.
- Move a task to a Backlog state only while waiting on the customer; the clock pauses there.
- Link duplicates to the first report instead of answering twice.
"""

AGENTS_DO_THE_WRITING = {
    "format": "hoja.workspace-template",
    "version": 1,
    "id": "agents-do-the-writing",
    "name": "Agents do most of the writing",
    "description": "Agents file most of the work: a triage target, house rules and quiet notifications.",
    "labels": [
        {"name": "Source", "isGroup": True, "exclusive": True,
         "description": "Who found the work."},
        {"name": "Agent finding", "group": "Source", "color": "#5e6ad2",
         "description": "Found by an agent; a person has not reviewed it yet."},
        {"name": "Reported by a person", "group": "Source", "color": "#26b5ce"},
        {"name": "Needs a person", "color": "#eb5757",
         "description": "An agent is blocked until someone decides or acts."},
        {"name": "Bug", "color": "#f2994a"},
        {"name": "Improvement", "color": "#4cb782"},
    ],
    "taskTemplates": [
        {"name": "Agent finding", "description": "What an agent files when it finds work.",
         "defaultTitle": "Finding: ",
         "defaultDescription": "## What was found\n\n## Evidence\n\n## Impact\n\n## Suggested next step\n",
         "defaultPriority": 3, "defaultLabels": ["Agent finding"]},
        {"name": "Bug report", "defaultTitle": "Bug: ",
         "defaultDescription": "## Steps to reproduce\n\n## Expected\n\n## Actual\n",
         "defaultPriority": 2, "defaultLabels": ["Bug", "Reported by a person"]},
    ],
    "projectTemplates": [
        {"name": "Agent workstream", "state": "planned",
         "description": "A body of work agents carry out, with a person who accepts the result."},
    ],
    "slaPolicies": [
        {"name": "Triage: needs a person", "appliesPriorities": [0],
         "resolutionMinutes": 2 * DAY, "stopsOn": "prioritized", "pauseStateTypes": [],
         "cycleBound": False, "businessDaysOnly": False, "enabled": True},
        {"name": "Urgent", "appliesPriorities": [1], "firstResponseMinutes": 120,
         "resolutionMinutes": DAY, "pauseStateTypes": [], "cycleBound": False, "enabled": True},
        {"name": "High", "appliesPriorities": [2], "firstResponseMinutes": DAY,
         "resolutionMinutes": 7 * DAY, "pauseStateTypes": [], "cycleBound": True, "enabled": True},
        {"name": "Medium", "appliesPriorities": [3], "resolutionMinutes": 14 * DAY,
         "pauseStateTypes": [], "cycleBound": True, "enabled": True},
        {"name": "Low", "appliesPriorities": [4], "resolutionMinutes": 28 * DAY,
         "pauseStateTypes": [], "cycleBound": True, "enabled": True},
    ],
    "knowledge": [
        {"title": "House rules for agents", "body": _AGENT_HOUSE_RULES, "enabled": True},
    ],
    "agentPolicyDefault": {
        "tasksDelegatedOnly": True,
        "maxConcurrency": 1,
        "budgetAction": "queue",
    },
    "notificationDefaults": {
        "issueAssigned": True, "issueMention": True, "issueDecision": True,
        "issueComment": True, "issueStatusChanged": False, "issueUpdated": False,
        "issueCreated": False,
    },
}

SMALL_PRODUCT_TEAM = {
    "format": "hoja.workspace-template",
    "version": 1,
    "id": "small-product-team",
    "name": "Small product team",
    "description": "A few people shipping together: bug and feature templates and SLAs by priority.",
    "labels": [
        {"name": "Bug", "color": "#eb5757"},
        {"name": "Feature", "color": "#5e6ad2"},
        {"name": "Improvement", "color": "#4cb782"},
        {"name": "Design", "color": "#bb87fc"},
    ],
    "taskTemplates": [
        {"name": "Bug report", "defaultTitle": "Bug: ",
         "defaultDescription": "## Steps to reproduce\n\n## Expected\n\n## Actual\n",
         "defaultPriority": 2, "defaultLabels": ["Bug"]},
        {"name": "Feature request", "defaultTitle": "Feature: ",
         "defaultDescription": "## Problem\n\n## Proposal\n\n## How we will know it works\n",
         "defaultLabels": ["Feature"]},
    ],
    "projectTemplates": [
        {"name": "Feature launch", "state": "planned",
         "description": "From spec to release: design, build, test, launch."},
    ],
    "slaPolicies": [
        {"name": "Urgent", "appliesPriorities": [1], "firstResponseMinutes": 120,
         "resolutionMinutes": DAY, "pauseStateTypes": ["backlog"], "enabled": True},
        {"name": "High", "appliesPriorities": [2], "firstResponseMinutes": DAY,
         "resolutionMinutes": 7 * DAY, "pauseStateTypes": ["backlog"], "cycleBound": True,
         "enabled": True},
    ],
    "knowledge": [
        {"title": "How we work", "body": _HUMAN_HOUSE_RULES, "enabled": True},
    ],
    "notificationDefaults": {},
}

SUPPORT_DESK = {
    "format": "hoja.workspace-template",
    "version": 1,
    "id": "support-desk",
    "name": "Customer support desk",
    "description": "Customer requests with first-response targets in business hours.",
    "labels": [
        {"name": "Request type", "isGroup": True, "exclusive": True},
        {"name": "Question", "group": "Request type", "color": "#26b5ce"},
        {"name": "Problem", "group": "Request type", "color": "#eb5757"},
        {"name": "Feature request", "group": "Request type", "color": "#5e6ad2"},
        {"name": "Waiting on customer", "color": "#95a2b3"},
    ],
    "taskTemplates": [
        {"name": "Customer request", "defaultTitle": "Request: ",
         "defaultDescription": "## Customer\n\n## What they asked\n\n## What we told them\n",
         "defaultPriority": 3},
    ],
    "slaPolicies": [
        {"name": "Urgent request", "appliesPriorities": [1], "firstResponseMinutes": 60,
         "resolutionMinutes": 8 * 60, "pauseStateTypes": ["backlog"],
         "businessDaysOnly": True, "enabled": True},
        {"name": "Standard request", "appliesPriorities": [0, 2, 3, 4],
         "firstResponseMinutes": 4 * 60, "resolutionMinutes": 3 * DAY,
         "pauseStateTypes": ["backlog"], "businessDaysOnly": True, "enabled": True},
    ],
    "knowledge": [
        {"title": "Support desk rules", "body": _SUPPORT_HOUSE_RULES, "enabled": True},
    ],
    "notificationDefaults": {"issueUpdated": False},
}

BUILTINS = [
    {"id": t["id"], "document": t}
    for t in (AGENTS_DO_THE_WRITING, SMALL_PRODUCT_TEAM, SUPPORT_DESK)
]
