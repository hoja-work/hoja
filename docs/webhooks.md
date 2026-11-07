# Webhooks

Hoja sends outgoing webhooks using Linear's wire contract, so existing Linear
webhook consumers (including `LinearWebhookClient` from `@linear/sdk`) work with
Hoja unchanged. Use them to run your own automation when work changes, for
example to provision a worktree, ports, and a database branch when an issue is
created and tear them down when it closes.

## Manage webhooks

Only workspace admins can read or manage webhooks, because the response includes
the signing secret. Use `hoja raw` or any GraphQL client:

```graphql
mutation {
  webhookCreate(input: {
    url: "https://hooks.example.com/hoja"
    teamId: "ENG"            # team id or key; or allPublicTeams: true
    resourceTypes: ["Issue"]
    label: "worktrees"
  }) { success webhook { id secret enabled } }
}
```

- `webhooks` / `webhook(id:)` list and read webhooks, including recent `failures`.
- `webhookUpdate(id:, input:)` changes `url`, `label`, `enabled`, `secret`, or
  `resourceTypes`. Re-enabling a webhook does not replay events from the time it
  was disabled.
- `webhookDelete(id:)` removes the webhook and its pending deliveries.
- `secret` is generated (`hoja_wh_…`) when omitted.

Webhooks created by an [extension](extensions.md) are managed through the extension and do not
appear here.

Only the `Issue` resource type is supported. Any other value is rejected with a
`BAD_USER_INPUT` error.

## Requests

Each event is an HTTP `POST` with a JSON body:

| Field | Meaning |
| --- | --- |
| `action` | `create`, `update`, or `remove` |
| `type` | `Issue` |
| `data` | The issue: `id`, `identifier`, `title`, `branchName`, `url`, `stateId`, `state {id name type color}`, `teamId`, `team {id key name}`, `assigneeId`, `labelIds`, timestamps, and more |
| `updatedFrom` | For `update`: the previous value of every changed field |
| `actor` | Who made the change when known: `{id, type, name, email}`, `type` is `user` or `integration` (an extension bot) |
| `url`, `createdAt`, `organizationId`, `webhookId` | Event context |
| `webhookTimestamp` | Send time in Unix milliseconds |

Headers: `Linear-Signature`, `Linear-Delivery`, `Linear-Event`, `Linear-Timestamp`.

A ticket is closed when an `update` has `stateId` in `updatedFrom` and
`data.state.type` is `completed` or `canceled`. Edits to an already-closed ticket
do not include `stateId` in `updatedFrom`.

## Verify requests

`Linear-Signature` is the hex HMAC-SHA256 of the raw request body, keyed with the
webhook secret. Compare it in constant time against the raw bytes, then reject a
`webhookTimestamp` more than 60 seconds from your clock.

```python
import hashlib, hmac, json, time

def verify(secret: str, raw_body: bytes, signature: str) -> dict:
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("bad signature")
    payload = json.loads(raw_body)
    if abs(time.time() * 1000 - payload["webhookTimestamp"]) > 60_000:
        raise ValueError("stale request")
    return payload
```

## Delivery guarantees

- **At least once.** A response other than 2xx, a timeout after 5 seconds, or a
  connection error is retried after 1 minute, 1 hour, and 6 hours, then recorded
  as a failure. `Linear-Delivery` stays the same across retries, so receivers can
  ignore duplicates. Redirects are not followed.
- **Ordered per webhook.** A webhook receives its events in commit order; a
  delivery waiting for a retry holds back later deliveries to that webhook until
  it succeeds or fails permanently.
- **No loss across restarts.** Events come from the server's change log. The
  dispatch position, the queued deliveries, and the state used for `updatedFrom`
  are committed together.
- **Coalescing.** Events are dispatched about once per second. Several changes to
  one issue in that window arrive as one event carrying the latest state; an issue
  created and closed in the same window arrives as a single `create`.

## Server configuration

| Variable | Effect |
| --- | --- |
| `HOJA_SECRET_KEY` | Required. Encrypts webhook signing secrets at rest. |
| `HOJA_APP_URL` | Base of the issue links in `url` and `data.url`. |
| `HOJA_WEBHOOK_ALLOW_PRIVATE_NETWORKS=1` | Allows loopback, private, and link-local targets, plain `http`, and ports other than 443, for receivers on your own machine or LAN. Without it, only `https` URLs on port 443 that resolve to public addresses are accepted, checked both on save and before each send. |
| `HOJA_WEBHOOK_WORKER=0` | Stops this server process from dispatching and sending. |
