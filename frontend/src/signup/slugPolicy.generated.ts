// GENERATED FILE — do not edit by hand.
//
// Source of truth: hoja_server/signup_policy.py (SLUG_RE, SLUG_MAX_LENGTH,
// RESERVED_SLUGS), which mirrors the provisioner's rule.
// Regenerate with: .venv/bin/python scripts/generate-signup-slug-policy.py
// Parity is enforced by tests/test_signup_page.py, so a server-side change that
// is not regenerated here fails the suite.

//: The provisioner's DNS-label expression, verbatim.
export const SLUG_PATTERN = "^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$";

//: A DNS label is at most this many characters.
export const SLUG_MAX_LENGTH = 63;

//: Names that already mean something on the base domain.
export const RESERVED_SLUGS: readonly string[] = [
  "admin",
  "api",
  "app",
  "assets",
  "auth",
  "autoconfig",
  "autodiscover",
  "billing",
  "blog",
  "cdn",
  "dashboard",
  "docs",
  "help",
  "imap",
  "login",
  "mail",
  "ns1",
  "ns2",
  "pop",
  "smtp",
  "sso",
  "static",
  "status",
  "support",
  "traefik",
  "webmail",
  "www",
];
