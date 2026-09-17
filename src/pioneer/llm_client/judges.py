"""LLM-as-a-judge hooks for the Stage 15 scoring (architecture.md §6): a second model call rating
whether the first one's answer fits — the player's question, progress and data for Chat, the
surroundings for a Map pin. Never arithmetic: the deterministic checks own every number.

Each factory returns a callable matching `verification_feedback.scoring`'s `ChatJudge` or
`TerrainJudge`, talking to the same local endpoint as everything else through `chat_completion`
(injectable, for tests). A judge that can't be reached, or answers with anything but the requested
JSON, yields `None` — no verdict — rather than an error: a verdict is advisory, and a flaky judge
mustn't cost the player their answer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from pioneer.contracts import RankedLocation, TransportError
from pioneer.llm_client.transport import chat_completion
from pioneer.verification_feedback.scoring import ChatJudge, JudgeVerdict, TerrainJudge

ChatCompletionFn = Callable[[str, str, list[dict[str, str]], str | None], str]

_VERDICT_FORMAT = (
    'Reply with only a JSON object: {"fits": true or false, "rationale": "<one sentence>"}.'
)
_CHAT_JUDGE_PROMPT = (
    "You review answers given by Pioneer, an assistant for the factory-building game "
    "Satisfactory. Judge only whether the answer suits the player's question and situation -- "
    "their game progress and the data the assistant had -- not whether its numbers are right: "
    "those are checked separately. " + _VERDICT_FORMAT
)
_TERRAIN_JUDGE_PROMPT = (
    "You review build sites suggested by Pioneer, an assistant for the factory-building game "
    "Satisfactory. Judge whether the suggested resource node is a sensible place to build next, "
    "given how far it is from the player's base and what they have built. " + _VERDICT_FORMAT
)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def chat_judge(
    base_url: str, model: str, api_key: str | None = None, *, chat: ChatCompletionFn | None = None
) -> ChatJudge:
    def judge(question: str, answer: str, context: str) -> JudgeVerdict | None:
        return _verdict(
            chat or chat_completion,
            base_url,
            model,
            api_key,
            _CHAT_JUDGE_PROMPT,
            f"Context:\n{context}\n\nPlayer's question:\n{question}\n\nAnswer given:\n{answer}",
        )

    return judge


def terrain_judge(
    base_url: str, model: str, api_key: str | None = None, *, chat: ChatCompletionFn | None = None
) -> TerrainJudge:
    def judge(location: RankedLocation, context: str) -> JudgeVerdict | None:
        position = location.position
        return _verdict(
            chat or chat_completion,
            base_url,
            model,
            api_key,
            _TERRAIN_JUDGE_PROMPT,
            f"Context:\n{context}\n\nSuggested site: resource node {location.resource_node_id}, "
            f"{location.purity.value} purity, at x={position.x:.0f} y={position.y:.0f} "
            f"z={position.z:.0f} (centimetres), "
            f"{location.distance_to_reference / 100:.0f} m from the player's base.",
        )

    return judge


def parse_verdict(reply: str) -> JudgeVerdict | None:
    """The `{"fits": ..., "rationale": ...}` object in `reply` — local models like to wrap JSON
    in prose or code fences — or `None` if there's no such object."""
    match = _JSON_OBJECT.search(reply)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("fits"), bool):
        return None
    return JudgeVerdict(fits_context=data["fits"], rationale=str(data.get("rationale", "")))


def _verdict(
    chat: ChatCompletionFn,
    base_url: str,
    model: str,
    api_key: str | None,
    system: str,
    user: str,
) -> JudgeVerdict | None:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        reply = chat(base_url, model, messages, api_key)
    except TransportError:
        return None
    return parse_verdict(reply)
