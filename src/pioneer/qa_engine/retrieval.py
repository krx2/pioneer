"""Deterministic retrieval half of the Q&A Engine (implementation.md Stage 11).

Plain TF-IDF cosine-similarity ranking over an in-memory corpus of `Passage`s — no model, no I/O.
Kept separate from `engine.py`'s LLM call so this half stays unit-testable without ever invoking a
model, per architecture.md §3's split between deterministic and LLM code paths.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """
    a an and are as at be by can do for from how i in into is it made of on or than that the
    their there this to what when where which who why with you your
    """.split()
)
"""Common function words, dropped before scoring so two passages don't look "relevant" to each
other purely for sharing "the"/"is"/"of" — with a corpus this small, IDF alone doesn't push their
weight low enough."""

_TITLE_WEIGHT = 0.5
"""How much a passage's title being named in the question adds to its text similarity."""


@dataclass(frozen=True)
class Passage:
    passage_id: str
    text: str
    source: str
    """Human-readable citation label (e.g. a wiki page title or recipe name)."""


@dataclass(frozen=True)
class ScoredPassage:
    passage: Passage
    score: float


def significant_words(text: str) -> list[str]:
    """`text`'s words worth matching on, lower-cased, function words dropped — what retrieval
    scores passages by, and what the Stage 15 grounding check compares an answer against."""
    return [token for token in _TOKEN_RE.findall(text.lower()) if token not in _STOPWORDS]


def _idf(corpus_tokens: Sequence[Sequence[str]]) -> dict[str, float]:
    document_count = len(corpus_tokens)
    document_frequency: Counter[str] = Counter()
    for tokens in corpus_tokens:
        document_frequency.update(set(tokens))
    return {
        term: math.log((document_count + 1) / (count + 1)) + 1.0
        for term, count in document_frequency.items()
    }


def _tfidf_vector(tokens: Sequence[str], idf: dict[str, float]) -> dict[str, float]:
    term_frequency = Counter(tokens)
    return {term: count * idf.get(term, 0.0) for term, count in term_frequency.items()}


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    shared_terms = a.keys() & b.keys()
    numerator = sum(a[term] * b[term] for term in shared_terms)
    if numerator == 0:
        return 0.0
    norm_a = math.sqrt(sum(weight * weight for weight in a.values()))
    norm_b = math.sqrt(sum(weight * weight for weight in b.values()))
    return numerator / (norm_a * norm_b)


def retrieve(
    question: str, corpus: Sequence[Passage], *, top_k: int = 3
) -> tuple[ScoredPassage, ...]:
    """Ranks `corpus` by TF-IDF cosine similarity to `question`, plus a bonus when the question
    names a passage's title (`source`) — see `_title_match`. Without it a short passage that merely
    repeats the question's words ("Conveyor Wall x 1. Has 1 Conveyor Belt connection.") outranks the
    one the question is about ("Conveyor Belt Mk.1. Transports up to 60 resources per minute.").
    Passages with zero term overlap are dropped rather than returned with a meaningless zero score,
    so callers can tell "nothing relevant" apart from "relevant but weakly worded"."""
    if not corpus:
        return ()

    corpus_tokens = [significant_words(passage.text) for passage in corpus]
    idf = _idf(corpus_tokens)
    question_tokens = significant_words(question)
    question_vector = _tfidf_vector(question_tokens, idf)
    question_words = set(question_tokens)

    relevant = []
    for passage, tokens in zip(corpus, corpus_tokens, strict=True):
        similarity = _cosine_similarity(question_vector, _tfidf_vector(tokens, idf))
        if similarity > 0:
            bonus = _TITLE_WEIGHT * _title_match(passage.source, question_words)
            relevant.append(ScoredPassage(passage=passage, score=similarity + bonus))
    relevant.sort(key=lambda sp: (-sp.score, sp.passage.passage_id))
    return tuple(relevant[:top_k])


def _title_match(title: str, question_words: set[str]) -> float:
    """The share of `title`'s words the question uses, counting a one-word title as half-named —
    sharing a single word ("Coal") is weaker evidence than naming "Coal-Powered Generator"."""
    title_words = set(significant_words(title))
    if not title_words:
        return 0.0
    return len(title_words & question_words) / max(len(title_words), 2)
