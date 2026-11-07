#!/usr/bin/env python3
"""Prove the mail setup from the outside, not from our own configuration.

Every check here reads what the world sees (public DNS over HTTPS) or what a
provider answers on the wire (an SMTP submission). Nothing reads the repository
for truth, because a record in Terraform is not a record on the internet.

    .venv/bin/python scripts/mail-doctor.py --dkim-selector google
    .venv/bin/python scripts/mail-doctor.py --smtp-check          # needs a token
    .venv/bin/python scripts/mail-doctor.py --send-to you@inbox.example

Exit code is 0 only when every required check passes.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
import urllib.parse
import urllib.request

DOH = "https://cloudflare-dns.com/dns-query"
CF_SMTP_HOST = "smtp.mx.cloudflare.net"
CF_SMTP_PORT = 465

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def dns(name: str, rr_type: str) -> list[str]:
    query = urllib.parse.urlencode({"name": name, "type": rr_type})
    request = urllib.request.Request(
        f"{DOH}?{query}", headers={"accept": "application/dns-json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    answers = payload.get("Answer") or []
    return [str(a.get("data", "")).strip('"') for a in answers]


def check_mx(domain: str) -> tuple[str, str, list[tuple[str, str, str]]]:
    rows = dns(domain, "MX")
    if not rows:
        return FAIL, f"no MX for {domain}", []
    return PASS, f"{len(rows)} MX records: {', '.join(sorted(rows))}", []


def check_spf(domain: str, expect_include: str | None) -> tuple[str, str, list]:
    records = [r for r in dns(domain, "TXT") if r.lower().startswith("v=spf1")]
    if not records:
        return FAIL, "no SPF record", []
    if len(records) > 1:
        return FAIL, f"{len(records)} SPF records — receivers treat this as a permanent error", []
    spf = records[0]
    tokens = spf.split()
    terminal = next((t for t in tokens if t.lower().endswith("all")), None)
    detail = f"{spf} (terminal {terminal or 'missing'})"
    if terminal is None:
        return WARN, detail + " — RFC 7208 wants an explicit all", []
    if terminal == "+all":
        return FAIL, detail + " — +all authorizes the entire internet", []
    if expect_include and expect_include not in spf:
        return FAIL, detail + f" — expected {expect_include}", []
    notes = []
    if terminal == "~all":
        notes.append("softfail; a spoofed message is marked, not rejected")
    return PASS, detail, notes


def check_dkim(domain: str, selectors: list[str]) -> tuple[str, str, list]:
    found: list[str] = []
    for selector in selectors:
        name = f"{selector}._domainkey.{domain}"
        for record in dns(name, "TXT"):
            if "p=" in record:
                found.append(f"{selector} ({len(record)} chars)")
    if not found:
        return FAIL, f"no DKIM key at {', '.join(selectors)}", []
    return PASS, "selectors with a key: " + ", ".join(found), []


def check_sending_domain(domain: str) -> tuple[str, str, list]:
    """Cloudflare Email Sending publishes its own bounce subdomain.

    Its absence is the concrete difference between "the API accepts the token"
    and "the provider will deliver": until the bounce MX, SPF and DKIM exist,
    submission is refused with 550 sending is not enabled for this domain.
    """
    bounce = f"cf-bounce.{domain}"
    mx = dns(bounce, "MX")
    spf = [r for r in dns(bounce, "TXT") if r.lower().startswith("v=spf1")]
    # The selector Cloudflare publishes is `cf-bounce`, i.e. the left-hand label
    # is the whole bounce subdomain, not "cf-bounce" plus the apex.
    dkim = [r for r in dns(f"cf-bounce._domainkey.{domain}", "TXT") if "p=" in r]
    missing = [name for name, present in
               (("bounce MX", mx), ("bounce SPF", spf), ("bounce DKIM", dkim)) if not present]
    if missing:
        return FAIL, f"{bounce}: missing {', '.join(missing)}", [
            "Onboard the domain: Compute > Email Service > Email Sending > Onboard Domain."]
    return PASS, f"{bounce}: {len(mx)} MX, SPF, DKIM present", []


def check_dmarc(domain: str) -> tuple[str, str, list]:
    records = [r for r in dns(f"_dmarc.{domain}", "TXT") if r.lower().startswith("v=dmarc1")]
    if not records:
        return FAIL, f"no DMARC record at _dmarc.{domain}", [
            "Without DMARC, a receiver has no instruction for SPF/DKIM alignment "
            "failures, and nobody receives aggregate reports."]
    record = records[0]
    # Tags arrive space-separated: "v=DMARC1; p=none; rua=mailto:...". Strip both
    # sides or every key except the first keeps its leading space.
    tags = {
        key.strip().lower(): value.strip()
        for key, _, value in (part.partition("=") for part in record.split(";"))
        if key.strip()
    }
    policy = (tags.get("p") or "").strip().lower()
    detail = f"{record}"
    notes = [f"policy p={policy or 'missing'}"]
    if policy == "none":
        notes.append("p=none monitors only; tighten to quarantine once reports are clean")
    if not tags.get("rua"):
        notes.append("no rua= address, so aggregate reports are not collected")
    if policy not in ("none", "quarantine", "reject"):
        return FAIL, detail, notes
    return (PASS if policy != "none" else WARN), detail, notes


def check_cloudflare_smtp(token: str, sender: str) -> tuple[str, str, list]:
    """Authenticate to the submission endpoint and stop. No message is sent."""
    try:
        with smtplib.SMTP_SSL(CF_SMTP_HOST, CF_SMTP_PORT, timeout=15,
                              context=ssl.create_default_context()) as client:
            code, _greeting = client.ehlo("hoja-mail-doctor")
            capabilities = sorted(
                str(feature).split(" ")[0]
                for feature in (client.esmtp_features or {})
            )
            try:
                client.login("api_token", token)
            except smtplib.SMTPAuthenticationError as exc:
                # exc.smtp_code/error text only; never the credential.
                return FAIL, f"AUTH rejected ({exc.smtp_code})", []
            auth_line = f"EHLO {code}, AUTH accepted as {sender!r}"
            return PASS, auth_line + f"; capabilities: {', '.join(capabilities)}", []
    except (OSError, smtplib.SMTPException) as exc:
        return FAIL, f"cannot reach {CF_SMTP_HOST}:{CF_SMTP_PORT} ({type(exc).__name__})", []


def check_live_send(token: str, sender: str, recipient: str, domain: str) -> tuple[str, str, list]:
    import urllib.error

    body = {
        "to": recipient,
        "from": sender,
        "subject": "Hoja mail check",
        "text": "This is a delivery check. The headers on this message are the evidence.",
        "html": "<p>This is a delivery check. The headers on this message are the evidence.</p>",
    }
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not account:
        return FAIL, "CLOUDFLARE_ACCOUNT_ID is not set", []
    request = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/email/sending/send",
        data=json.dumps(body).encode("utf-8"),
        headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            parsed = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")[:400]
        return FAIL, f"HTTP {exc.code}: {raw}", []
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return FAIL, f"transport failed ({type(exc).__name__})", []
    result = parsed.get("result") or {}
    return PASS, json.dumps(result), [
        "Deliverability is decided by the receiving MTA: read Authentication-Results "
        f"on the message at {recipient} for spf/dkim/dmarc=pass."]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default=os.environ.get("HOJA_MAIL_DOMAIN", "hoja.work"))
    parser.add_argument("--dkim-selector", action="append", default=None,
                        help="repeatable; defaults to google (Workspace) and cf")
    parser.add_argument("--spf-include", default=None,
                        help="expected include:, e.g. _spf.google.com")
    parser.add_argument("--smtp-check", action="store_true",
                        help="authenticate to Cloudflare SMTP without sending")
    parser.add_argument("--send-to", default=None,
                        help="send one real message and print the provider response")
    parser.add_argument("--from", dest="sender",
                        default=os.environ.get("HOJA_MAIL_FROM", "notifications@hoja.work"))
    args = parser.parse_args(argv)

    selectors = args.dkim_selector or ["google", "cf"]
    rows: list[tuple[str, str, str, list]] = []
    rows.append(("mx", *check_mx(args.domain)))
    rows.append(("spf", *check_spf(args.domain, args.spf_include)))
    rows.append(("dkim", *check_dkim(args.domain, selectors)))
    rows.append(("dmarc", *check_dmarc(args.domain)))
    rows.append(("sending", *check_sending_domain(args.domain)))

    token = (os.environ.get("CLOUDFLARE_EMAIL_API_TOKEN")
             or os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if args.smtp_check:
        if not token:
            rows.append(("smtp", FAIL, "CLOUDFLARE_EMAIL_API_TOKEN is not set", []))
        else:
            rows.append(("smtp", *check_cloudflare_smtp(token, args.sender)))
    if args.send_to:
        if not token:
            rows.append(("send", FAIL, "CLOUDFLARE_EMAIL_API_TOKEN is not set", []))
        else:
            rows.append(("send", *check_live_send(token, args.sender, args.send_to, args.domain)))

    width = max(len(name) for name, *_ in rows)
    for name, status, detail, notes in rows:
        print(f"{name.upper():<{width}}  {status:<4}  {detail}")
        for note in notes:
            print(f"{'':<{width}}  {'':<4}  - {note}")

    failed = [name for name, status, *_ in rows if status == FAIL]
    if failed:
        print(f"\n{len(failed)} check(s) failed: {', '.join(failed)}")
        return 1
    print("\nAll checks passed. A pass here is authentication, not reputation: "
          "confirm with receiver headers and a spam score before bulk sending.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
