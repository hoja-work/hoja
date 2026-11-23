"""The usage ledger: what a run cost, in credits and in money (HOJA-602-A-12, WP14).

The pricing decision (``company/decisions/2026-11-12-native-agent-execution-and-metering.md``)
sets the units, and this module is the only place they are computed:

* **1 credit = 1,000 processed tokens**, where processed is fresh input + cache read +
  output. A credit is a unit of *usage*, not of money.
* **Overage $0.08 per 1,000 credits** is the plan's price for credits beyond what a
  seat includes. That is a billing calculation over ledger rows, not a field on one:
  a row records the money Hoja can attribute to a run, and the seat allowance is
  applied when somebody invoices.
* **Managed compute $0.005 per run-minute with a 15-minute floor.** The floor belongs
  to the *run*, so it is applied to the run's cumulative minutes and never to a
  per-report delta — otherwise ten reports of a one-minute run would bill 150 minutes.
* **A bring-your-own key costs nothing.** The customer paid their model provider
  directly; Hoja supplied no key and rented no capacity, so both money columns on such
  a run are zero and only its usage is recorded. The money columns are therefore what
  Hoja charges for a run — which equals what the run cost Hoja when Hoja supplied the
  key — so a month's rows sum into an invoice and into a gross margin without a second
  interpretation of "cost".

Three rules are load-bearing here.

Money is never a float. ``Decimal`` in, a canonical decimal string out, at the price
card's own rates: a float would put a binary rounding error in a column somebody is
invoiced from.

Cost is never taken from the harness. The study behind WP11 found ``usage.cost`` was 0
in every harness log, so a ledger that trusted it would record a free month for every
customer. Every number here is derived from token counts against a **versioned price
card**, and each row records the card version it was priced with, so a later card can
price future rows and cannot rewrite a past one.

The ledger is append-only. There is no update and no delete function in this module,
no caller anywhere writes one, and a correction is a NEW row whose ``source`` says
``correction`` — so the mistake and its fix are both still visible. ``_write`` is the
only INSERT in the file, and every read sums rows rather than trusting a stored total.

Where the numbers come from: ``runner_api`` calls :func:`record_report` from the two
places a runner reports usage — the ``/runner/events`` stream as it goes and the
``/runner/finish`` claim at the end. Both report *cumulative* counters for the run, so
the writer stores the difference from what the ledger already holds for that
``run_id``. Summing a run's rows therefore returns the runner's own last cumulative
report, which is what makes the reconciliation test a real check rather than a
tautology: a writer that counted the finish on top of the events would fail it.

One deliberate reading of the spec: WP14's prompt says a bring-your-own-key run
records *zero credits*, and the work package's issue says a bring-your-own key is
*free*. Those are only compatible if "free" means money, so a BYO run records its
credits (usage is usage, and the budget is a usage cap) and costs $0 in tokens and
compute. Charging nothing while still counting the run against the agent's budget is
the conservative pair: no customer is billed for a key they paid for, and no agent
buys unlimited capacity by bringing one.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from ariadne import ObjectType, QueryType, gql
from graphql import GraphQLError
from sqlalchemy import Integer, and_, desc, func, literal_column, select

from hoja_server.db import (
    agent_member,
    agent_policy,
    new_id,
    now_iso,
    usage_event,
    user,
)

# How many tokens one credit buys. The decision's only unit: 1 credit = 1k tokens.
TOKENS_PER_CREDIT = 1000

# The wire's two report paths, plus the one way a row is ever "changed".
SOURCE_FINISH = "finish"
SOURCE_EVENTS = "events"
SOURCE_CORRECTION = "correction"
SOURCES = (SOURCE_FINISH, SOURCE_EVENTS, SOURCE_CORRECTION)

# Whose key paid for the model tokens.
AUTH_BYO = "byo-key"
AUTH_MANAGED = "managed-key"
AUTH_MODES = (AUTH_BYO, AUTH_MANAGED)

# Whose machine paid for the minutes.
COMPUTE_CUSTOMER = "customer"
COMPUTE_MANAGED = "managed"
COMPUTE_MODES = (COMPUTE_CUSTOMER, COMPUTE_MANAGED)

# The groupings the screen offers, in one place so the screen and the resolver cannot
# drift into offering something the other cannot answer.
GROUPINGS = ("agent", "model", "month")

# Bumped whenever a rate changes. Rows keep the version they were priced with.
PRICE_CARD_VERSION = "2026-11-12"

# USD per 1,000,000 tokens, keyed by the price-card name a harness profile declares
# (``harness_profiles.Cost.price_card``). A model this card does not name is priced at
# ``DEFAULT_RATES`` and recorded under its own model string, so the row stays
# attributable; a later card that names it appends a correction row instead of editing
# history.
PRICE_CARD_RATES: dict[str, dict[str, str]] = {
    "anthropic/claude-sonnet-4-5": {"input": "3.00", "cache_read": "0.30", "output": "15.00"},
    "openai/gpt-5-codex": {"input": "1.25", "cache_read": "0.125", "output": "10.00"},
    "google/gemini-2.5-pro": {"input": "1.25", "cache_read": "0.31", "output": "10.00"},
    "opencode/default": {"input": "0.60", "cache_read": "0.06", "output": "2.20"},
    "pi/default": {"input": "0.60", "cache_read": "0.06", "output": "2.20"},
    "generic/default": {"input": "1.00", "cache_read": "0.10", "output": "4.00"},
}
DEFAULT_RATES = {"input": "1.00", "cache_read": "0.10", "output": "4.00"}

# The card's non-token terms, as strings so nothing here is ever a float.
COMPUTE_USD_PER_RUN_MINUTE = "0.005"
COMPUTE_FLOOR_MINUTES = 15
OVERAGE_USD_PER_K_CREDITS = "0.08"

# Six decimal places is a micro-dollar: finer than any invoice line, and enough that a
# single cheap run is not rounded to zero.
MONEY_PLACES = Decimal("0.000001")

MAX_PAGE = 500


def price_card() -> dict[str, Any]:
    """The card as data, so a caller can prove which rates a row was priced with."""
    return {
        "version": PRICE_CARD_VERSION,
        "tokensPerCredit": TOKENS_PER_CREDIT,
        "overageUsdPerKCredits": OVERAGE_USD_PER_K_CREDITS,
        "computeUsdPerRunMinute": COMPUTE_USD_PER_RUN_MINUTE,
        "computeFloorMinutes": COMPUTE_FLOOR_MINUTES,
        "ratesPerMillionTokens": {k: dict(v) for k, v in PRICE_CARD_RATES.items()},
        "defaultRates": dict(DEFAULT_RATES),
    }


def rates_for(model: str | None) -> dict[str, Decimal]:
    """The rates for one model, falling back to the card's default row."""
    raw = PRICE_CARD_RATES.get((model or "").strip()) or DEFAULT_RATES
    return {name: Decimal(value) for name, value in raw.items()}


def money(value: Decimal) -> str:
    """A canonical decimal string: no exponent, no float, no locale."""
    return format(value.quantize(MONEY_PLACES), "f")


def decimal_of(value: Any) -> Decimal:
    """Parse a stored decimal string. Never ``float()``."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def processed_tokens(input_tokens: Any, output_tokens: Any, cache_read_tokens: Any) -> int:
    """Fresh input + cache read + output: the decision's definition of processed."""
    return (max(0, int(input_tokens or 0)) + max(0, int(output_tokens or 0))
            + max(0, int(cache_read_tokens or 0)))


def credits_for(tokens: int) -> int:
    """Credits for a token count, rounded up: a part credit is still a credit spent."""
    return math.ceil(max(0, int(tokens)) / TOKENS_PER_CREDIT)


def token_cost_usd(model: str | None, input_tokens: Any, output_tokens: Any,
                   cache_read_tokens: Any) -> Decimal:
    """Money for one delta of tokens at the card's per-million rates."""
    rates = rates_for(model)
    million = Decimal(1_000_000)
    return (
        Decimal(max(0, int(input_tokens or 0))) * rates["input"] / million
        + Decimal(max(0, int(cache_read_tokens or 0))) * rates["cache_read"] / million
        + Decimal(max(0, int(output_tokens or 0))) * rates["output"] / million
    )


def charged_token_cost(auth_mode: str, model: str | None, input_tokens: Any,
                       output_tokens: Any, cache_read_tokens: Any) -> Decimal:
    """What Hoja charges for a delta of tokens. A bring-your-own key is charged nothing.

    The customer's own provider bill is theirs whether they read it here or not, so a
    BYO run records its usage and no money: charging it would be billing somebody for a
    key they bought.
    """
    if auth_mode != AUTH_MANAGED:
        return Decimal("0")
    return token_cost_usd(model, input_tokens, output_tokens, cache_read_tokens)


def run_minutes_for(seconds: Any, compute: str) -> int:
    """Minutes for a run's cumulative compute seconds.

    Managed compute carries the decision's 15-minute floor; customer compute does not,
    because the floor is the price of a sandbox Hoja had ready and a laptop nobody
    rented does not have one.
    """
    minutes = math.ceil(max(0.0, float(seconds or 0)) / 60.0)
    if compute == COMPUTE_MANAGED:
        return max(COMPUTE_FLOOR_MINUTES, minutes)
    return minutes


def compute_cost_usd(run_minutes: Any, compute: str) -> Decimal:
    """Money for compute: only a managed run is charged, and it is charged per minute."""
    if compute != COMPUTE_MANAGED:
        return Decimal("0")
    return Decimal(max(0, int(run_minutes or 0))) * Decimal(COMPUTE_USD_PER_RUN_MINUTE)


# ---------------------------------------------------------------------------
# Writing (append-only)
# ---------------------------------------------------------------------------


class ReportError(ValueError):
    """A usage report the ledger refuses because it cannot be read as a number."""


def _write(conn, values: Mapping[str, Any]) -> dict[str, Any]:
    """The only INSERT in this module. Nothing here updates or deletes a row."""
    row = {"id": new_id(), **dict(values)}
    conn.execute(usage_event.insert().values(**row))
    return row


def _count(value: Any, field: str) -> int:
    """A non-negative integer, or a refusal. A negative count is not a correction."""
    if value in (None, ""):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ReportError(f"{field} must be an integer") from None
    if number < 0:
        raise ReportError(f"{field} must not be negative")
    return number


def _seconds(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ReportError("run_seconds must be a number") from None
    if number < 0 or number != number:  # negative, or NaN
        raise ReportError("run_seconds must not be negative")
    return number


def usage_report(body: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Read a usage document off the wire, or None when the runner sent none.

    Both key spellings are accepted because the wire is camelCase (the daemon and the
    harness profiles) while Hoja's own examples are snake_case, and a metering field
    silently dropped over a case convention is worse than one read twice.
    """
    if not isinstance(body, Mapping):
        return None
    present = [key for key in ("usage", "tokens", "tokenUsage")
               if isinstance(body.get(key), Mapping)]
    if not present:
        return None
    document = body[present[0]]
    model = document.get("model") or body.get("model") or "unknown"
    auth_mode = str(document.get("auth_mode") or document.get("authMode")
                    or AUTH_BYO).strip().lower()
    # An unrecognised mode is recorded as bring-your-own: that is the mode that charges
    # the customer nothing, so a daemon that invents a spelling cannot bill somebody.
    if auth_mode not in AUTH_MODES:
        auth_mode = AUTH_BYO
    compute = COMPUTE_MANAGED if auth_mode == AUTH_MANAGED else COMPUTE_CUSTOMER
    return {
        "model": str(model)[:120] or "unknown",
        "input_tokens": _count(document.get("input_tokens", document.get("inputTokens")),
                               "input_tokens"),
        "output_tokens": _count(document.get("output_tokens", document.get("outputTokens")),
                                "output_tokens"),
        "cache_read_tokens": _count(
            document.get("cache_read_tokens", document.get("cacheReadTokens")),
            "cache_read_tokens"),
        "run_seconds": _seconds(document.get("run_seconds", document.get("runSeconds"))),
        "auth_mode": auth_mode,
        "compute": compute,
    }


def recorded_totals(conn, organization_id: str, run_id: str) -> dict[str, Any]:
    """What the ledger already holds for one run: the baseline a delta is taken from."""
    empty = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "credits": 0,
             "run_minutes": 0, "cost_usd": Decimal("0"), "compute_cost_usd": Decimal("0"),
             "rows": 0}
    if not run_id:
        return empty
    clauses = and_(usage_event.c.organization_id == organization_id,
                   usage_event.c.run_id == run_id)
    totals = conn.execute(select(
        func.coalesce(func.sum(usage_event.c.input_tokens), 0),
        func.coalesce(func.sum(usage_event.c.output_tokens), 0),
        func.coalesce(func.sum(usage_event.c.cache_read_tokens), 0),
        func.coalesce(func.sum(usage_event.c.credits), 0),
        func.coalesce(func.sum(usage_event.c.run_minutes), 0),
        func.count(usage_event.c.id),
    ).where(clauses)).one()
    # Money is summed in Python: a SQL SUM over a text column is a string concatenation
    # on one engine and a cast on the other, and neither is money.
    money = conn.execute(select(usage_event.c.cost_usd, usage_event.c.compute_cost_usd)
                         .where(clauses)).all()
    cost = sum((decimal_of(row[0]) for row in money), Decimal("0"))
    compute = sum((decimal_of(row[1]) for row in money), Decimal("0"))
    return {
        "input_tokens": int(totals[0] or 0),
        "output_tokens": int(totals[1] or 0),
        "cache_read_tokens": int(totals[2] or 0),
        "credits": int(totals[3] or 0),
        "run_minutes": int(totals[4] or 0),
        "rows": int(totals[5] or 0),
        "cost_usd": cost,
        "compute_cost_usd": compute,
    }


def record_report(conn, *, organization_id: str, run_id: str, agent_user_id: str,
                  usage: Mapping[str, Any], session_id: str | None = None,
                  lease_id: str | None = None, source: str = SOURCE_FINISH,
                  at: str | None = None) -> dict[str, Any] | None:
    """Store one report as the delta it adds to the run. None when it adds nothing.

    The counters a runner sends are cumulative for the run, so this is where the
    double counting a naive ledger would do is prevented: events arrive while the run
    goes, the finish arrives at the end, and the finish's counters already include the
    events'. The row written is the difference, the credits are the difference of
    ``ceil(tokens / 1000)`` (so per-report rounding cannot drift), the minutes are the
    difference of the run's floored cumulative minutes, and a report behind what is
    stored is a re-delivery rather than a refund.
    """
    if source not in SOURCES:
        raise ReportError(f"unknown usage source {source!r}")
    if not agent_user_id:
        # A run nobody owns cannot be attributed, and a row with no agent is a number
        # the usage screen could never explain. Refuse rather than invent.
        raise ReportError("a usage report needs the agent it belongs to")
    stamp = at or now_iso()
    reported = {
        "input_tokens": max(0, int(usage.get("input_tokens") or 0)),
        "output_tokens": max(0, int(usage.get("output_tokens") or 0)),
        "cache_read_tokens": max(0, int(usage.get("cache_read_tokens") or 0)),
    }
    model = str(usage.get("model") or "unknown")
    auth_mode = str(usage.get("auth_mode") or AUTH_BYO)
    compute = str(usage.get("compute") or COMPUTE_CUSTOMER)
    if auth_mode not in AUTH_MODES or compute not in COMPUTE_MODES:
        raise ReportError("unknown auth mode or compute mode")
    previous = recorded_totals(conn, organization_id, run_id)
    delta = {name: max(0, reported[name] - int(previous[name])) for name in reported}
    delta_tokens = processed_tokens(delta["input_tokens"], delta["output_tokens"],
                                   delta["cache_read_tokens"])
    reported_tokens = processed_tokens(reported["input_tokens"], reported["output_tokens"],
                                       reported["cache_read_tokens"])
    delta_credits = max(0, credits_for(reported_tokens) - int(previous["credits"]))
    delta_minutes = max(0, run_minutes_for(usage.get("run_seconds"), compute)
                        - int(previous["run_minutes"]))
    if not delta_tokens and not delta_minutes:
        # A re-delivered batch, or a poll that learned nothing: nothing to append.
        return None
    cost = charged_token_cost(auth_mode, model, delta["input_tokens"], delta["output_tokens"],
                              delta["cache_read_tokens"])
    billed_compute = compute_cost_usd(delta_minutes, compute)
    return _write(conn, {
        "organization_id": organization_id,
        "agent_user_id": agent_user_id,
        "session_id": session_id,
        "lease_id": lease_id,
        "run_id": run_id,
        "model": model[:120],
        "input_tokens": delta["input_tokens"],
        "output_tokens": delta["output_tokens"],
        "cache_read_tokens": delta["cache_read_tokens"],
        "credits": delta_credits,
        "run_minutes": delta_minutes,
        "cost_usd": money(cost),
        "compute_cost_usd": money(billed_compute),
        "source": source,
        "auth_mode": auth_mode,
        "compute": compute,
        "price_card": PRICE_CARD_VERSION,
        "cumulative_tokens": reported_tokens,
        "recorded_at": stamp,
    })


def record_correction(conn, *, organization_id: str, agent_user_id: str, run_id: str,
                      model: str = "unknown", credits: int = 0, input_tokens: int = 0,
                      output_tokens: int = 0, cache_read_tokens: int = 0,
                      auth_mode: str = AUTH_BYO) -> dict[str, Any]:
    """Append a correction row. A mistake is fixed forward, never edited away.

    The correction is a signed *addition* to the run: a reader that sums the run sees
    the corrected total, and a reader that filters on ``source`` sees both the original
    number and the fix.
    """
    if auth_mode not in AUTH_MODES:
        raise ReportError(f"unknown auth mode {auth_mode!r}")
    tokens = processed_tokens(input_tokens, output_tokens, cache_read_tokens)
    previous = recorded_totals(conn, organization_id, run_id)
    cost = charged_token_cost(auth_mode, model, input_tokens, output_tokens,
                              cache_read_tokens)
    return _write(conn, {
        "organization_id": organization_id,
        "agent_user_id": agent_user_id,
        "session_id": None,
        "lease_id": None,
        "run_id": run_id,
        "model": model[:120],
        "input_tokens": max(0, int(input_tokens)),
        "output_tokens": max(0, int(output_tokens)),
        "cache_read_tokens": max(0, int(cache_read_tokens)),
        "credits": max(0, int(credits)),
        "run_minutes": 0,
        "cost_usd": money(cost),
        "compute_cost_usd": money(Decimal("0")),
        "source": SOURCE_CORRECTION,
        "auth_mode": auth_mode,
        "compute": COMPUTE_MANAGED if auth_mode == AUTH_MANAGED else COMPUTE_CUSTOMER,
        "price_card": PRICE_CARD_VERSION,
        "cumulative_tokens": int(previous["input_tokens"] + previous["output_tokens"]
                                 + previous["cache_read_tokens"]) + tokens,
        "recorded_at": now_iso(),
    })


# ---------------------------------------------------------------------------
# Reading: totals, budget, groupings
# ---------------------------------------------------------------------------


def parse_stamp(stamp: str | None) -> datetime:
    if not stamp:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def month_window(month: str | None = None, *,
                 at: datetime | None = None) -> tuple[str, str, str]:
    """``(label, start_iso, end_iso)`` for a calendar month, in UTC.

    UTC always: a budget that reset at each member's local midnight would reset several
    times inside one server day, and two rows from the same hour could land in
    different months.
    """
    if month:
        try:
            parsed = datetime.strptime(str(month), "%Y-%m").replace(tzinfo=timezone.utc)
        except ValueError:
            raise ReportError(f"month must be YYYY-MM, not {month!r}") from None
    else:
        base = at or datetime.now(timezone.utc)
        parsed = base.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (parsed.replace(year=parsed.year + 1, month=1) if parsed.month == 12
           else parsed.replace(month=parsed.month + 1))
    return parsed.strftime("%Y-%m"), parsed.isoformat(), end.isoformat()


def _month_clauses(organization_id: str, start: str, end: str) -> list:
    return [usage_event.c.organization_id == organization_id,
            usage_event.c.recorded_at >= start,
            usage_event.c.recorded_at < end]


def month_totals(conn, organization_id: str, *, agent_user_id: str | None = None,
                 month: str | None = None) -> dict[str, Any]:
    """Summed rows for one month, optionally for one agent."""
    label, start, end = month_window(month)
    clauses = _month_clauses(organization_id, start, end)
    if agent_user_id:
        clauses.append(usage_event.c.agent_user_id == agent_user_id)
    totals = conn.execute(select(
        func.coalesce(func.sum(usage_event.c.credits), 0),
        func.coalesce(func.sum(usage_event.c.run_minutes), 0),
        func.coalesce(func.sum(usage_event.c.input_tokens), 0),
        func.coalesce(func.sum(usage_event.c.output_tokens), 0),
        func.coalesce(func.sum(usage_event.c.cache_read_tokens), 0),
        func.count(usage_event.c.id),
    ).where(and_(*clauses))).one()
    money_rows = conn.execute(select(usage_event.c.cost_usd, usage_event.c.compute_cost_usd)
                              .where(and_(*clauses))).all()
    cost = sum((decimal_of(row[0]) for row in money_rows), Decimal("0"))
    compute = sum((decimal_of(row[1]) for row in money_rows), Decimal("0"))
    return {
        "month": label,
        "start": start,
        "end": end,
        "credits": int(totals[0] or 0),
        "run_minutes": int(totals[1] or 0),
        "input_tokens": int(totals[2] or 0),
        "output_tokens": int(totals[3] or 0),
        "cache_read_tokens": int(totals[4] or 0),
        "events": int(totals[5] or 0),
        "cost_usd": money(cost),
        "compute_cost_usd": money(compute),
        "total_usd": money(cost + compute),
    }


def budget_for(conn, organization_id: str, agent_user_id: str) -> dict[str, Any]:
    """The agent's monthly credit budget and what happens when it runs out.

    WP12's policy is the authority and ``agent_member.monthly_budget_credits`` is the
    fallback it has always documented, so the two cannot disagree: a policy value wins,
    a policy with no value defers to the member, and neither set means "no ceiling this
    workspace stated" — reported as available, because inventing a ceiling would stop
    work nobody limited.
    """
    member = conn.execute(select(agent_member.c.id, agent_member.c.monthly_budget_credits)
                          .where(and_(
                              agent_member.c.organization_id == organization_id,
                              agent_member.c.user_id == agent_user_id,
                          ))).mappings().first()
    policy = None
    if member:
        policy = conn.execute(select(agent_policy.c.monthly_credit_budget,
                                     agent_policy.c.budget_action)
                              .where(and_(
                                  agent_policy.c.organization_id == organization_id,
                                  agent_policy.c.agent_member_id == member["id"],
                              ))).mappings().first()
    limit = None
    if policy is not None and policy["monthly_credit_budget"] is not None:
        limit = int(policy["monthly_credit_budget"])
    elif member is not None and member["monthly_budget_credits"] is not None:
        limit = int(member["monthly_budget_credits"])
    action = str((policy["budget_action"] if policy is not None else None) or "queue")
    return {"limit": limit, "action": action if action in ("queue", "stop") else "queue"}


def budget_status(conn, organization_id: str, agent_user_id: str, *,
                  month: str | None = None) -> dict[str, Any]:
    """How much of this month's credit budget is left, and whether it is spent."""
    budget = budget_for(conn, organization_id, agent_user_id)
    totals = month_totals(conn, organization_id, agent_user_id=agent_user_id, month=month)
    limit = budget["limit"]
    used = totals["credits"]
    return {
        "month": totals["month"],
        "limit": limit,
        "used": used,
        # None means the workspace set no ceiling: not "unlimited credits", but "there
        # is nothing to compare against", which the screen prints instead of a number.
        "remaining": None if limit is None else max(0, limit - used),
        "exhausted": limit is not None and used >= limit,
        "action": budget["action"],
    }


def budget_exhausted(conn, organization_id: str, agent_user_id: str) -> bool:
    """The one question the schedule check and the lease gate both ask."""
    return budget_status(conn, organization_id, agent_user_id)["exhausted"]


def budget_action(conn, organization_id: str, agent_user_id: str) -> str:
    """``queue`` or ``stop``: what the workspace asked for when the budget runs out."""
    return budget_for(conn, organization_id, agent_user_id)["action"]


def session_totals(conn, organization_id: str, session_id: str) -> dict[str, Any]:
    """One session's ledger totals: the reconciliation test's other side."""
    rows = conn.execute(select(
        func.coalesce(func.sum(usage_event.c.input_tokens), 0),
        func.coalesce(func.sum(usage_event.c.output_tokens), 0),
        func.coalesce(func.sum(usage_event.c.cache_read_tokens), 0),
        func.coalesce(func.sum(usage_event.c.credits), 0),
        func.coalesce(func.sum(usage_event.c.run_minutes), 0),
    ).where(and_(
        usage_event.c.organization_id == organization_id,
        usage_event.c.session_id == session_id,
    ))).one()
    return {
        "input_tokens": int(rows[0] or 0),
        "output_tokens": int(rows[1] or 0),
        "cache_read_tokens": int(rows[2] or 0),
        "credits": int(rows[3] or 0),
        "run_minutes": int(rows[4] or 0),
        "tokens": processed_tokens(rows[0], rows[1], rows[2]),
    }


def agent_labels(conn, organization_id: str, agent_user_ids: list[str]) -> dict[str, str]:
    """Display names for agents, so a row says "Scout" rather than a UUID."""
    wanted = [value for value in dict.fromkeys(agent_user_ids) if value]
    if not wanted:
        return {}
    rows = conn.execute(select(user.c.id, user.c.name, user.c.display_name, user.c.email)
                        .where(and_(
                            user.c.organization_id == organization_id,
                            user.c.id.in_(wanted),
                        ))).all()
    return {str(row[0]): str(row[1] or row[2] or row[3] or row[0]) for row in rows}


def summary(conn, organization_id: str, *, group_by: str = "agent",
            month: str | None = None) -> dict[str, Any]:
    """The usage screen's data: the month's totals, plus one row per grouping key."""
    if group_by not in GROUPINGS:
        raise ReportError(f"groupBy must be one of {', '.join(GROUPINGS)}")
    label, start, end = month_window(month)
    totals = month_totals(conn, organization_id, month=label)
    aggregates = (
        func.sum(usage_event.c.credits),
        func.sum(usage_event.c.run_minutes),
        func.sum(usage_event.c.input_tokens),
        func.sum(usage_event.c.output_tokens),
        func.sum(usage_event.c.cache_read_tokens),
        func.count(func.distinct(usage_event.c.run_id)),
    )
    rollup: dict[str, dict[str, Any]] = {}

    def entry_for(key: str, text: str, *, agent_id: str | None, model: str | None,
                  row_month: str) -> dict[str, Any]:
        return rollup.setdefault(key, {
            "key": key, "label": text, "agentUserId": agent_id, "model": model,
            "month": row_month, "credits": 0, "runMinutes": 0, "inputTokens": 0,
            "outputTokens": 0, "cacheReadTokens": 0, "runs": 0,
            "costUsd": Decimal("0"), "computeCostUsd": Decimal("0"),
            "limitCredits": None, "budgetAction": None,
        })

    if group_by == "month":
        # Every month this workspace has spent in, not just the selected one: a screen
        # grouped by month that shows one row would be a screen with no grouping.
        month_bucket = func.substr(usage_event.c.recorded_at, literal_column("1", Integer),
                                   literal_column("7", Integer))
        # The same expression object in SELECT and GROUP BY, and its offsets inlined
        # rather than bound: PostgreSQL matches a grouped expression textually, so two
        # separately-bound ``substr(..., $1, $2)`` calls are two different expressions
        # and the query is a GroupingError there while SQLite accepts it.
        rows = conn.execute(select(month_bucket, *aggregates)
                            .where(usage_event.c.organization_id == organization_id)
                            .group_by(month_bucket)).all()
        for stamp, credits, minutes, inp, out, cache, runs in rows:
            bucket = entry_for(str(stamp), str(stamp), agent_id=None, model=None,
                               row_month=str(stamp))
            bucket["credits"] += int(credits or 0)
            bucket["runMinutes"] += int(minutes or 0)
            bucket["inputTokens"] += int(inp or 0)
            bucket["outputTokens"] += int(out or 0)
            bucket["cacheReadTokens"] += int(cache or 0)
            bucket["runs"] += int(runs or 0)
        cost_rows = conn.execute(select(
            month_bucket, usage_event.c.cost_usd, usage_event.c.compute_cost_usd,
        ).where(usage_event.c.organization_id == organization_id)).all()
        for stamp, cost, compute in cost_rows:
            bucket = rollup.get(str(stamp))
            if bucket is not None:
                bucket["costUsd"] += decimal_of(cost)
                bucket["computeCostUsd"] += decimal_of(compute)
    else:
        column = usage_event.c.agent_user_id if group_by == "agent" else usage_event.c.model
        rows = conn.execute(select(column, *aggregates)
                            .where(and_(*_month_clauses(organization_id, start, end)))
                            .group_by(column)).all()
        for key, credits, minutes, inp, out, cache, runs in rows:
            text = str(key)
            bucket = entry_for(
                text, text,
                agent_id=text if group_by == "agent" else None,
                model=text if group_by == "model" else None,
                row_month=label)
            bucket["credits"] += int(credits or 0)
            bucket["runMinutes"] += int(minutes or 0)
            bucket["inputTokens"] += int(inp or 0)
            bucket["outputTokens"] += int(out or 0)
            bucket["cacheReadTokens"] += int(cache or 0)
            bucket["runs"] += int(runs or 0)
        cost_rows = conn.execute(select(
            column, usage_event.c.cost_usd, usage_event.c.compute_cost_usd,
        ).where(and_(*_month_clauses(organization_id, start, end)))).all()
        for key, cost, compute in cost_rows:
            bucket = rollup.get(str(key))
            if bucket is not None:
                bucket["costUsd"] += decimal_of(cost)
                bucket["computeCostUsd"] += decimal_of(compute)
        names = agent_labels(conn, organization_id,
                             [b["agentUserId"] for b in rollup.values() if b["agentUserId"]])
        for bucket in rollup.values():
            if bucket["agentUserId"]:
                bucket["label"] = names.get(bucket["agentUserId"], bucket["agentUserId"])
                # The screen has to show the ceiling next to the spend, or "credits"
                # is a number with nothing to compare it against. It is read per row
                # because a budget is per agent (WP12's policy), never per workspace.
                budget = budget_for(conn, organization_id, bucket["agentUserId"])
                bucket["limitCredits"] = budget["limit"]
                bucket["budgetAction"] = budget["action"]

    ordered = sorted(rollup.values(), key=lambda b: (-b["credits"], b["label"]))
    for bucket in ordered:
        bucket["costUsd"] = money(bucket["costUsd"])
        bucket["computeCostUsd"] = money(bucket["computeCostUsd"])
    return {
        "month": label,
        "groupBy": group_by,
        "credits": totals["credits"],
        "runMinutes": totals["run_minutes"],
        "costUsd": totals["cost_usd"],
        "computeCostUsd": totals["compute_cost_usd"],
        "totalUsd": totals["total_usd"],
        "inputTokens": totals["input_tokens"],
        "outputTokens": totals["output_tokens"],
        "cacheReadTokens": totals["cache_read_tokens"],
        "events": totals["events"],
        "priceCard": PRICE_CARD_VERSION,
        "rows": ordered,
    }


def recent(conn, organization_id: str, *, month: str | None = None,
           agent_user_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """The newest ledger rows, for the screen's detail and for a support question."""
    _label, start, end = month_window(month)
    clauses = _month_clauses(organization_id, start, end)
    if agent_user_id:
        clauses.append(usage_event.c.agent_user_id == agent_user_id)
    rows = conn.execute(select(usage_event).where(and_(*clauses))
                        .order_by(desc(usage_event.c.recorded_at), desc(usage_event.c.id))
                        .limit(max(1, min(int(limit or 100), MAX_PAGE)))).mappings().all()
    names = agent_labels(conn, organization_id, [str(row["agent_user_id"]) for row in rows])
    return [{
        "id": row["id"],
        "agentUserId": row["agent_user_id"],
        "agentName": names.get(str(row["agent_user_id"]), str(row["agent_user_id"])),
        "model": row["model"],
        "sessionId": row["session_id"],
        "runId": row["run_id"],
        "credits": int(row["credits"]),
        "runMinutes": int(row["run_minutes"]),
        "inputTokens": int(row["input_tokens"]),
        "outputTokens": int(row["output_tokens"]),
        "cacheReadTokens": int(row["cache_read_tokens"]),
        "costUsd": row["cost_usd"],
        "computeCostUsd": row["compute_cost_usd"],
        "source": row["source"],
        "authMode": row["auth_mode"],
        "compute": row["compute"],
        "priceCard": row["price_card"],
        "recordedAt": row["recorded_at"],
    } for row in rows]


# ---------------------------------------------------------------------------
# GraphQL surface
# ---------------------------------------------------------------------------

query = QueryType()
usage_summary_type = ObjectType("UsageSummary")
usage_row_type = ObjectType("UsageRow")
usage_event_type = ObjectType("UsageEvent")

SDL = gql('''
    """
    One row of the append-only usage ledger: what one report added, in tokens,
    credits, run-minutes and money. A delta, never a running total; summing a run's
    rows gives the runner's own last cumulative report.
    """
    type UsageEvent {
      id: ID!
      agentUserId: ID!
      agentName: String!
      model: String!
      runId: String
      sessionId: ID
      inputTokens: Int!
      outputTokens: Int!
      cacheReadTokens: Int!
      credits: Int!
      runMinutes: Int!
      "USD for the tokens, as a decimal string. Never a float."
      costUsd: String!
      "USD for managed compute, as a decimal string."
      computeCostUsd: String!
      "finish | events | correction"
      source: String!
      "byo-key | managed-key"
      authMode: String!
      "customer | managed"
      compute: String!
      "The price-card version this row was priced with, so a reprice cannot rewrite it."
      priceCard: String!
      recordedAt: DateTime!
    }

    "One grouping of the month's spend: an agent, a model, or a month."
    type UsageRow {
      key: ID!
      label: String!
      agentUserId: ID
      model: String
      month: String!
      inputTokens: Int!
      outputTokens: Int!
      cacheReadTokens: Int!
      credits: Int!
      runMinutes: Int!
      runs: Int!
      costUsd: String!
      computeCostUsd: String!
      "The agent's monthly credit ceiling, when grouped by agent and one is set."
      limitCredits: Int
      "queue | stop, when grouped by agent."
      budgetAction: String
    }

    """
    A month of usage for the workspace, grouped by agent, model or month. Admin only:
    the ledger is billing data for the whole workspace, not one member's.
    """
    type UsageSummary {
      month: String!
      "agent | model | month"
      groupBy: String!
      credits: Int!
      runMinutes: Int!
      inputTokens: Int!
      outputTokens: Int!
      cacheReadTokens: Int!
      events: Int!
      costUsd: String!
      computeCostUsd: String!
      totalUsd: String!
      "The price-card version the totals were computed with; rows carry their own."
      priceCard: String!
      rows: [UsageRow!]!
    }

    """
    One agent's credit budget for a month: the ceiling, the spend, and what happens
    when it is reached.
    """
    type UsageBudget {
      month: String!
      "Null when the workspace stated no ceiling for this agent."
      limitCredits: Int
      usedCredits: Int!
      remainingCredits: Int
      exhausted: Boolean!
      "queue | stop"
      action: String!
    }

    extend type Query {
      "Workspace usage for a month (YYYY-MM, default the current month), grouped."
      usageSummary(month: String, groupBy: String): UsageSummary!
      "The newest ledger rows, newest first."
      usageEvents(month: String, agentUserId: ID, limit: Int): [UsageEvent!]!
      "One agent's credit budget for a month. Null when no agent member matches."
      usageBudget(agentUserId: ID!, month: String): UsageBudget
    }
''')


def _ctx(info):
    return info.context["store"], info.context["org_id"], info.context["user_id"]


def _require_admin(store, org, uid):
    """The ledger is workspace billing data: an admin reads it, a member does not.

    The same gate ``agentPolicy`` uses, applied before any query runs, so a member
    cannot read the workspace's spend by asking a narrower question.
    """
    actor = store.get_user(org, uid)
    if not actor or not actor.get("admin"):
        raise GraphQLError(
            "Only workspace admins can read the usage ledger.",
            extensions={"code": "FORBIDDEN"},
        )


@query.field("usageSummary")
def r_usage_summary(_, info, month=None, groupBy=None):  # noqa: N803 - GraphQL argument names
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    try:
        with store.engine.connect() as conn:
            return summary(conn, org, group_by=(groupBy or "agent"), month=month)
    except ReportError as bad:
        raise GraphQLError(str(bad), extensions={"code": "BAD_USER_INPUT",
                                                 "field": "groupBy"}) from None


@query.field("usageEvents")
def r_usage_events(_, info, month=None, agentUserId=None, limit=None):  # noqa: N803
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    try:
        with store.engine.connect() as conn:
            return recent(conn, org, month=month, agent_user_id=agentUserId,
                          limit=limit or 100)
    except ReportError as bad:
        raise GraphQLError(str(bad), extensions={"code": "BAD_USER_INPUT",
                                                 "field": "month"}) from None


@query.field("usageBudget")
def r_usage_budget(_, info, agentUserId, month=None):  # noqa: N803
    store, org, uid = _ctx(info)
    _require_admin(store, org, uid)
    try:
        with store.engine.connect() as conn:
            member = conn.execute(select(agent_member.c.id).where(and_(
                agent_member.c.organization_id == org,
                agent_member.c.user_id == agentUserId,
            ))).scalar()
            if not member:
                return None
            status = budget_status(conn, org, agentUserId, month=month)
    except ReportError as bad:
        raise GraphQLError(str(bad), extensions={"code": "BAD_USER_INPUT",
                                                 "field": "month"}) from None
    return {
        "month": status["month"],
        "limitCredits": status["limit"],
        "usedCredits": status["used"],
        "remainingCredits": status["remaining"],
        "exhausted": status["exhausted"],
        "action": status["action"],
    }
