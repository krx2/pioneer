"""Tests for the LLM-as-a-judge hooks, against fake chat-completion callables — no networking."""

from pioneer.contracts import Coordinates, Purity, RankedLocation, TransportError
from pioneer.llm_client.judges import chat_judge, parse_verdict, terrain_judge
from pioneer.verification_feedback import JudgeVerdict


def _recording_chat(reply: str):
    calls = []

    def chat(base_url, model, messages, api_key):
        calls.append((base_url, model, messages, api_key))
        return reply

    chat.calls = calls  # type: ignore[attr-defined]
    return chat


def test_a_verdict_wrapped_in_prose_and_code_fences_is_found() -> None:
    reply = 'Sure!\n```json\n{"fits": false, "rationale": "Too far from the base."}\n```'

    assert parse_verdict(reply) == JudgeVerdict(
        fits_context=False, rationale="Too far from the base."
    )


def test_replies_without_a_usable_verdict_give_none() -> None:
    assert parse_verdict("I think it fits.") is None
    assert parse_verdict("{not json}") is None
    assert parse_verdict('{"fits": "yes"}') is None


def test_the_chat_judge_is_shown_the_question_answer_and_context() -> None:
    chat = _recording_chat('{"fits": true, "rationale": "Answers what was asked."}')
    judge = chat_judge("http://llm/v1", "judge-model", "key", chat=chat)

    verdict = judge("Where do I get screws?", "Build a Constructor.", "tech tier 2")

    assert verdict == JudgeVerdict(fits_context=True, rationale="Answers what was asked.")
    (base_url, model, messages, api_key) = chat.calls[0]  # type: ignore[attr-defined]
    assert (base_url, model, api_key) == ("http://llm/v1", "judge-model", "key")
    assert messages[0]["role"] == "system"
    for part in ("Where do I get screws?", "Build a Constructor.", "tech tier 2"):
        assert part in messages[1]["content"]


def test_the_terrain_judge_is_shown_the_site_in_metres() -> None:
    chat = _recording_chat('{"fits": true, "rationale": "Close enough."}')
    site = RankedLocation(
        resource_node_id="BP_ResourceNode103",
        position=Coordinates(x=1000, y=-2000, z=300),
        purity=Purity.PURE,
        distance_to_reference=250_000,
        score=0.1,
    )

    terrain_judge("http://llm/v1", "judge-model", chat=chat)(site, "base near the lake")

    prompt = chat.calls[0][2][1]["content"]  # type: ignore[attr-defined]
    for part in ("BP_ResourceNode103", "pure purity", "2500 m", "base near the lake"):
        assert part in prompt


def test_an_unreachable_judge_gives_no_verdict() -> None:
    def unreachable(base_url, model, messages, api_key):
        raise TransportError("connection refused")

    assert chat_judge("http://llm/v1", "m", chat=unreachable)("q", "a", "c") is None
