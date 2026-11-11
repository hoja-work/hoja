# Extensions

An extension is any HTTPS service that automates Hoja. One manifest installs everything it
needs: a bot identity with a scoped API key, event subscriptions, hooks that can adjust or
refuse task changes before they are saved, and actions people and agents can run on tasks.
Hoja never runs extension code; it sends signed requests to the extension, and the extension
calls back through the normal GraphQL API with its own key.

The model follows Shi, Zhang and Cui, *A Programming Paradigm for Spatiotemporal
Composability* (arXiv:2608.25512): every registration an install makes is recorded with its
inverse, uninstall replays those inverses in reverse order, and an update reconciles
registrations by key so the result depends only on the final manifest.

## Manifest

```json
{
  "key": "worktrees",
  "name": "Worktree provisioner",
  "version": "1.0.0",
  "url": "https://ext.example.com",
  "access": "read_write",
  "events": [{ "key": "issues", "resourceTypes": ["Issue"], "teamId": "ENG", "path": "/events" }],
  "hooks": [{ "key": "guard", "on": "issue.beforeCreate", "path": "/hooks/guard", "failurePolicy": "allow" }],
  "actions": [{ "key": "provision", "title": "Provision environment", "entity": "Issue", "path": "/actions/provision" }]
}
```

| Field | Meaning |
| --- | --- |
| `key` | Stable identifier (`a-z`, `0-9`, `-`); cannot change on update. |
| `url` | The extension's base URL. Every `path` is appended to it; absolute URLs are rejected. |
| `access` | `read` or `read_write` for the bot's API key. `read` keys cannot run mutations. |
| `events` | Event subscriptions; each takes `teamId` (id or key) or `allPublicTeams: true`. Default path `/events`. |
| `hooks` | `issue.beforeCreate` or `issue.beforeUpdate` interceptors, called in manifest order. |
| `actions` | Commands shown in the task menu and exposed as the MCP tool `hoja_extensionActionRun`. |

Unknown fields, duplicate keys, and invalid paths are rejected with `BAD_USER_INPUT` and the
manifest path in `extensions.field` (for example `hooks[0].path`).

## Install, update, uninstall

Workspace admins manage extensions in **Settings → Extensions** or through GraphQL:

```graphql
mutation($m: JSON!) { extensionInstall(manifest: $m) { success apiKey signingSecret extension { id } } }
mutation($id: String!, $m: JSON!) { extensionUpdate(id: $id, manifest: $m) { success } }
mutation($id: String!) { extensionUninstall(id: $id) { success } }
```

- `apiKey` and `signingSecret` are returned once, at install.
- Install and update each run in one transaction: a manifest that fails validation changes nothing.
- Update keeps the same key, secret, and row ids for hooks, actions, and events whose `key`
  is unchanged; queued event deliveries survive.
- Uninstall revokes the key, deactivates the bot (it stays as the author of what it did),
  removes its event subscriptions with their pending deliveries, hooks, actions, and private-team
  seats. The extension can then be installed again.

The bot is not a member: it cannot sign in, cannot be made an admin, and is not listed among
members. It can see public teams, plus private teams named in its `events`.

## Requests from Hoja

Every request is a JSON `POST` signed like a the upstream API webhook: `Linear-Signature` is the hex
HMAC-SHA256 of the raw body with the signing secret, and the body carries `webhookTimestamp`
(Unix ms). Verify both (see [webhooks](webhooks.md#verify-requests)); `LinearWebhookClient`
from `@linear/sdk` works unchanged.

**Events** follow the [webhook format](webhooks.md). `actor` is set when Hoja knows who made the
change (`type` is `user` or `integration`) and `null` otherwise, for example for an archive. An extension never receives events for changes made
by its own bot.

**Hooks** (`Linear-Event: IssueHook`) receive the pending change and must answer within 2 seconds:

```json
{ "type": "Issue", "hook": "guard", "on": "issue.beforeUpdate",
  "input": { "stateId": "…" }, "data": { "…current issue…": "" }, "actor": { "id": "…", "type": "user" } }
```

| Reply | Effect |
| --- | --- |
| `{"allow": true}` | The change proceeds. |
| `{"allow": true, "patch": {"priority": 2}}` | The patch is merged into the input; the next hook sees it. |
| `{"allow": false, "message": "…"}` | The change is refused with `EXTENSION_REJECTED` and the message. |

Patches may set `title`, `description`, `priority`, `estimate`, `stateId`, `assigneeId`,
`labelIds`, `dueDate`, `projectId`, and `cycleId`. Values are checked exactly like client input
(`priority` must be an integer, `labelIds` a list of strings, `title` a non-empty string); a patch
with any invalid value is dropped as a whole and counts as a failure. Each applied patch is
recorded in the task history as an `extensionPatched` activity by the bot. Hooks never run for
changes made by the same extension's bot, nor for a create in a team the caller cannot see.

All hooks on one write share a 3-second budget. A timeout, non-2xx reply, malformed reply, or
disallowed or invalid patch is a failure: `failurePolicy: "allow"` (default) lets the change proceed,
`"block"` refuses it. After 5 consecutive failures a hook is skipped for 60 seconds, with its
failure policy applied. Failures are listed on the extension.

**Actions** (event type `IssueAction`) are run by any member who can see the task:

```json
{ "type": "Issue", "action": "provision", "runId": "…", "data": { "…issue…": "" }, "actor": { "…": "" } }
```

Reply within 8 seconds with any 2xx status and optionally `{"message": "…"}`; the message is shown
to the person who ran it. Actions run once, with no automatic retry, and every run is audited.
Start long work asynchronously and report back through the API (for example a comment).

## Example: per-task environments

A receiver that provisions a worktree, ports, and a database branch when a task is created
and tears them down when it is completed or canceled:

```python
import hashlib, hmac, json, os, subprocess, time
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = os.environ["HOJA_EXTENSION_SECRET"].encode()

class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        signature = hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
        event = json.loads(raw)
        if not hmac.compare_digest(signature, self.headers.get("Linear-Signature", "")) \
                or abs(time.time() * 1000 - event["webhookTimestamp"]) > 60_000:
            self.send_response(401); self.end_headers(); return
        issue = event["data"]
        if self.path == "/events" and event["action"] == "create":
            subprocess.Popen(["./pre.sh", issue["identifier"], issue["branchName"] or ""])
        elif (self.path == "/events" and "stateId" in event.get("updatedFrom", {})
                and issue["state"]["type"] in ("completed", "canceled")):
            subprocess.Popen(["./post.sh", issue["identifier"]])
        elif self.path == "/actions/provision":
            subprocess.Popen(["./pre.sh", issue["identifier"], issue["branchName"] or ""])
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"message": "started"}')

HTTPServer(("0.0.0.0", 8443), Receiver).serve_forever()
```

Run it behind HTTPS on port 443, or set `HOJA_WEBHOOK_ALLOW_PRIVATE_NETWORKS=1` on the Hoja
server for a receiver on your own machine or LAN. `pre.sh` can report ports and URLs back with
`HOJA_TOKEN=<apiKey> hoja comment add <identifier>`.

## Server configuration

| Variable | Effect |
| --- | --- |
| `HOJA_SECRET_KEY` | Required. Extension signing secrets are encrypted with it; install fails without it. |
| `HOJA_WEBHOOK_ALLOW_PRIVATE_NETWORKS=1` | Allows loopback, private, and link-local URLs, plain `http`, and ports other than 443. |
| `HOJA_WEBHOOK_WORKER=0` | Stops this process from delivering events (hooks and actions are unaffected). |
