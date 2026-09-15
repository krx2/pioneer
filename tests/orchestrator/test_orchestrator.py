"""Tests for the Orchestrator's tool-calling routing loop, against a scripted fake `ToolCallingLLM`
and fake `ChatCompletion` -- no real networking, mirroring every other LLM-touching module's test
style (implementation.md Stage 16): known tool-call sequences must route to the right deterministic
module and land in the right `ResponseArtifact` field, and an unreachable/misbehaving LLM must
come back as a typed `OrchestratorUnavailable`, never a raised exception."""

from typing import Any

from pioneer.contracts import (
    Coordinates,
    ItemAmount,
    ProductionGraph,
    ProductionNode,
    Purity,
    Recipe,
    ResourceNode,
)
from pioneer.orchestrator.orchestrator import (
    OrchestratorContext,
    OrchestratorUnavailable,
    handle_query,
)
from pioneer.qa_engine import Passage

_BASE_URL = "http://localhost:11434/v1"
_MODEL = "test-model"

_RECIPES = (
    Recipe(
        recipe_id="Recipe_IronPlate_C",
        name="Iron Plate",
        building_ids=("Build_ConstructorMk1_C",),
        inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
        outputs=(ItemAmount(item_id="Desc_IronPlate_C", amount_per_minute=20),),
    ),
)

_QA_CORPUS = (
    Passage(
        passage_id="recipe_iron_ingot",
        text="The Smelter recipe for Iron Ingot converts 1 Iron Ore into 1 Iron Ingot.",
        source="Recipe: Iron Ingot",
    ),
)


def _scripted_tool_calling_llm(responses: list[dict[str, Any]]):
    calls = []

    def tool_calling_llm(
        base_url: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        api_key: str | None,
    ) -> dict[str, Any]:
        calls.append(
            {"base_url": base_url, "model": model, "messages": list(messages), "tools": tools}
        )
        return responses[len(calls) - 1]

    tool_calling_llm.calls = calls  # type: ignore[attr-defined]
    return tool_calling_llm


def _fake_qa_chat_completion(reply: str):
    def chat_completion(
        base_url: str, model: str, messages: list[dict[str, str]], api_key: str | None
    ) -> str:
        return reply

    return chat_completion


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"id": call_id, "name": name, "arguments": arguments}


def test_immediate_answer_with_no_tool_calls() -> None:
    llm = _scripted_tool_calling_llm([{"content": "Hello, player!", "tool_calls": []}])

    result = handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "hi",
        OrchestratorContext(),
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="resp-1",
    )

    assert result.response_id == "resp-1"
    assert result.chat == "Hello, player!"
    assert result.graph is None
    assert result.map_locations is None


def test_plan_production_tool_populates_graph() -> None:
    llm = _scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [
                    _tool_call(
                        "call_1",
                        "plan_production",
                        {"target_item_id": "Desc_IronPlate_C", "target_rate_per_minute": 20},
                    )
                ],
            },
            {"content": "You need 1 Constructor making Iron Plates.", "tool_calls": []},
        ]
    )

    result = handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "I want to produce 20/min of Iron Plate",
        OrchestratorContext(recipes=_RECIPES),
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="resp-2",
    )

    assert result.chat == "You need 1 Constructor making Iron Plates."
    assert result.graph is not None
    assert result.graph.nodes[0].recipe_id == "Recipe_IronPlate_C"
    assert result.graph.nodes[0].machine_count == 1

    # The second round's tool result message must carry the real machine count back to the model.
    second_round_messages = llm.calls[1]["messages"]  # type: ignore[attr-defined]
    tool_result = next(m for m in second_round_messages if m["role"] == "tool")
    assert '"Recipe_IronPlate_C": 1' in tool_result["content"]


def test_expand_existing_factory_without_save_reports_error_gracefully() -> None:
    llm = _scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [
                    _tool_call(
                        "call_1",
                        "expand_existing_factory",
                        {"target_item_id": "Desc_IronPlate_C", "target_rate_per_minute": 20},
                    )
                ],
            },
            {
                "content": "I don't have your save loaded, so I can't compute an expansion.",
                "tool_calls": [],
            },
        ]
    )

    result = handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "extend my factory to make 20/min Iron Plate",
        OrchestratorContext(recipes=_RECIPES),  # no existing_graph
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="resp-3",
    )

    assert result.graph is None
    assert "don't have your save loaded" in result.chat
    messages = llm.calls[1]["messages"]  # type: ignore[attr-defined]
    tool_result = next(m for m in messages if m["role"] == "tool")
    assert "error" in tool_result["content"]


def test_rank_build_locations_tool_populates_map_locations() -> None:
    nodes = (
        ResourceNode(
            node_id="node_far",
            item_id="Desc_OreIron_C",
            purity=Purity.NORMAL,
            position=Coordinates(x=10000, y=0, z=0),
        ),
        ResourceNode(
            node_id="node_near",
            item_id="Desc_OreIron_C",
            purity=Purity.PURE,
            position=Coordinates(x=100, y=0, z=0),
        ),
    )
    llm = _scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [
                    _tool_call("call_1", "rank_build_locations", {"item_id": "Desc_OreIron_C"})
                ],
            },
            {"content": "Build near node_near, it's pure and close.", "tool_calls": []},
        ]
    )

    result = handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "where should I mine iron ore?",
        OrchestratorContext(resource_nodes=nodes),
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="resp-4",
    )

    assert result.map_locations is not None
    assert result.map_locations[0].resource_node_id == "node_near"


def test_answer_game_question_tool_reuses_qa_engine() -> None:
    llm = _scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [
                    _tool_call(
                        "call_1",
                        "answer_game_question",
                        {"question": "How do I make Iron Ingot?"},
                    )
                ],
            },
            {"content": "Smelt Iron Ore into Iron Ingot at a Smelter, 1:1.", "tool_calls": []},
        ]
    )

    result = handle_query(
        llm,
        _fake_qa_chat_completion("Smelt Iron Ore into Iron Ingot, 1:1."),
        "How do I make Iron Ingot?",
        OrchestratorContext(qa_corpus=_QA_CORPUS),
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="resp-5",
    )

    assert result.chat == "Smelt Iron Ore into Iron Ingot at a Smelter, 1:1."
    messages = llm.calls[1]["messages"]  # type: ignore[attr-defined]
    tool_result = next(m for m in messages if m["role"] == "tool")
    assert "Smelt Iron Ore into Iron Ingot, 1:1." in tool_result["content"]


def test_diagnose_factory_problems_detects_a_real_deficit() -> None:
    graph = ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="n1",
                recipe_id="Recipe_IronPlate_C",
                building_id="Build_ConstructorMk1_C",
                machine_count=1,
            ),
        ),
        flows=(),
    )
    llm = _scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [_tool_call("call_1", "diagnose_factory_problems", {})],
            },
            {"content": "Your Iron Ingot supply is short.", "tool_calls": []},
        ]
    )

    handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "what's wrong with my factory?",
        OrchestratorContext(recipes=_RECIPES, existing_graph=graph),
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="resp-6",
    )

    messages = llm.calls[1]["messages"]  # type: ignore[attr-defined]
    tool_result = next(m for m in messages if m["role"] == "tool")
    assert "resource_deficit" in tool_result["content"]
    assert "Desc_IronIngot_C" in tool_result["content"]


def test_unreachable_llm_returns_orchestrator_unavailable() -> None:
    from pioneer.orchestrator.orchestrator import TransportError

    def failing_llm(base_url, model, messages, tools, api_key):
        raise TransportError("connection refused")

    result = handle_query(
        failing_llm,
        _fake_qa_chat_completion(""),
        "hi",
        OrchestratorContext(),
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="resp-7",
    )

    assert isinstance(result, OrchestratorUnavailable)
    assert "connection refused" in result.reason


def test_runaway_tool_calling_returns_orchestrator_unavailable_instead_of_looping_forever() -> None:
    llm = _scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [_tool_call("call_1", "answer_game_question", {"question": "?"})],
            }
        ]
    )

    result = handle_query(
        llm,
        _fake_qa_chat_completion("some answer"),
        "hi",
        OrchestratorContext(),
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="resp-8",
        max_tool_rounds=1,
    )

    assert isinstance(result, OrchestratorUnavailable)
    assert "1 tool-call round" in result.reason


def test_unknown_tool_name_reports_error_without_crashing() -> None:
    llm = _scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [_tool_call("call_1", "not_a_real_tool", {})],
            },
            {"content": "Sorry, I can't do that.", "tool_calls": []},
        ]
    )

    result = handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "do something weird",
        OrchestratorContext(),
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="resp-9",
    )

    assert result.chat == "Sorry, I can't do that."
