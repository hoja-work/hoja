# GitHub linking

A Hoja issue should show the GitHub work that belongs to it: the branches, pull requests,
commits, deployments and checks that name its identifier. This document records the design and,
plainly, how much of it exists today.

**Implemented:** identifier detection (`hoja_server/github_linking.py`), the `github_link` and
`github_delivery` tables, the signed webhook receiver for `pull_request` and `push`, the
`Issue.githubLinks` GraphQL field, and the GitHub section on the issue page.

**Follow-up work, specified here but not built:** the backfill/poll for repositories without a
webhook, and the authenticated "deployed to prod at <sha>" endpoint. The `deploy` and `check`
link kinds are recorded in the data model and rendered by the panel, but nothing writes them yet.

## Why identifiers and not a mapping table

Hoja already writes the identifier into every branch it generates
(`<uid>/<identifier>-<slug>`, `hoja_server/writer.py`), and issues expose it as `branchName`.
GitHub text therefore already contains the join key, so linking needs no prior registration:
a pull request that mentions `HOJA-123` is evidence, whether or not anyone connected anything
in advance.

## Link detection

An identifier is `<TEAM>-<number>`. Detection is in `hoja_server/github_linking.py` and is
deliberately strict:

- **Only known team keys match.** `ISO-9001` and `HTTP-404` have the identifier's shape; without
  this rule they would create links to teams that never mentioned the work. Callers pass the
  keys they can resolve.
- **Identifiers must stand alone.** `XHOJA-1`, `HOJA-1x` and `HOJA_1` are rejected, so hashes and
  version strings do not become references.
- **Case-insensitive, normalised to upper case,** because Hoja lowercases the identifier when it
  generates a branch.
- **Closing keywords** `close`/`closes`/`closed`, `fix`/`fixes`/`fixed`,
  `resolve`/`resolves`/`resolved` mark a reference as closing, with an optional colon
  (`fixes: HOJA-1`). A closing reference is the only thing allowed to move an issue to Done.

Sources, in the order the panel prefers them: pull request title, pull request body, head branch,
commit message. A merge commit's subject contains the source branch, so work that reached the
default branch by merge is still detected.

## Ingestion

### Webhook (primary)

A GitHub App webhook posts to `POST /api/connectors/github/webhook`. Subscribed events:
`pull_request`, `push`, `issues`, `deployment`, `deployment_status`, `check_suite`.

The receiver must, in this order:

1. Read the raw body **before** parsing. The signature is over the bytes GitHub sent.
2. Verify `X-Hub-Signature-256` as `sha256=<hex HMAC>` over the raw body with the app's webhook
   secret, using a constant-time comparison. A missing or wrong signature is `401` and is never
   processed.
3. Reject a missing `X-GitHub-Delivery`.
4. Deduplicate on the delivery id: insert it into a `github_delivery` row with a primary key; a
   conflict means GitHub re-sent a delivery we already had, so answer `200` and stop.
5. Reject a stale delivery: `X-GitHub-Delivery` bodies carry a timestamp; compare against a
   five-minute window to blunt replay of a captured request.
6. Resolve the installation id to an organization. An unknown installation is `202` with no
   work performed, never an error GitHub would retry forever.

Steps 1–2 and 6 are the tests this change's design requires: bad signature, replay, unknown
installation.

### Backfill and poll (fallback)

Repositories where the webhook is not enabled are read with the existing installation token:
walk open pull requests and recent commits, run the same detection, and upsert. A poll on a
schedule keeps them fresh. This is why the connector is read-only today: linking needs no write
scope.

## Data model

A new table, `github_link`. The existing `attachment` table does not fit and is not reused: it
has `url`/`title`/`subtitle`/`content_type` but no `kind`, `external_id`, `state`, `author`,
`sha` or `environment`, and it models user-attached content rather than deduplicated
machine-ingested events.

| Column | Meaning |
| --- | --- |
| `id` | Row id. |
| `organization_id` | Tenant; every read is scoped by it under RLS. |
| `issue_id` | The linked issue. |
| `kind` | `pr`, `commit`, `issue`, `deploy` or `check`. |
| `external_id` | GitHub's id for the object; unique with `kind` and `organization_id` so re-delivery upserts. |
| `url`, `title`, `state`, `author` | Display fields straight from the payload. |
| `sha` | Commit, head, or deployed sha where applicable. |
| `environment` | Deployment environment (`production`, `staging`, …). |
| `created_at`, `updated_at` | Bookkeeping. |

`UNIQUE (organization_id, kind, external_id)` is the idempotency key and makes replay harmless.
In Cloud the table is org-scoped and protected by row-level security exactly as the other
connector tables are.

## Optional workflow automation

Off by default, configurable per team:

- pull request opened → move linked issues to In Progress;
- pull request merged and a closing keyword present → move linked issues to Done.

Both are opt-in per team because they change issue state as a side effect of an external event.
A merged PR without a closing keyword never moves anything.

## Deploys

Two inputs, one effect:

- **`deployment` / `deployment_status` events** record a `deploy` link with the environment and
  sha.
- **An authenticated endpoint** lets the release runbook declare
  "deployed to prod at `<sha>`". It resolves the commits contained in that sha, finds the linked
  issues, and records the deployment against each of them.

The endpoint is authenticated as a workspace actor so it cannot be driven by an anonymous caller,
and it is the only link-creating write path that is not a GitHub delivery.

## GitHub App settings the owner must change

The app **Hoja Connector** (App ID 5688507, slug `hoja-connector`) currently has **webhooks off**.
It is private and its redirect allow-list holds a single entry. This change sets none of that;
the owner must:

1. **Enable the webhook.** Set the webhook URL to
   `https://hoja.hoja.work/api/connectors/github/webhook` and generate a webhook secret, storing
   it as `HOJA_GITHUB_WEBHOOK_SECRET` on the server.
2. **Subscribe to events:** `Pull request`, `Push`, `Issues`, `Deployment`,
   `Deployment status`, `Check suite`.
3. **Permissions, all read-only:** `Metadata: read` (always required), `Contents: read`,
   `Issues: read`, `Pull requests: read`, `Deployments: read`, `Checks: read`. No write
   permission is needed and none should be granted.
4. **Optionally**, to test from a non-production origin, add that origin's
   `/api/connectors/github/callback` to the redirect allow-list. GitHub compares redirect URIs as
   strings, so a local server cannot complete the OAuth flow until its callback is listed.

## Migration numbering

The schema is versioned by an integer `CURRENT_SCHEMA_REVISION` applied from the ordered
`_MIGRATIONS` map, not by SQL files. OSS `origin/main` is at **49**, so the link table is
revision **50** and the delivery ledger **51**. Cloud `origin/main` is at **50**, so the same
pair is **51** and **52**.

Revision numbers must form a contiguous prefix: `migrate()` indexes `_MIGRATIONS[revision]` for
every number up to `CURRENT_SCHEMA_REVISION`, so the pair cannot start at 52 without inventing a
gap, and a placeholder revision is unsafe because a database that recorded it would later skip
another change's real migration. HOJA-280 reserves **51 and up** in Cloud; if that lands first
these two renumber to **53** and **54** at merge, which is the practice this file's history
already records.

## Consequences

- Read-only app permissions stay read-only; linking adds no write scope.
- Every ingestion path is idempotent on `(kind, external_id)`.
- Detection is pure and unit-tested, so a false positive is a parser test failure rather than a
  production surprise.
- Unregistered repositories are still covered by the backfill, but with more delay than a
  webhook.
