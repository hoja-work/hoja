# Workspace templates

A workspace template is one JSON document that sets up a workspace's shared settings in a
single step: workspace labels, task templates, project templates, SLA policies, and the
notification kinds a member gets until they choose for themselves.

Workspace admins use them in **Settings → Workspace → Templates**. The same operations are
available over GraphQL to admins (`workspaceTemplates`, `workspaceTemplatePreview`,
`workspaceTemplateExport`, `workspaceTemplateApply`). The server lives in
`hoja_server/workspace_templates.py`. The ready-made templates are in
`hoja_server/workspace_template_builtins.py`.

## The document

```json
{
  "format": "hoja.workspace-template",
  "version": 1,
  "name": "Small product team",
  "description": "A few people shipping together.",
  "labels": [{"name": "Bug", "color": "#eb5757"}],
  "taskTemplates": [{"name": "Bug report", "defaultTitle": "Bug: ", "defaultLabels": ["Bug"]}],
  "projectTemplates": [{"name": "Feature launch", "state": "planned"}],
  "slaPolicies": [{"name": "Urgent", "appliesPriorities": [1], "firstResponseMinutes": 120,
                   "resolutionMinutes": 1440, "pauseStateTypes": ["backlog"]}],
  "notificationDefaults": {"issueUpdated": false}
}
```

Every section is optional. A template never carries a row id. Instead it names things in ways
that mean the same in every workspace:

| Section | Matched by | Portable references |
| --- | --- | --- |
| `labels` | name | `group` names a label group in the template or the workspace |
| `taskTemplates` | name | `defaultLabels` are workspace label names |
| `projectTemplates` | name | `state` is a project status name |
| `slaPolicies` | name | `pauseStateTypes` are workflow state types (`backlog`, `started`, …), resolved to every state of that type |
| `notificationDefaults` | one per workspace | notification kinds |

Hoja Cloud templates may also carry `knowledge` and `agentPolicyDefault` sections. This
server accepts and ignores them, so a Cloud template or export still loads.

Templates cover workspace-level settings only. Teams, people, projects, repositories,
connectors, secrets and egress hosts belong to a single workspace, so a template cannot name
them. The server rejects any field that would need one.

## Applying

- **Preview first.** `workspaceTemplatePreview` returns every step (`create`, `update` with
  the fields it changes, or `unchanged`) and writes nothing. `workspaceTemplateApply`
  recomputes the same plan inside one transaction and executes it. A step that fails
  validation rolls back the whole apply.
- **Additive.** Apply creates what is missing and updates the fields the template names. It
  never archives or deletes. A field the template leaves out keeps the workspace's value. A
  name that matches in a different case keeps the workspace's spelling, and an existing label
  never becomes or stops being a group.
- **Idempotent.** Applying the same template twice changes nothing the second time.
- **Same rules as the settings pages.** SLA policies and task templates go
  through the validators their own mutations use. A template cannot store a value those pages
  would refuse.
- **SLA clocks.** New or changed policies cover tasks created afterwards. With
  `startSlaClocks: true` (the "Start SLA clocks for open tasks now" option), apply also starts
  clocks for open tasks, exactly like a policy's own "Start clocks".
- **Audit.** An apply that changes something writes one `workspace.template.apply` audit event.
  It names the template and each changed step, never setting values.

## Defaults a template sets

One setting exists only as a workspace default, in the `workspace_default` table:

- **Notification defaults.** A member who has not set a notification kind gets the workspace
  default for it. A kind the default does not name stays on. A member's own choice in
  Settings → Notifications always wins.

## Export

`workspaceTemplateExport` returns the workspace's settings as a template document. It lists in
`omitted` whatever is team-scoped (team labels, team task templates and policies) and
therefore left out. Applying an export to another workspace rebuilds
the same workspace-level settings there.

## Ready-made templates

| Template | For |
| --- | --- |
| Agents do most of the writing | Workspaces where agents file most tasks. SLA policies do not pause in Backlog, and a 48-hour triage target stops when a task gets a priority. Status and update notifications are off by default. |
| Small product team | A few people shipping together. Bug and feature templates, and SLAs by priority that pause in Backlog. |
| Customer support desk | Customer requests. First-response targets in business days, a pause while waiting on the customer, and request labels. |
