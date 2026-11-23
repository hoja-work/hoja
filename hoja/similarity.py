"""Lexical duplicate detection for drafts, ranked client-side (HOJA-208).

The server's search is a single literal substring match ordered by
``updated_at`` (``hoja_server/store.py::search_issues``): a paraphrase returns
nothing, or everything that shares one word, in no relevance order. So ranking
happens here, over a corpus the caller fetched.

Phase 1 is deliberately lexical: IDF-weighted token overlap with the title
weighted three times, stop-words removed and a light stemmer. That finds the
same *bug restated*, which is the duplicate case that matters. It does **not**
find synonyms, typos, or reordered phrasing — ``log out`` will not match
``sign-out``. Say so rather than implying otherwise.

Two numbers are reported because they answer different questions:

- ``score``: how much distinctive material the two texts share.
- ``coverage``: the share of the draft's distinct terms that appear at all.
- ``strength``: the share of the draft's *IDF mass* that appears in the
  candidate — how much of what the draft says is already said there.

Coverage alone is not enough to gate on: a novel draft that shares five ordinary
words with a long, unrelated body reaches 0.56 coverage. Strength weights each
term by how informative it is and counts a term the corpus has never seen as
fully unmatched, so describing something new scores low even when common words
line up. That is the number the duplicate gate uses.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

# Function words only: nothing here can be an entity name or a verb in a bug
# report. Deliberately conservative — dropping real words would make a
# duplicate invisible, which is the failure this feature exists to prevent.
STOP_WORDS: frozenset[str] = frozenset(
    ["a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those", "there", "here", "is", "are", "was", "were", "be", "been", "being", "am", "i", "me", "my", "we", "our", "us", "you", "your", "he", "him", "his", "she", "her", "it", "its", "they", "them", "their", "of", "to", "in", "on", "for", "with", "at", "by", "from", "as", "into", "out", "up", "down", "about", "over", "under", "again", "further", "once", "both", "each", "few", "other", "own", "same", "too", "only", "very", "just", "also", "so", "not", "no", "nor", "do", "does", "did", "doing", "done", "have", "has", "had", "having", "when", "where", "which", "who", "whom", "what", "how", "why", "whether", "while", "can", "could", "should", "would", "will", "shall", "may", "might", "must", "s", "t", "don", "now", "yet", "still", "even", "ever", "never", "always"]
)

_WORD_RE = re.compile(r"[a-z0-9]+")

# A word this short is not stemmed: "used", "ping", "ring", "seed" and "need"
# would all lose their tail and stop matching themselves. Four still lets the
# plural rule fire on "signs" -> "sign", which is the difference between a
# paraphrase matching its issue and not.
_MIN_STEM_LEN = 4


def stem(word: str) -> str:
    """Light, deterministic suffix stripping.

    Only the plural/``ing``/``ed`` forms that actually differ between how one
    person describes a bug and how the next person files it. No dictionary and
    no language model, so the same input always yields the same output.
    """
    if len(word) <= _MIN_STEM_LEN:
        return word
    if word.endswith("ies") and len(word) > _MIN_STEM_LEN + 1:
        return word[:-3] + "y"
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    if word.endswith("es") and len(word) > _MIN_STEM_LEN + 1:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokenize(text: str | None) -> list[str]:
    """Lowercase word tokens with stop-words removed, in order, with repeats."""
    if not text:
        return []
    out: list[str] = []
    for raw in _WORD_RE.findall(text.lower()):
        if len(raw) < 2 or raw in STOP_WORDS:
            continue
        out.append(stem(raw))
    return out


@dataclass(frozen=True)
class Document:
    """One issue as the scorer sees it."""

    identifier: str
    title: str
    description: str | None = None
    state_name: str | None = None
    state_type: str | None = None
    title_tokens: tuple[str, ...] = field(default=(), repr=False)
    description_tokens: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class Hit:
    """One ranked candidate, with the evidence for its position."""

    identifier: str
    title: str
    state: str | None
    state_type: str | None
    score: float
    coverage: float
    strength: float
    matched_terms: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "title": self.title,
            "state": self.state,
            "stateType": self.state_type,
            "score": round(self.score, 2),
            "coverage": round(self.coverage, 4),
            "strength": round(self.strength, 4),
            "matchedTerms": list(self.matched_terms),
        }


_TITLE_WEIGHT = 3
# A term repeated inside one description is not three times the evidence: cap
# the count before weighting so a long body cannot outrank a matching title.
_TF_CAP = 3


class Corpus:
    """Document bags plus the IDF table the scorer needs.

    Built once per invocation. ``documents`` must already be filtered to the
    scope the caller intends to search; nothing here is stateful across calls.
    """

    def __init__(self, documents: Iterable[Document]) -> None:
        self.documents: list[Document] = []
        df: Counter[str] = Counter()
        for doc in documents:
            title_tokens = tuple(tokenize(doc.title)) if not doc.title_tokens else doc.title_tokens
            description_tokens = (
                tuple(tokenize(doc.description))
                if not doc.description_tokens
                else doc.description_tokens
            )
            prepared = Document(
                identifier=doc.identifier,
                title=doc.title,
                description=doc.description,
                state_name=doc.state_name,
                state_type=doc.state_type,
                title_tokens=title_tokens,
                description_tokens=description_tokens,
            )
            self.documents.append(prepared)
            for term in set(title_tokens) | set(description_tokens):
                df[term] += 1
        self._df: Mapping[str, int] = df
        n = len(self.documents)
        # Smoothed IDF: a term in every document still carries a little weight
        # rather than going negative or dividing by zero.
        self._idf: dict[str, float] = {
            term: math.log((n + 1) / (count + 0.5)) for term, count in df.items()
        }
        self._max_idf: float = max(self._idf.values()) if self._idf else 1.0
        self.median_idf: float = _median(sorted(self._idf.values())) if self._idf else 0.0
        lengths = [len(doc.title_tokens) + len(doc.description_tokens) for doc in self.documents]
        self.average_length: float = (sum(lengths) / len(lengths)) if lengths else 0.0

    def __len__(self) -> int:
        return len(self.documents)

    @property
    def idf(self) -> Mapping[str, float]:
        return self._idf

    def term_weight(self, term: str) -> float:
        """IDF of a term, or the corpus's rarest weight for a term it has never seen.

        An unseen term cannot match anything, so it only ever enters the
        denominator of ``strength``; charging it the corpus maximum keeps the
        ratio on one scale instead of inventing an absolute one that would
        dominate a tiny corpus.
        """
        return self._idf.get(term, self._max_idf)

    def rank(
        self,
        draft: str,
        *,
        limit: int = 5,
        exclude: Sequence[str] = (),
        min_coverage: float = 0.0,
        min_terms: int = 0,
        min_strength: float = 0.0,
    ) -> list[Hit]:
        """Rank the corpus against ``draft``, best first, deterministically."""
        draft_tokens = tokenize(draft)
        draft_distinct = sorted(set(draft_tokens))
        if not draft_distinct or not self.documents:
            return []
        # Every distinct draft term carries its IDF weight into the denominator,
        # including terms this corpus has never seen: a draft full of unknown
        # words is describing something new, and must not read as a duplicate
        # just because its ordinary words also appear elsewhere.
        draft_mass = sum(self.term_weight(term) for term in draft_distinct)
        excluded = {(value or "").strip().upper() for value in exclude if value}

        hits: list[Hit] = []
        for doc in self.documents:
            if doc.identifier.upper() in excluded:
                continue
            title_counts = Counter(doc.title_tokens)
            body_counts = Counter(doc.description_tokens)
            score = 0.0
            matched_mass = 0.0
            matched: list[str] = []
            for term in draft_distinct:
                tf = _TITLE_WEIGHT * title_counts.get(term, 0) + body_counts.get(term, 0)
                if not tf:
                    continue
                matched.append(term)
                weight = min(tf, _TF_CAP) / _TF_CAP
                matched_mass += self.term_weight(term)
                score += self.term_weight(term) * weight
            if not matched:
                continue
            # Length normalisation: a long, templated body contains almost every
            # common word, so raw overlap makes boilerplate look like a match.
            # Dividing by sqrt(doc length / average length) demoted exactly that
            # case in the measured evaluation (a Grafana provisioning run ranked
            # first for "per token api rate limit dashboard" until this was on).
            doc_length = len(doc.title_tokens) + len(doc.description_tokens)
            norm = 1.0
            if self.average_length > 0 and doc_length > self.average_length:
                norm = (doc_length / self.average_length) ** 0.5
            score /= norm
            coverage = len(matched) / len(draft_distinct)
            strength = matched_mass / draft_mass if draft_mass else 0.0
            if coverage < min_coverage or len(matched) < min_terms or strength < min_strength:
                continue
            hits.append(
                Hit(
                    identifier=doc.identifier,
                    title=doc.title,
                    state=doc.state_name,
                    state_type=doc.state_type,
                    score=score,
                    coverage=coverage,
                    strength=strength,
                    # Most informative first: the reader checks the reason, not
                    # just the rank.
                    matched_terms=tuple(
                        sorted(matched, key=lambda term: (-self.term_weight(term), term))
                    ),
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.identifier))
        return hits[:limit]


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2
