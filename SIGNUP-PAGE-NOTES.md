# The sign-up page: implementation notes

Branch `feat/signup-page`, worktree `../hoja-signup-page`, cut from
`feat/google-signup-gate` (the newest product state). The server gate is
unchanged in behaviour; this page is its user-visible half. Specs:
`SIGNUP-GATE-NOTES.md` (state machine, reason codes, env) and
`Hoja-Cloud/docs/signup-security.md` (threat model, control placement).

Nothing here provisions anything, talks to Cloudflare or Google, or moves an
issue to Done.

## 1. What was built

| Piece | Where | What it does |
| --- | --- | --- |
| Public configuration endpoint | `hoja_server/app.py` → `GET /api/signup/config` | Returns the Turnstile **site** key, the current `HOJA_SIGNUP_MODE`, the Google sign-in start URL, the widget script URL and `available`. No secret, ever. `cache-control: no-store`. |
| Public site key in config | `hoja_server/signup_config.py` → `TURNSTILE_SITE_KEY` | Optional, validated as a bare token, never part of `configured`, and refused at startup if it equals `TURNSTILE_SECRET_KEY`. |
| One reason-code list | `hoja_server/signup_gate.py` → `SIGNUP_REASON_CODES` | `SignupReason` values + `cross_site_request` + `invalid_request`; the route now uses those two constants instead of string literals. |
| The page | `frontend/src/signup/SignupPage.tsx` (+ `signup.css`) | `/signup` in the existing SPA. |
| Slug rule, generated | `scripts/generate-signup-slug-policy.py` → `frontend/src/signup/slugPolicy.generated.ts` | The regex, the length limit and the reserved names are generated from `hoja_server/signup_policy.py`, not retyped. |
| Client validation | `frontend/src/signup/slugValidation.ts` | Same check **order** as `validate_slug`, so the page and the gate refuse the same input with the same code. Pure, DOM-free. |
| Reason copy | `frontend/src/signup/reasonCopy.ts` | Human title + message for every server code; unknown codes fall back to the server's own message. |
| Submit handler | `frontend/src/signup/submitSignup.ts` | `POST /api/signup` with `credentials: 'include'` and `{slug, turnstile_token}`; never throws. |
| Widget loader | `frontend/src/signup/turnstile.ts` | One module-level promise → the script is injected once; explicit `render()`, never implicit mode. |
| Route | `frontend/src/main.tsx` | `/signup` renders before the auth gates, so both an anonymous browser and a google_verified one land on the page. |

Layout reuses the existing auth surface rather than inventing a parallel one:
`.onboarding-page` / `.onboarding-card` / `.onboarding-brand` (onboarding.css) and
`.primary` / `.error` / `.login-help` (styles.css). `signup.css` adds only the
gate's own pieces (hint, notice, alert, challenge box). No new layout system.

## 2. The endpoint the page reads

```
GET /api/signup/config        200, cache-control: no-store
{
  "mode": "closed" | "invite" | "open",
  "available": true | false,          # a widget can be rendered AND its token verified
  "sitekey": "0x4AAAAA…" | null,
  "signInUrl": "/auth/google/start?return_to=%2Fsignup" | null,
  "widgetScriptUrl": "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"
}
```

`available = configured and bool(turnstile_site_key)`. Both halves are needed: a
site key with no server secret renders a widget whose token the gate can never
spend, which is worse than saying "unavailable". `tests/test_signup_page.py`
asserts the **exact key set**, so a field added here without review fails.

Never in the body: `TURNSTILE_SECRET_KEY`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_CLIENT_ID`, `TURNSTILE_EXPECTED_HOSTNAME`, the tenant registry, or the
string `secret`. Asserted by `test_public_config_leaks_no_secret` (distinctive
sentinel values) and, for the bundle, by the `rg` gate below plus
`test_no_secret_can_reach_the_bundle`.

The live widget in the `hoja.work` account is `hoja-signup`, sitekey
`0x4AAAAAAF0T6-JuMDYB9gPf`, domain `hoja.work` (apex only — a wildcard was
rejected by the API). Set `TURNSTILE_SITE_KEY` to that value in production.

## 3. What the page shows, per state

| Server state | Page |
| --- | --- |
| `mode=closed` (the default) | "Sign-ups are invite-only" notice; **no form, no widget, no script request** |
| `mode=open`/`invite`, no site key | "Sign-up is unavailable" notice (fail closed); no widget is ever rendered |
| `mode=open`/`invite`, anonymous | One paragraph explaining the flow + "Continue with Google" → `signInUrl` |
| `mode=invite`, anonymous | Same, plus "invite-only: use the Google address that was invited" |
| google_verified | Slug field, Turnstile widget, submit |
| refusal | Title + message from `REASON_COPY`, announced in a persistent `aria-live="assertive"` region |
| eligible (201/200) | "Request received" + the reserved `slug.hoja.work` + the server's message |

`/api/signup` GET is what tells the page whether this browser is already
google_verified; the page never infers it from a token in `localStorage`.

## 4. Reason codes → page copy

All 17 codes in `signup_gate.SIGNUP_REASON_CODES`. The key set of
`REASON_COPY` is asserted **equal** to that list (`test_the_page_maps_every_reason_the_server_can_return`),
so a new server code fails the suite until the page has copy for it, and a page
entry the server does not define fails too.

| `reason` | Page title | Retry hint the page acts on |
| --- | --- | --- |
| `signup_unconfigured` | Sign-up is not configured | — |
| `not_authenticated` | Sign in with Google again | resets to the Google button |
| `signup_closed` | Sign-ups are invite-only | — |
| `domain_not_allowed` | This Google account is not accepted here | resets to the Google button |
| `slug_required` | Pick a workspace address | focuses the field |
| `invalid_slug` | That address cannot be used | focuses the field |
| `reserved_slug` | That address is reserved | focuses the field |
| `slug_taken` | That address is taken | focuses the field |
| `turnstile_missing` | Finish the human check | — (the click never leaves the page) |
| `turnstile_failed` | The human check failed | resets the widget |
| `turnstile_replayed` | That verification was already used | resets the widget |
| `invite_required` | An invitation is required | — |
| `identity_cap_exceeded` | This account already asked | — |
| `ip_cap_exceeded` | Too many requests from this network | — |
| `cross_site_request` | The request was blocked | — |
| `invalid_request` | The request could not be read | — |
| `eligible` | Your request is queued | (a member of the enum; delivered as `state`, never as `reason`) |

Unknown code (an older bundle against a newer server): `describeReason` returns
the **server's message verbatim** with the code in the title; with no message it
shows the code itself. There is no generic "an error occurred" anywhere — a test
asserts that string is absent.

The **login-endpoint** codes (`state_unknown`, `bad_signature`, …) are *not* in
this table: they come back from `GET /auth/google/callback` as JSON on a redirect,
never from `/api/signup`, so the page never receives them. Mapping them would
mean a second, differently-shaped surface (see §7).

## 5. How the page cannot drift from the server

1. **Slug rule** — `scripts/generate-signup-slug-policy.py` writes
   `slugPolicy.generated.ts` from `SLUG_RE`, `SLUG_MAX_LENGTH` and
   `RESERVED_SLUGS`. `test_the_generated_slug_policy_is_current` regenerates in
   memory and fails on any difference, and
   `test_the_page_and_the_server_agree_on_every_slug` feeds one corpus (34 cases)
   to both the real Python `validate_slug` and the TypeScript `checkSlug` (through
   `node scripts/test-signup-page.cjs --slug-check`) and compares the answers.
2. **Reason codes** — one list in `signup_gate.SIGNUP_REASON_CODES`, compared with
   the TypeScript table's keys (§4).
3. **Submit contract** — `scripts/test-signup-page.cjs` asserts the method, URL,
   `credentials: 'include'`, JSON content type and the `{slug, turnstile_token}`
   body, against a fake `fetch`.

## 6. Turnstile

- The script is requested with `render=explicit` and injected **once** per page
  load (a module-level promise), so a remount reuses the loaded API instead of a
  second `<script>`.
- `render()` into the page's own node with `{sitekey, action: 'signup', size: 'flexible', theme}`;
  the callback stores the token in React state, `expired-callback` clears it, and
  `error-callback` clears it and says the challenge could not run.
- After a refusal that spends or invalidates the token
  (`turnstile_missing`/`turnstile_failed`/`turnstile_replayed`) the page calls
  `turnstile.reset(widgetId)` for a fresh one — it never re-posts the old token.
- The widget is removed on unmount and on success.
- Client-side "success" is never trusted: the token is verified server-side
  (`hoja_server/turnstile.py`), which is unchanged.

## 7. Tests

`tests/test_signup_page.py` (19 tests, all pass) — real app + schema for the
endpoint tests, no network:

| Test | Proves |
| --- | --- |
| `test_public_config_is_exactly_the_public_fields` | the 5 keys, their values, `no-store` |
| `test_public_config_needs_no_cookie_and_starts_the_flow_on_this_host` | public, same-origin start URL |
| `test_public_config_leaks_no_secret` | sentinel secrets and the word `secret` are absent |
| `test_public_config_fails_closed_when_the_site_key_is_unset` | `available: false`, `sitekey: null` |
| `test_public_config_on_an_unconfigured_deployment` | mode defaults `closed`, everything null |
| `test_the_site_key_must_not_be_the_secret_key` | startup error for that mistake |
| `test_a_site_key_that_is_not_a_bare_token_is_refused` | URL/whitespace keys are a startup error |
| `test_the_site_key_does_not_change_whether_the_gate_is_configured` | the public key never enables the gate |
| `test_the_page_maps_every_reason_the_server_can_return` | set equality with `SIGNUP_REASON_CODES` |
| `test_the_server_reason_list_covers_the_route_level_refusals` | the two route codes are in the list |
| `test_an_unknown_code_is_not_silently_mapped_by_the_page` | membership test + no catch-all copy |
| `test_the_generated_slug_policy_is_current` | the generated file is up to date |
| `test_the_generated_policy_really_contains_the_server_rule` | pattern, limit, all 27 names |
| `test_the_page_and_the_server_agree_on_every_slug` | behaviour on a 34-case corpus |
| `test_the_page_scripts_pass` | the node suite below |
| `test_the_spa_routes_signup_to_the_page` | route wired, and before the login gate |
| `test_the_page_is_accessible_by_construction` | label, `aria-live` ×2, `aria-invalid`, `aria-describedby`, disabled-in-flight, three modes |
| `test_the_page_never_retries_a_missing_token_on_the_client` | reset, not re-post |
| `test_no_secret_can_reach_the_bundle` | the grep gate, encoded |

`scripts/test-signup-page.cjs` (15 checks, all pass, `node scripts/test-signup-page.cjs`) —
loads the pure TS modules with the project's own TypeScript compiler and asserts:
the rule comes from the generated file (and the regex is not retyped), slug
accept/refuse cases, copy completeness and non-generic wording, unknown-code
fallback, known-code precedence, the exact request (URL, method, credentials,
content type, body), queued/refused/unreachable results, per-code classification,
and the retry hints.

**Mutation evidence** (each applied, the named test failed, then reverted):

| Mutation | Result |
| --- | --- |
| delete the `slug_taken` entry from `reasonCopy.ts` | `test_the_page_maps_every_reason_the_server_can_return` + `test_the_page_scripts_pass` failed |
| add `BRAND_NEW = "brand_new_refusal"` to `SignupReason` | same two failed |
| delete `"www"` from `slugPolicy.generated.ts` | `test_the_generated_slug_policy_is_current`, `…really_contains_the_server_rule`, `…agree_on_every_slug`, `test_the_page_scripts_pass` failed |

**Browser check** (local, not committed — it mocks the two third parties:
Cloudflare's widget script and the Google-verified session, and drives the real
built bundle through playwright at 1440/390/320 px). Result: one script tag, one
explicit render with the configured sitekey and `action: 'signup'`, no horizontal
page scroll at 320 px, submit inside the viewport at every width, visible
associated label, both live regions present, live refusal of `Acme` and `www`
(and focus returns to the field, and nothing is posted), the button disables and
says "Sending…" in flight, a `turnstile_failed` refusal renders the page's copy
(not the raw server text) and resets the widget, `slug_taken` surfaces, a 201
renders the queued state naming `acme.hoja.work`, and the swept POST carried
`{"slug":"acme","turnstile_token":"…"}`. `mode=closed` and "no site key" both
render their notice with **zero** Turnstile script requests and no form.

## 8. Gates

| Gate | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | **464 passed, 6 skipped, 0 failed** (baseline on this branch: 445 / 6 / 0; the +19 are this module) |
| `cd frontend && ./node_modules/.bin/tsc --noEmit` | exit 0 |
| `npm run lint:styles` | exit 0 |
| `npm run lint:ui` (eslint, incl. the local mobile rules) | exit 0 |
| `rg -i "turnstile_secret\|GOCSPX\|client_secret" frontend/` | no matches |
| `node scripts/test-signup-page.cjs` | 15/15 |

## 9. Not done, and honest gaps

- **No real Google or Cloudflare round trip.** There are no credentials here and
  the brief forbids deploying or touching Cloudflare/Google. The widget was
  rendered against a stub of Cloudflare's script; the real widget on
  `localhost` would fail the domain check by design.
- **The site key is a deployment step**, not code: `TURNSTILE_SITE_KEY=0x4AAAAAAF0T6-JuMDYB9gPf`.
  Until it is set the page correctly says "sign-up unavailable".
- **Login-endpoint reason codes are not mapped to copy** (§4). A failed OIDC
  callback still renders JSON, as `SIGNUP-GATE-NOTES.md` §6 records.
- **No React render/unit test stack** exists in `frontend/`, and this change does
  not add one: the page's logic is extracted into pure modules and tested in
  node; the component's structure is asserted from source plus the browser check.
  A regression that only shows in JSX *markup* (e.g. a lost `aria-live`) would be
  caught by `test_the_page_is_accessible_by_construction`, not by a renderer.
- **No screen-reader or axe run.** Keyboard reachability is by construction
  (native button/input/anchor, `:focus-visible` outline from styles.css) and was
  clicked through in the browser check, but no assistive-technology session was
  performed.
- **`size: 'flexible'`** is passed to the widget so it can shrink under 300 px; if
  a future Cloudflare widget type ignores it, the mobile check that keeps the page
  from scrolling horizontally is the guard, not a fallback layout.
- **The marketing-site contract** (`Hoja-Cloud/docs/google-sso-design.md` §7) is
  still not implemented; this page is inside the app SPA at `/signup`.
- The page assumes `GET /api/signup` (identity state) and `GET /api/signup/config`
  answer 200; a 5xx surfaces as a visible error, but there is no retry/backoff.
