"""Plain-language filters ("AI filter"): text in, editable view filters out.

Linear runs every prompt through a model (``issueFilterSuggestion``, linear-capture/s4-*).
Hoja answers in two tiers (the prototype comparison is in
artifacts/runs/20261123-views-depth/PROTOTYPES.md):

1. A deterministic parser for the phrases a triage lead actually types ("unassigned bugs
   due this week", "overdue tasks assigned to me"). It knows the workspace's own label,
   state, member and project names, answers in milliseconds, needs no model, and reports
   every word it did not understand.
2. When words are left over and the workspace has a model configured (the same provider
   as Agent Chat and initiative drafts), the model gets the prompt plus the workspace's
   vocabulary and must answer with a filter document. Its answer passes the same validator
   as a saved view (``views.parse_filters``); one retry carries the validator's message.

Either way the result is ordinary filter chips the person can see and edit, never a hidden
query, and a prompt that means nothing concrete ("what should I work on next") gets no
filter rather than a confident wrong one.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, select

from hoja_server import views as viewdocs
from hoja_server.db import issue_label, project, team_member, user, workflow_state

log = logging.getLogger(__name__)

PRIORITIES = {"urgent": 1, "high": 2, "medium": 3, "normal": 3, "low": 4}
UNITS = {"day": "D", "days": "D", "week": "W", "weeks": "W", "month": "M", "months": "M",
         "year": "Y", "years": "Y"}
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "nine": 9, "ten": 10, "couple": 2, "few": 3}
# Words that carry no filter meaning in a triage prompt.
STOPWORDS = frozenset(["a", "an", "the", "all", "any", "every", "everything", "anything", "show", "find", "list", "give", "get", "me", "please", "that", "which", "who", "are", "is", "were", "was", "be", "been", "being", "with", "without", "of", "for", "and", "or", "in", "on", "at", "to", "from", "by", "as", "it", "its", "this", "these", "those", "issue", "issues", "task", "tasks", "ticket", "tickets", "item", "items", "thing", "things", "stuff", "ones", "one", "label", "labels", "labelled", "labeled", "tagged", "tag", "priority", "priorities", "status", "state", "project", "projects", "only", "just", "also", "have", "has", "had", "there", "their", "them", "whose", "whos", "still", "yet", "currently", "right", "now", "please", "pls", "thanks", "that's", "thats", "what's", "whats", "what", "sitting", "sit", "work", "request", "requests"])


@dataclass
class Vocabulary:
    """The workspace's names, lower-cased, mapped to ids."""
    users: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)
    projects: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, conn: Any, org_id: str, viewer_id: str | None, team_id: str | None = None) -> Vocabulary:
        vocab = cls()
        for uid, name, display in conn.execute(select(user.c.id, user.c.name, user.c.display_name)
                                               .where(and_(user.c.organization_id == org_id,
                                                           user.c.active.is_not(False)))):
            for n in (name, display):
                if n:
                    vocab.users.setdefault(n.lower(), uid)
                    vocab.users.setdefault(n.split()[0].lower(), uid)
        for lid, name in conn.execute(select(issue_label.c.id, issue_label.c.name).where(and_(
                issue_label.c.organization_id == org_id, issue_label.c.archived_at.is_(None)))):
            vocab.labels.setdefault(name.lower(), lid)
        stmt = select(workflow_state.c.id, workflow_state.c.name).where(
            workflow_state.c.organization_id == org_id)
        if team_id:
            stmt = stmt.where(workflow_state.c.team_id == team_id)
        elif viewer_id:
            mine = select(team_member.c.team_id).where(team_member.c.user_id == viewer_id)
            stmt = stmt.where(workflow_state.c.team_id.in_(mine))
        for sid, name in conn.execute(stmt):
            vocab.states.setdefault(name.lower(), sid)
        for pid, name in conn.execute(select(project.c.id, project.c.name).where(and_(
                project.c.organization_id == org_id, project.c.archived_at.is_(None)))):
            vocab.projects.setdefault(name.lower(), pid)
        return vocab


@dataclass
class Suggestion:
    filters: dict[str, Any] | None
    unmatched: list[str]
    source: str  # "rules", "model", or "none"
    message: str | None = None


# ------------------------------------------------------------------ the rules tier

def _duration(n: int, unit: str) -> str:
    return f"P{n}{UNITS[unit]}"


def _amount(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    return NUMBERS.get(text)


def _days_since(weekday: str, now: Any) -> int:
    """Days back to the most recent ``weekday`` strictly before today (a week on that day)."""
    target = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
              "sunday").index(weekday)
    return int((now.weekday() - target - 1) % 7 + 1)


class _Parser:
    def __init__(self, prompt: str, vocab: Vocabulary, now: Any = None) -> None:
        from datetime import datetime, timezone

        self.now = now or datetime.now(timezone.utc)
        self.vocab = vocab
        self.text = " " + re.sub(r"\s+", " ", prompt.strip().lower()) + " "
        self.parts: list[tuple[str, dict[str, Any]]] = []
        self.quoted: list[tuple[str, str]] = re.findall(r'"([^"]+)"|“([^”]+)”', prompt)

    def take(self, pattern: str) -> re.Match[str] | None:
        """Find ``pattern`` (on word boundaries), consume it, return the match."""
        m = re.search(rf"(?<![\w-]){pattern}(?![\w-])", self.text)
        if m:
            self.text = self.text[:m.start()] + " " + self.text[m.end():]
        return m

    def add(self, prop: str, cmp: dict[str, Any]) -> None:
        self.parts.append((prop, cmp))

    # Names from the workspace, longest first, so "mobile app" wins over "app".
    def names(self, table: dict[str, str], plural: bool = False) -> list[tuple[str, str, int]]:
        found = []
        for name in sorted(table, key=len, reverse=True):
            variants = [re.escape(name)]
            if plural:
                variants.append(re.escape(name) + "e?s")
                if name.endswith("y"):
                    variants.append(re.escape(name[:-1]) + "ies")
            m = re.search(rf"(?<![\w-])(?:{'|'.join(variants)})(?![\w-])", self.text)
            if m:
                found.append((name, table[name], m.start()))
                self.text = self.text[:m.start()] + " " * (m.end() - m.start()) + self.text[m.end():]
        return found

    def parse(self) -> tuple[dict[str, Any] | None, list[str]]:
        self.title()
        self.people()
        self.dates()
        self.priority()
        self.states()
        self.structure()
        self.projects()
        self.labels()
        if any(prop == "creator" for prop, _cmp in self.parts):
            # "I filed …", "Bob opened …": the verb named the creator and carries no date.
            self.take(r"(?:created|made|filed|opened|reported|logged|raised)")
        leftover = [w for w in re.findall(r"[\w'-]+", self.text) if w not in STOPWORDS]
        if not self.parts:
            return None, leftover
        out: dict[str, Any] = {}
        extra: list[dict[str, Any]] = []
        for prop, cmp in self.parts:
            target = out.setdefault(prop, {})
            if set(cmp) & set(target):
                extra.append({prop: cmp})
            else:
                target.update(cmp)
        if extra:
            out["and"] = extra
        return out, leftover

    # -- phrases ---------------------------------------------------------------------
    def title(self) -> None:
        m = self.take(r"(?:with )?([\w-]+) in (?:the|its) (?:title|name)")
        if m:
            self.add("title", {"containsIgnoreCase": m.group(1)})
        for a, b in self.quoted:
            words = (a or b).strip()
            if not words:
                continue
            self.take(rf"(?:title|titled|named|called)?\s*(?:contains|containing|with|has)?\s*[\"“]{re.escape(words.lower())}[\"”](?:\s+in (?:the|its) (?:title|name))?")
            self.take(r"(?:title|titled|named|called)")
            self.add("title", {"containsIgnoreCase": words})

    def people(self) -> None:
        if self.take(r"(?:unassigned|without (?:an )?assignee|no assignee|not assigned(?! to)|nobody assigned|"
                     r"assigned to (?:no ?one|nobody)|(?:that )?(?:no ?one|nobody) (?:owns|has (?:taken|claimed|picked up))|(?:with )?no owner|ownerless)"):
            self.add("assignee", {"is": None})
        if self.take(r"(?:not assigned to me|assigned to (?:someone|somebody) (?:else|other than me)|"
                     r"(?:someone|somebody) (?:else|other than me)|not mine)"):
            self.add("assignee", {"isMe": False})
        if self.take(r"(?:assigned to me|(?:on )?my plate|(?<!show )(?<!give )(?<!find )(?<!get )for me|my|mine|"
                     r"i(?:'m| am) assigned(?: to)?|i own)"):
            self.add("assignee", {"isMe": True})
        if self.take(r"(?:i(?= (?:created|made|filed|opened|reported|logged|raised)\b)|created by me|reported by me|opened by me|filed by me)"):
            self.add("creator", {"isMe": True})
        if self.take(r"(?:i(?= (?:picked up|grabbed|took|own)\b))"):
            self.add("assignee", {"isMe": True})
            self.take(r"(?:picked up|grabbed|took)")
        if self.take(r"(?:i(?:'m| am) subscribed to|i follow|i'm following|subscribed by me|i subscribed to|i'm watching)"):
            self.add("subscriber", {"isMe": True})
        names = "|".join(re.escape(n) for n in sorted(self.vocab.users, key=len, reverse=True))
        if not names:
            return
        many = rf"((?:{names})(?:'s)?(?:(?:,| or| and|, or|, and) (?:{names})(?:'s)?)*)"

        def ids(group: str) -> list[str]:
            parts = re.split(r",? (?:or|and) |, ", group)
            return list(dict.fromkeys(self.vocab.users[n.strip().removesuffix("'s")]
                                      for n in parts if n.strip()))

        def one_or_many(found: list[str], negative: bool = False) -> dict[str, Any]:
            if negative:
                return {"isNot": found[0]} if len(found) == 1 else {"isNeitherOf": found}
            return {"is": found[0]} if len(found) == 1 else {"isEitherOf": found}

        m = self.take(rf"(?:aren't|are not|isn't|is not|not) (?:with |assigned to |owned by )?{many}")
        if m:
            self.add("assignee", one_or_many(ids(m.group(1)), negative=True))
        for verb, prop in (("(?:assigned to|owned by)", "assignee"),
                           ("(?:created by|reported by|opened by|filed by)", "creator")):
            m = self.take(rf"not {verb} {many}")
            if m:
                self.add(prop, one_or_many(ids(m.group(1)), negative=True))
            m = self.take(rf"{verb} {many}")
            if m:
                self.add(prop, one_or_many(ids(m.group(1))))
        m = self.take(rf"{many}(?= (?:created|made|filed|opened|reported))")
        if m:
            self.add("creator", one_or_many(ids(m.group(1))))
        m = self.take(rf"(?:what(?:'s| is| are) )?{many} (?:is |are |'s )?(?:working on|doing|busy with)(?: right now| now| currently| at the moment)?")
        if m:
            self.add("assignee", one_or_many(ids(m.group(1))))
            self.add("stateType", {"is": "started"})
        m = self.take(rf"(?:for|of) {many}") \
            or self.take(rf"((?:{names})(?:(?:,| or| and|, or|, and) (?:{names}))*'s)")
        if m:
            self.add("assignee", one_or_many(ids(m.group(1))))

    def dates(self) -> None:
        self.text = re.sub(r"\b(?:a |one )?fortnight\b", "2 weeks", self.text)
        amount = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|couple of|few)"
        unit = r"(days?|weeks?|months?|years?)"

        def dur(m: re.Match[str], a_group: int = 1, u_group: int = 2) -> str:
            n = _amount(m.group(a_group).replace(" of", "")) or 1
            return _duration(n, m.group(u_group))

        for word, prop in (("created|opened|filed|reported|added", "createdAt"),
                           ("updated|changed|touched|modified|moved", "updatedAt"),
                           ("completed|finished|closed|(?:got )?done|resolved|shipped", "completedAt")):
            m = self.take(rf"(?:not|n't|haven't|hasn't|have not|has not|(?:no ?one|nobody)(?:'s| has| have)?) (?:been )?(?:{word}) (?:in|for|since)(?: the last| the past| over)? {amount} {unit}")
            if m:
                self.add(prop, {"before": "-" + dur(m)})
                continue
            m = self.take(rf"(?:{word}) (?:more than|over|longer than|before) {amount} {unit} ago")
            if m:
                self.add(prop, {"before": "-" + dur(m)})
                continue
            m = self.take(rf"(?:{word})(?: items| issues| tasks)? (?:in|within|during|over|from) the (?:last|past) {amount} {unit}") \
                or self.take(rf"(?:{word}) (?:in|within) (?:the )?(?:last|past) {unit}") \
                or self.take(rf"(?:{word}) (?:in|within) {amount} {unit}")
            if m:
                if m.lastindex == 1:
                    self.add(prop, {"after": "-" + _duration(1, m.group(1))})
                else:
                    self.add(prop, {"after": "-" + dur(m)})
                continue
            m = self.take(rf"(?:{word}) (?:last|this) (week|month|year)")
            if m:
                self.add(prop, {"after": "-" + _duration(1, m.group(1))})
                continue
            m = self.take(rf"(?:{word}) (today|yesterday)")
            if m:
                self.add(prop, {"is": m.group(1)})
                continue
            m = self.take(rf"(?:{word}) since (?:last )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)")
            if m:
                days = _days_since(m.group(1), self.now)
                self.add(prop, {"after": f"-P{days}D" if days % 7 else f"-P{days // 7}W"})
        m = self.take(rf"(?:stale|untouched|idle) (?:for|in) {amount} {unit}")
        if m:
            self.add("updatedAt", {"before": "-" + dur(m)})
        if self.take(r"(?:overdue|past due|late)"):
            self.add("dueDate", {"is": "overdue"})
        if self.take(r"(?:no|without(?: a)?|missing(?: a)?) due dates?"):
            self.add("dueDate", {"is": None})
        m = self.take(r"due (today|tomorrow)")
        if m:
            self.add("dueDate", {"is": m.group(1)})
        m = self.take(rf"due (?:in|within) (?:the next |the coming )?{amount} {unit}") \
            or self.take(rf"due (?:in|within) (?:the )?next {unit}")
        if m:
            if m.lastindex == 1:
                self.add("dueDate", {"before": _duration(1, m.group(1))})
            else:
                self.add("dueDate", {"before": dur(m)})
        m = self.take(r"due (?:this|within (?:a|the)|in (?:a|the next)|next) (week|month)")
        if m:
            self.add("dueDate", {"before": _duration(1, m.group(1))})

    def priority(self) -> None:
        if self.take(r"(?:no|without(?: a)?) priority"):
            self.add("priority", {"is": 0})
            return
        self.text = re.sub(r"\bp([1-4])s?\b", lambda m: {"1": "urgent", "2": "high", "3": "medium", "4": "low"}[m.group(1)], self.text)
        self.text = re.sub(r"\b(hi|high)-pri(?:ority|o)?\b", "high", self.text)
        names = "|".join(PRIORITIES)
        m = self.take(rf"((?:{names})(?:(?:,| or| and|, or|, and|/) (?:{names}))*)(?: priority| prio| pri)?")
        if m:
            values = list(dict.fromkeys(PRIORITIES[w] for w in re.findall(names, m.group(1))))
            self.add("priority", {"is": values[0]} if len(values) == 1 else {"isEitherOf": values})

    def states(self) -> None:
        if self.take(r"(?:closed)"):
            self.add("stateType", {"isEitherOf": ["completed", "canceled"]})
        if self.take(r"(?:open|active|not done|unfinished|outstanding|remaining)"):
            self.add("stateType", {"isNeitherOf": ["completed", "canceled"]})
        if self.take(r"(?:aren't|are not|isn't|is not|not) (?:yet )?(?:done|finished|completed|closed)(?: yet)?"):
            self.add("stateType", {"isNeitherOf": ["completed", "canceled"]})
        if self.take(r"(?:canceled|cancelled)"):
            self.add("stateType", {"is": "canceled"})
        if self.take(r"(?:triage|untriaged)"):
            self.add("stateType", {"is": "triage"})
        found = self.names(self.vocab.states)
        if self.take(r"(?:started|in flight|being worked on|underway)(?: work)?"):
            self.add("stateType", {"is": "started"})
        aliases = {"to do": "todo", "to-do": "todo", "wip": "in progress", "in-progress": "in progress",
                   "under review": "in review",
                   "in code review": "in review"}
        for phrase, target in aliases.items():
            if target in self.vocab.states and self.take(re.escape(phrase)):
                found.append((target, self.vocab.states[target], 0))
        for name, sid, _pos in found:
            if name == "done":
                self.add("stateType", {"is": "completed"})
            elif name in ("canceled", "cancelled"):
                self.add("stateType", {"is": "canceled"})
            else:
                self.add("state", {"is": sid})
        if self.take(r"(?:done|completed|finished|resolved|shipped)"):
            self.add("stateType", {"is": "completed"})

    def structure(self) -> None:
        if self.take(r"(?:top[- ]level|not (?:a )?sub[- ]?(?:issues?|tasks?)|root)"):
            self.add("isSubIssue", {"is": False})
        if self.take(r"(?:with|(?:that )?(?:have|has)) (?:sub[- ]?(?:issues|tasks)|subtasks|children)|parent (?:issues|tasks)|parents"):
            self.add("hasSubIssues", {"is": True})
        if self.take(r"(?:sub[- ]?(?:issues|tasks)|subtasks|child (?:issues|tasks))(?: only)?"):
            self.add("isSubIssue", {"is": True})

    def projects(self) -> None:
        if self.take(r"(?:no|without(?: a)?|not in (?:a|any)) project"):
            self.add("project", {"is": None})
        found = self.names(self.vocab.projects)
        if found:
            ids = [pid for _n, pid, _p in sorted(found, key=lambda f: f[2])]
            self.add("project", {"is": ids[0]} if len(ids) == 1 else {"isEitherOf": ids})

    def labels(self) -> None:
        if self.take(r"(?:without|with no|no|unlabell?ed|missing) labels?|unlabell?ed"):
            self.add("labels", {"is": None})
        negated: list[str] = []
        for name in sorted(self.vocab.labels, key=len, reverse=True):
            esc = re.escape(name)
            if self.take(rf"(?:that are not|that aren't|not|isn't|without|except|excluding|but not|no|"
                         rf"not label(?:l)?ed|not tagged)(?: as| with)?(?: an?| the| any)? {esc}(?:e?s)?(?: label)?"):
                negated.append(self.vocab.labels[name])
        joined = re.search(r"(?<![\w-])(?:and|or|,|&|/)(?![\w-])", self.text) is not None
        found = self.names(self.vocab.labels, plural=True)
        self.take(r"(?:in )?the group")
        positive = list(dict.fromkeys(lid for _n, lid, _p in sorted(found, key=lambda f: f[2])))
        cmp: dict[str, Any] = {}
        if positive:
            # "bugs and improvements" is either label; "api bugs" (labels as modifiers) is both,
            # which is also what two separate chips mean on Linear.
            cmp["includesAny" if joined or len(positive) == 1 else "includesAll"] = positive
        if negated:
            cmp["includesNone"] = list(dict.fromkeys(negated))
        if cmp:
            self.add("labels", cmp)


NEGATIONS = frozenset(["not", "no", "never", "without", "except", "excluding", "nobody", "noone", "nobody's", "isn't", "aren't", "don't", "doesn't", "haven't", "hasn't", "other", "else"])


def _meaningful(words: list[str], vocab: Vocabulary) -> bool:
    known = set(vocab.users) | set(vocab.labels) | set(vocab.states) | set(vocab.projects)
    return any(w in NEGATIONS or w.removesuffix("'s") in known or w.endswith("n't") for w in words)


def parse(prompt: str, vocab: Vocabulary) -> tuple[dict[str, Any] | None, list[str]]:
    """The rules tier: (filters or None, words it did not understand)."""
    return _Parser(prompt, vocab).parse()


# ------------------------------------------------------------------ the model tier

MODEL_INSTRUCTIONS = """Turn a request for a list of tasks into a filter document. Answer with
JSON only: {"filters": {...}} or {"filters": null} when the request names nothing concrete.
Grammar: each key is a property, each value maps an operator to an operand; several keys
mean AND; "and"/"or" take lists of documents.
Properties and operators:
- assignee, creator: is <user id|null>, isNot, isEitherOf [ids], isNeitherOf, isMe true|false
- state: is <state id>; stateType: is|isEitherOf|isNeitherOf triage|backlog|unstarted|started|completed|canceled
- priority: is 0 (none)|1 urgent|2 high|3 medium|4 low, isEitherOf [n], isGreaterThan, isLessThan
- labels: includesAny [label ids], includesAll, includesNone, is null (no labels)
- project, cycle: is <id|null>, isEitherOf; subscriber: isMe true
- createdAt, updatedAt, completedAt, dueDate: is|before|after|onOrBefore|onOrAfter a date,
  "today", "yesterday", "tomorrow", "overdue" (dueDate, with is), or an ISO-8601 duration
  from now ("-P1W" a week ago, "P3D" three days ahead)
- title, description: containsIgnoreCase <text>; isSubIssue, hasSubIssues: is true|false
Use only ids from the vocabulary. Do not invent conditions the request does not state."""


def _model_filters(provider: dict[str, Any], org: str, prompt: str, vocab: Vocabulary, kind: str,
                   client_factory: Any = None) -> tuple[dict[str, Any] | None, str | None]:
    """Ask the workspace's model; (validated filters, error message)."""
    from hoja_server.secrets_box import decrypt_secret

    if client_factory is None:
        import openai

        from hoja_server.model_providers import validate_model_base_url

        def client_factory() -> Any:
            base_url = validate_model_base_url(provider["base_url"]) or None
            return openai.OpenAI(base_url=base_url, timeout=20, max_retries=0,
                                 api_key=decrypt_secret(provider["encrypted_api_key"], org),
                                 http_client=openai.DefaultHttpxClient(follow_redirects=False))

    client = client_factory()
    vocabulary = {"users": vocab.users, "labels": vocab.labels, "states": vocab.states,
                  "projects": vocab.projects}
    messages = [{"role": "system", "content": MODEL_INSTRUCTIONS},
                {"role": "user", "content": json.dumps({"vocabulary": vocabulary, "request": prompt})}]
    error = None
    for _attempt in range(2):
        reply = client.chat.completions.create(
            model=provider["model_id"], messages=messages, max_tokens=400,
            response_format={"type": "json_object"})
        text = (reply.choices[0].message.content or "").strip()
        try:
            doc = json.loads(text)
            filters = doc.get("filters") if isinstance(doc, dict) else None
            if filters is None:
                return None, None
            return viewdocs.parse_filters(kind, filters), None
        except (ValueError, viewdocs.ViewError) as exc:
            error = str(exc)
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"That filter was rejected: {error} "
                                                     "Answer again with valid JSON."}]
    return None, error


def suggest(store: Any, org_id: str, viewer_id: str | None, prompt: str, *, kind: str = "issue",
            team_id: str | None = None, provider: dict[str, Any] | None = None,
            client_factory: Any = None) -> Suggestion:
    """Rules first; the model only for what the rules could not read."""
    prompt = (prompt or "").strip()[:500]
    if not prompt:
        return Suggestion(None, [], "none", "Type what you want to see.")
    with store.engine.connect() as conn:
        vocab = Vocabulary.load(conn, org_id, viewer_id, team_id)
    filters, leftover = parse(prompt, vocab)
    if filters is not None:
        filters = viewdocs.parse_filters(kind, filters)
    if not leftover:
        return Suggestion(filters, [], "rules" if filters else "none",
                          None if filters else "Nothing in that names a filter.")
    if provider is None:
        from hoja_server.agent_chat import _enabled_provider

        provider = _enabled_provider(store, org_id)
    if provider:
        try:
            model, error = _model_filters(provider, org_id, prompt, vocab, kind, client_factory)
        except Exception:  # an unreachable model falls back to what the rules understood
            log.warning("ai filter: model call failed", exc_info=True)
            model, error = None, "The model did not answer."
        if model is not None:
            return Suggestion(model, [], "model")
        if error:
            return Suggestion(filters, leftover, "rules" if filters else "none", error)
        return Suggestion(None, leftover, "none", "Nothing in that names a filter.")
    # Any word left unread could change the answer ("stale bugs" is not "bugs"; held-out set
    # v2), so the rules tier never answers partially: it abstains and says what it missed.
    return Suggestion(None, leftover, "none",
                      "Couldn't turn that into filters: " + ", ".join(leftover[:6]) + ".")
