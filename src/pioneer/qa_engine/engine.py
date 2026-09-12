"""LLM answer-synthesis half of the Q&A Engine (implementation.md Stage 11).

Ties `retrieval.retrieve` to a chat-completion call against the local OpenAI-compatible endpoint
named by `pioneer.config.Settings` (`llm_base_url` + `llm_model`). Like `server_client`, this
module never imports an HTTP library itself — the caller injects a `ChatCompletion` transport, so
prompt-building and response-shaping stay testable against a fake model with zero real networking.
The prompt is scoped to rephrase retrieved passages only; retrieval never runs empty into the LLM
(`NoRelevantPassages`), matching architecture.md's graceful-degradation invariant.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pioneer.qa_engine.retrieval import Passage, ScoredPassage, retrieve

_SYSTEM_PROMPT = (
    "You answer Satisfactory game-mechanics questions using ONLY the numbered passages provided "
    "below. Rephrase and synthesize them into a direct answer; never add facts that aren't in the "
    "passages. If the passages don't contain the answer, say so explicitly."
)


@dataclass(frozen=True)
class Citation:
    passage_id: str
    source: str


@dataclass(frozen=True)
class QAAnswer:
    answer: str
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class NoRelevantPassages:
    """Typed "nothing in the corpus matched the question" result — returned instead of calling
    the LLM with nothing to ground it."""

    question: str


@dataclass(frozen=True)
class LLMUnavailable:
    reason: str


class TransportError(Exception):
    """Raised by a `ChatCompletion` implementation when the request couldn't complete at all
    (connection refused, timeout, ...) — distinct from the model successfully replying, which
    `answer_question` handles itself without needing this exception."""


class ChatCompletion(Protocol):
    """Sends `messages` (OpenAI chat/completions shape) to `base_url` for `model`, returning the
    assistant's reply text. Raises `TransportError` if the request couldn't complete at all."""

    def __call__(
        self,
        base_url: str,
        model: str,
        messages: list[dict[str, str]],
        api_key: str | None,
    ) -> str: ...


def answer_question(
    chat_completion: ChatCompletion,
    question: str,
    corpus: Sequence[Passage],
    *,
    llm_base_url: str,
    llm_model: str,
    llm_api_key: str | None = None,
    top_k: int = 3,
) -> QAAnswer | NoRelevantPassages | LLMUnavailable:
    retrieved = retrieve(question, corpus, top_k=top_k)
    if not retrieved:
        return NoRelevantPassages(question=question)

    messages = _build_messages(question, retrieved)
    try:
        reply = chat_completion(llm_base_url, llm_model, messages, llm_api_key)
    except TransportError as error:
        return LLMUnavailable(reason=f"could not reach LLM endpoint: {error}")

    citations = tuple(
        Citation(passage_id=scored.passage.passage_id, source=scored.passage.source)
        for scored in retrieved
    )
    return QAAnswer(answer=reply.strip(), citations=citations)


def _build_messages(question: str, retrieved: Sequence[ScoredPassage]) -> list[dict[str, str]]:
    context = "\n\n".join(
        f"[{scored.passage.passage_id}] ({scored.passage.source}): {scored.passage.text}"
        for scored in retrieved
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Passages:\n{context}\n\nQuestion: {question}"},
    ]
