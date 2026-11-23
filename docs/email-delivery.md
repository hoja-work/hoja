# Email delivery: sign-up, invites, and what actually proves it works

Two messages leave Hoja today: an **invitation** when an admin creates a member
invitation, and a **welcome** when a workspace is created. Both are sent from
an `@hoja.work` address, and both are rendered as text *and* HTML.

## The parts, and why they are separate

| Module | Owns | Must not |
|---|---|---|
| `mail_config.py` | reading the environment, fail-closed | do network I/O |
| `mail_templates.py` | wording, HTML, text, escaping | decide who receives what |
| `mail_outbox.py` | the queue, attempts, provider response | know the copy |
| `mail_events.py` | *when* an email is sent (invite, welcome) | render or transport |
| `email_adapter.py` | SMTP, STARTTLS and implicit TLS | choose a provider |
| `scripts/mail-doctor.py` | proving the outside world agrees | change anything |

`stores.invite_member` writes the member, seat and API key in one transaction;
the invitation code is a separate single-use row whose token is stored hashed.
The email carries the raw code once. Re-issuing an invitation deletes the old
one, so a new email is always the only valid link.

## Delivery is a queue, not a request

`mail_events.send_invite` enqueues a `mail_outbox` row keyed by
`sha256(template|org|recipient|code)`. The unique index means a retried request,
a double-clicked button, or a replayed API call sends **one** email. Delivery is
attempted immediately and, if it fails, the row stays `queued` with
`attempts`/`last_error` for the next drain:

```python
from hoja_server import mail_config, mail_outbox
mail_outbox.deliver_pending(engine, mail_config.current())   # cron every minute
```

A failure is never allowed to fail the invite: the member exists, the code
exists, and the UI still returns the link.

## Choosing the transport

**Cloudflare can send.** That was the open question, and the answer is not the
one Email Routing's own documentation gives: Email Routing is inbound only and
states plainly that Cloudflare "does not process outbound email". Outbound is a
different product — Cloudflare Email Service / Email Sending (beta, Workers
Paid) — which submits over a REST API, a Workers binding, or authenticated
SMTP at `smtp.mx.cloudflare.net:465` (implicit TLS, username `api_token`,
password = an API token with **Email Sending: Edit**). It signs DKIM and ARC for
the sending domain and requires that domain to be onboarded on the account.

Consequences that shaped the code:

- SMTP submission is **465 with implicit TLS only**; STARTTLS on 587 is not
  offered. `SMTPConfig.implicit_tls` exists for exactly this, and chooses
  `smtplib.SMTP_SSL`.
- The sending domain must be onboarded before `not_entitled` becomes a success;
  until then the API answers `403 10105`.
- Delivery status is per recipient (`delivered` / `queued` /
  `permanent_bounces`) and is stored on the outbox row.

The alternative, and the current fallback, is Google Workspace: `hoja.work`'s
MX is Google, SPF already includes `_spf.google.com`, and DKIM is published at
`google._domainkey.hoja.work`. That works with `HOJA_MAIL_TRANSPORT=smtp`, but
it sends as a mailbox, needs a relay or app password, and has weaker per-message
observability than the provider API.

## Configuration

Disabled is the default and sends nothing:

```
HOJA_MAIL_TRANSPORT=disabled
```

Cloudflare:

```
HOJA_MAIL_TRANSPORT=cloudflare
HOJA_MAIL_FROM=notifications@hoja.work      # must be on HOJA_MAIL_DOMAIN
HOJA_MAIL_DOMAIN=hoja.work
HOJA_MAIL_REPLY_TO=support@hoja.work        # a monitored mailbox, not noreply@
HOJA_MAIL_APP_URL=https://hoja.work
CLOUDFLARE_ACCOUNT_ID=…
CLOUDFLARE_EMAIL_API_TOKEN=…                # Email Sending: Edit
```

Any SMTP server:

```
HOJA_MAIL_TRANSPORT=smtp
HOJA_MAIL_SMTP_HOST=smtp.mx.cloudflare.net
HOJA_MAIL_SMTP_PORT=465
HOJA_MAIL_SMTP_TLS=implicit
HOJA_MAIL_SMTP_USERNAME=api_token
HOJA_MAIL_SMTP_PASSWORD=…                   # the API token
```

A half configuration (token without account, username without password, a
`From:` on a lookalike domain, an `http://` app URL) raises at startup instead
of degrading silently, because a forgotten variable must not become "email
quietly stopped".

## DNS, and what is on the zone today

Verified 2026-11-07 against public DNS:

| Record | State |
|---|---|
| MX | 5 Google Workspace hosts — the human mailboxes |
| SPF | `v=spf1 include:_spf.google.com ~all` |
| DKIM | `google._domainkey` present (Workspace) |
| DMARC | **absent** — no policy, no aggregate reports |

The DMARC record is declared in `Hoja-Homepage/infra/cloudflare.tf`
(`v=DMARC1; p=none; rua=mailto:dmarc@hoja.work; fo=1`). It is monitor mode on
purpose: tighten to `quarantine` only after the aggregate reports are clean.
Cloudflare Email Sending publishes its own DKIM selector when the domain is
onboarded — that record is created by the dashboard/API, not by hand.

## Proof, not intention

### Verified on 2026-11-07 (hoja.work)

```
MX/MX6  PASS   Google Workspace unchanged
SPF     PASS   v=spf1 include:_spf.google.com ~all
DKIM    PASS   google._domainkey (Workspace) + cf-bounce._domainkey (sending)
DMARC   WARN   p=none, rua=mailto:luis@hoja.work  (monitor mode, deliberate)
SENDING PASS   cf-bounce.hoja.work: 3 MX, SPF, DKIM
SMTP    PASS   AUTH accepted on smtp.mx.cloudflare.net:465 as notifications@hoja.work
```

A real invite through `mail_events.send_invite` → `mail_outbox` → Cloudflare
produced `status=sent` with `provider_message_id`; calling the same event twice
enqueued nothing. Cloudflare reports the recipient as `queued`, the suppression
list is empty, and the apex MX still points at Google — onboarding a *sending*
domain did not disturb inbound mail.

```bash
.venv/bin/python scripts/mail-doctor.py --spf-include _spf.google.com
.venv/bin/python scripts/mail-doctor.py --smtp-check
.venv/bin/python scripts/mail-doctor.py --send-to you@your-inbox.example
```

The doctor reads public DNS over HTTPS, authenticates to the submission endpoint
without sending, and can send one real message. A configuration pass is not a
deliverability pass. The only proof that a receiver accepted the mail is *its*
`Authentication-Results` header:

```
Authentication-Results: mx.google.com;
  spf=pass smtp.mailfrom=notifications@hoja.work;
  dkim=pass header.d=hoja.work;
  dmarc=pass header.from=hoja.work
```

## Failure modes worth knowing

- **Read the error code first.** These three mean different things and only one
  of them is a code change:

  | Symptom | Meaning | Fix |
  |---|---|---|
  | `403 10203 email.sending_disabled` | the sending domain was never onboarded | Cloudflare dashboard → Compute → Email Service → Email Sending → **Onboard Domain** → `hoja.work` |
  | `550 5.7.1 Email sending is not enabled for domain hoja.work` (SMTP) | same cause, seen on the SMTP path | same |
  | `403 10105 not_entitled` | account is not on a plan that includes sending | Workers Paid |
  | `403 10102 forbidden` | the token lacks **Email Sending: Edit** | mint a token with that permission |

  Onboarding is dashboard-only as of 2026-11: the documented REST resources are
  `POST /accounts/{id}/email/sending/send` and `send_raw`, and
  `/accounts/{id}/email/sending/domains` returns `404 10001`. Onboarding also
  creates the `cf-bounce` MX records, a sending SPF, a sending DKIM selector, and
  a DMARC record — expect those four changes to the zone.
- **Accepted but not delivered.** SMTP `250` only means the submission server
  took it. Watch the outbox's `provider_message_id` and the provider's delivery
  log, not the API call.
- **Invitation link leaked.** Codes are single-use and expire in 72 hours; the
  row is deleted on re-task, so an old link stops working the moment a new one
  is created. Codes are stored hashed.
- **Domain reputation.** Transactional mail from the apex shares reputation with
  human mail. If volume grows, move application mail to a subdomain (for example
  `mail.hoja.work`) with its own DKIM selector and DMARC record.
- **Open-relay by proxy.** Invitations are admin-only and rate-limited by the
  existing member-management checks; the `To:` is always the invited member's
  stored address, never a request parameter.
