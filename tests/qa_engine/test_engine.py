"""Tests for answer_question, against a fake `ChatCompletion` transport — no real networking, per
implementation.md Stage 11: known questions against a fixture corpus get answers traceable to
specific retrieved passages."""

from pioneer.qa_engine.engine import (
    LLMUnavailable,
    NoRelevantPassages,
    QAAnswer,
    TransportError,
    answer_question,
)
from pioneer.qa_engine.retrieval import Passage

_BASE_URL = "http://localhost:11434/v1"
_MODEL = "test-model"

_CORPUS = (
    Passage(
        passage_id="recipe_iron_ingot",
        text="The Smelter recipe for Iron Ingot converts 1 Iron Ore into 1 Iron Ingot.",
        source="Recipe: Iron Ingot",
    ),
    Passage(
        passage_id="wiki_power_shards",
        text="Power Shards, found in Power Slugs, can overclock a machine above 100% clock speed.",
        source="Wiki: Power Shards",
    ),
)


def _fake_transport(reply: str):
    calls = []

    def chat_completion(base_url: str, model: str, messages: list[dict[str, str]], api_key):
        calls.append((base_url, model, messages, api_key))
        return reply

    chat_completion.calls = calls  # type: ignore[attr-defined]
    return chat_completion


def test_successful_answer_cites_the_retrieved_passages() -> None:
    transport = _fake_transport("The Smelter turns Iron Ore into Iron Ingot, 1:1.")

    result = answer_question(
        transport, "How do I make Iron Ingot?", _CORPUS, llm_base_url=_BASE_URL, llm_model=_MODEL
    )

    assert isinstance(result, QAAnswer)
    assert result.answer == "The Smelter turns Iron Ore into Iron Ingot, 1:1."
    assert [c.passage_id for c in result.citations] == ["recipe_iron_ingot"]


def test_request_shape_passes_base_url_model_and_grounded_prompt() -> None:
    transport = _fake_transport("some answer")

    answer_question(
        transport,
        "How do I make Iron Ingot?",
        _CORPUS,
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        llm_api_key="secret",
    )

    (base_url, model, messages, api_key) = transport.calls[0]  # type: ignore[attr-defined]
    assert base_url == _BASE_URL
    assert model == _MODEL
    assert api_key == "secret"
    assert messages[0]["role"] == "system"
    assert "Iron Ore" in messages[1]["content"]


def test_question_with_no_relevant_passage_never_calls_the_llm() -> None:
    transport = _fake_transport("should not be called")

    result = answer_question(
        transport,
        "What is the capital of France?",
        _CORPUS,
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
    )

    assert result == NoRelevantPassages(question="What is the capital of France?")
    assert transport.calls == []  # type: ignore[attr-defined]


def test_transport_error_becomes_llm_unavailable() -> None:
    def failing_transport(base_url: str, model: str, messages: list[dict[str, str]], api_key):
        raise TransportError("connection refused")

    result = answer_question(
        failing_transport,
        "How do I make Iron Ingot?",
        _CORPUS,
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
    )

    assert isinstance(result, LLMUnavailable)
    assert "connection refused" in result.reason


def test_answer_question_never_raises_on_bad_transport() -> None:
    def flaky_transport(base_url: str, model: str, messages: list[dict[str, str]], api_key):
        raise TransportError("timed out")

    result = answer_question(
        flaky_transport,
        "How do I make Iron Ingot?",
        _CORPUS,
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
    )

    assert isinstance(result, LLMUnavailable)
