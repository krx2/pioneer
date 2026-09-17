"""Smoke tests against a real local model — the one run the rest of the suite fakes.

Opt-in: they need `PIONEER_LLM_BASE_URL` and `PIONEER_LLM_MODEL` pointing at a running model, and
`PIONEER_LIVE_LLM_TESTS=1`, since they're slow and depend on how capable that model is. What they
check is only what any usable model must manage: reaching for the right tool, and answering with
numbers the tools gave.
"""

import os

import pytest

from pioneer.app import DOCS_JSON, ask, build_context, load_knowledge_base, load_resource_nodes
from pioneer.config import settings
from pioneer.contracts import ResponseArtifact
from pioneer.orchestrator import verify_response

pytestmark = pytest.mark.skipif(
    os.environ.get("PIONEER_LIVE_LLM_TESTS") != "1"
    or not settings.llm_base_url
    or not settings.llm_model
    or not DOCS_JSON.exists(),
    reason="set PIONEER_LIVE_LLM_TESTS=1 and PIONEER_LLM_* to run against a real model",
)


@pytest.fixture(scope="module")
def context():
    return build_context(load_knowledge_base(), None, load_resource_nodes())


def test_a_production_goal_comes_back_as_a_verified_plan(context) -> None:
    result = ask("I want to produce 10 Iron Plates per minute. What do I build?", context)

    assert isinstance(result, ResponseArtifact), result
    assert result.chat
    assert result.graph is not None, "the model never called plan_production"
    score = verify_response(result, context)
    assert score.graph is not None and score.graph.balanced
    assert score.chat is not None
    assert score.chat.consistent, f"numbers not from any tool: {score.chat.ungrounded_numbers}"


def test_a_game_question_is_answered_from_the_knowledge_base(context) -> None:
    result = ask("How many items per minute does a Conveyor Belt Mk.1 carry?", context)

    assert isinstance(result, ResponseArtifact), result
    assert result.chat is not None and "60" in result.chat
