"""Tiny attribute-based access control (ABAC) engine.

A decision is a pure function of four attribute bags: the *subject* (who is asking), the
*resource* (what they touch), the *action*, and the *environment* (server facts). Rules
are data: each has an effect, the actions it covers, and a predicate over the attributes.

Evaluation is deny-overrides with default deny:

1. Any matching ``deny`` rule refuses the request (first one in rule order is reported).
2. Otherwise any matching ``permit`` rule allows it.
3. Otherwise the request is refused ("no rule permits").

Every decision names the rule that decided it, so refusals can be audited and explained.
Predicates read attributes through :class:`Attrs`, which returns ``None`` for anything
missing, so a rule can never crash on an attribute the caller did not supply.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

PERMIT = "permit"
DENY = "deny"
ANY = "*"


class Attrs:
    """Read-only attribute bag; missing keys read as None."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any] | None = None) -> None:
        self._data = dict(data or {})

    def __getattr__(self, name: str) -> Any:
        return self._data.get(name)

    def get(self, name: str, default: Any = None) -> Any:
        return self._data.get(name, default)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)


@dataclass(frozen=True)
class Context:
    action: str
    s: Attrs  # subject
    r: Attrs  # resource
    e: Attrs  # environment


@dataclass(frozen=True)
class Rule:
    id: str
    effect: str
    actions: frozenset
    when: Callable[[Context], bool]
    reason: str

    def covers(self, action: str) -> bool:
        return ANY in self.actions or action in self.actions


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    rule: str | None = None

    def __bool__(self) -> bool:
        return self.allowed


def permit(rule_id: str, actions: Iterable[str] | str, when: Callable[[Context], bool], reason: str = "") -> Rule:
    return Rule(rule_id, PERMIT, _actions(actions), when, reason or rule_id)


def deny(rule_id: str, actions: Iterable[str] | str, when: Callable[[Context], bool], reason: str) -> Rule:
    return Rule(rule_id, DENY, _actions(actions), when, reason)


def _actions(actions: Iterable[str] | str) -> frozenset:
    return frozenset([actions]) if isinstance(actions, str) else frozenset(actions)


class Policy:
    def __init__(self, rules: Iterable[Rule]) -> None:
        self.rules = tuple(rules)
        seen: set[str] = set()
        for rule in self.rules:
            if rule.id in seen:
                raise ValueError(f"duplicate rule id {rule.id!r}")
            seen.add(rule.id)

    def decide(self, action: str, subject: Mapping | None = None, resource: Mapping | None = None,
               environment: Mapping | None = None) -> Decision:
        ctx = Context(action, Attrs(subject), Attrs(resource), Attrs(environment))
        permitted: Rule | None = None
        for rule in self.rules:
            if not rule.covers(action):
                continue
            try:
                hit = bool(rule.when(ctx))
            except Exception:  # a broken predicate must fail closed, never open
                hit = rule.effect == DENY
            if not hit:
                continue
            if rule.effect == DENY:
                return Decision(False, rule.reason, rule.id)
            if permitted is None:
                permitted = rule
        if permitted is not None:
            return Decision(True, permitted.reason, permitted.id)
        return Decision(False, "No rule permits this action.", None)
