"""The Orchestrator's routing loop, against a scripted fake model: answers with and without tool
calls, tool calls written as text, failures that come back as results rather than exceptions, the
conversation so far, grounding, and what the model is told."""

import json
from typing import Any

from tests.orchestrator.support import (
    ITEMS,
    RECIPES,
    ask,
    picked,
    run_single_tool,
    scripted_tool_calling_llm,
    tool_call,
)

from pioneer.contracts import (
    Building,
    GameState,
    ResponseArtifact,
)
from pioneer.orchestrator.base import OrchestratorContext, OrchestratorUnavailable
from pioneer.orchestrator.orchestrator import recent_history
from pioneer.qa_engine import Passage

_QA_CORPUS = (
    Passage(
        passage_id="recipe_iron_ingot",
        text="The Smelter recipe for Iron Ingot converts 1 Iron Ore into 1 Iron Ingot.",
        source="Recipe: Iron Ingot",
    ),
)


def test_immediate_answer_with_no_tool_calls() -> None:
    llm = scripted_tool_calling_llm([{"content": "Hello, player!", "tool_calls": []}])

    result = ask(llm, "hi", OrchestratorContext(), response_id="resp-1")

    assert result.response_id == "resp-1"
    assert result.chat == "Hello, player!"
    assert result.graph is None
    assert result.map_locations is None


def test_answer_game_question_tool_reuses_qa_engine() -> None:
    llm = scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [
                    tool_call(
                        "call_1",
                        "answer_game_question",
                        {"question": "How do I make Iron Ingot?"},
                    )
                ],
            },
            {"content": "Smelt Iron Ore into Iron Ingot at a Smelter, 1:1.", "tool_calls": []},
        ]
    )

    result = ask(
        llm,
        "How do I make Iron Ingot?",
        OrchestratorContext(qa_corpus=_QA_CORPUS),
        qa_reply="Smelt Iron Ore into Iron Ingot, 1:1.",
        response_id="resp-5",
    )

    assert result.chat == "Smelt Iron Ore into Iron Ingot at a Smelter, 1:1."
    messages = llm.calls[1]["messages"]  # type: ignore[attr-defined]
    tool_result = next(m for m in messages if m["role"] == "tool")
    assert "Smelt Iron Ore into Iron Ingot, 1:1." in tool_result["content"]


def test_unreachable_llm_returns_orchestrator_unavailable() -> None:
    from pioneer.contracts import TransportError

    def failing_llm(base_url, model, messages, tools, api_key):
        raise TransportError("connection refused")

    result = ask(failing_llm, "hi", OrchestratorContext(), response_id="resp-7")

    assert isinstance(result, OrchestratorUnavailable)
    assert "connection refused" in result.reason


def test_runaway_tool_calling_returns_orchestrator_unavailable_instead_of_looping_forever() -> None:
    llm = scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [tool_call("call_1", "answer_game_question", {"question": "?"})],
            }
        ]
    )

    result = ask(
        llm,
        "hi",
        OrchestratorContext(),
        qa_reply="some answer",
        response_id="resp-8",
        max_tool_rounds=1,
    )

    assert isinstance(result, OrchestratorUnavailable)
    assert "1 tool-call round" in result.reason


def test_unknown_tool_name_reports_error_without_crashing() -> None:
    llm = scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [tool_call("call_1", "not_a_real_tool", {})],
            },
            {"content": "Sorry, I can't do that.", "tool_calls": []},
        ]
    )

    result = ask(llm, "do something weird", OrchestratorContext(), response_id="resp-9")

    assert result.chat == "Sorry, I can't do that."


def test_a_module_failing_mid_tool_is_reported_not_raised() -> None:
    """The Verifier raises on a building it wasn't given. That must reach the model as a tool
    error -- and the unverifiable plan must not be published -- rather than escape handle_query."""
    smelter_only = (
        Building(
            building_id="Build_SmelterMk1_C",
            name="Smelter",
            power_consumption_mw=4,
            input_slots=1,
            output_slots=1,
        ),
    )
    context = OrchestratorContext(recipes=RECIPES, buildings=smelter_only)

    result, tool_result = run_single_tool(
        context,
        "plan_production",
        {"target_item_id": "Desc_IronPlate_C", "target_rate_per_minute": 20},
    )

    assert isinstance(result, ResponseArtifact)
    assert "Build_ConstructorMk1_C" in tool_result["error"]
    assert result.graph is None


def test_the_model_is_told_what_data_it_has_and_has_not() -> None:
    llm = scripted_tool_calling_llm([{"content": "Hi!", "tool_calls": []}])
    context = OrchestratorContext(
        recipes=RECIPES,
        game_state=GameState(phase="Phase_2", tech_tier=6, session_name="stal mielec"),
    )

    ask(llm, "hi", context, response_id="resp")

    system = llm.calls[0]["messages"][0]["content"]  # type: ignore[attr-defined]
    assert "Knowledge base: 1 factory recipes." in system
    assert "no save loaded" in system
    assert "Resource node data: not loaded" in system
    assert "session stal mielec, game phase Phase_2, tech tier 6" in system


def test_the_answer_carries_its_question_and_named_grounding() -> None:
    context = OrchestratorContext(recipes=RECIPES, items=ITEMS)

    result, _ = run_single_tool(
        context, "plan_production", {"target_item_id": "Iron Plate", "target_rate_per_minute": 20}
    )

    assert result.question == "question"
    question, plan_result = result.grounding
    assert question == "question"
    assert json.loads(plan_result)["names"] == {
        "Recipe_IronPlate_C": "Iron Plate",
        "Desc_IronIngot_C": "Iron Ingot",
        "Desc_IronPlate_C": "Iron Plate",
    }


def test_retrieved_passages_are_part_of_the_grounding() -> None:
    result, _ = run_single_tool(
        OrchestratorContext(qa_corpus=_QA_CORPUS),
        "answer_game_question",
        {"question": "How do I make Iron Ingot?"},
    )

    assert _QA_CORPUS[0].text in result.grounding


def test_earlier_turns_come_before_the_question_and_ground_the_answer() -> None:
    llm = scripted_tool_calling_llm([{"content": "About 12 MW.", "tool_calls": []}])

    result = ask(
        llm,
        "and the power?",
        OrchestratorContext(),
        response_id="r",
        history=[("plan 20 plates", "Build 1 Constructor; it draws 12 MW.")],
    )

    messages = llm.calls[0]["messages"]  # type: ignore[attr-defined]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "plan 20 plates"
    assert messages[3]["content"] == "and the power?"
    assert result.grounding == (
        "and the power?",
        "plan 20 plates",
        "Build 1 Constructor; it draws 12 MW.",
    )


def test_only_the_newest_turns_that_fit_the_budget_are_passed_on() -> None:
    """A local model's context window is small, and what overflows it is cut from the front --
    the system prompt and the tools -- so a long conversation keeps only its newest turns."""
    history = [(f"question {i}", "x" * 5_000) for i in range(4)]
    llm = scripted_tool_calling_llm([{"content": "Fine.", "tool_calls": []}])

    result = ask(llm, "and now?", OrchestratorContext(), response_id="r", history=history)

    messages = llm.calls[0]["messages"]  # type: ignore[attr-defined]
    questions = [m["content"] for m in messages if m["role"] == "user"]
    assert questions == ["question 2", "question 3", "and now?"]
    assert "question 1" not in result.grounding


def test_an_answer_too_long_on_its_own_is_cut_short_rather_than_dropped() -> None:
    kept = recent_history([("earlier", "y" * 50)], budget=20)

    assert kept == [("earlier", "y" * 13 + " …")]
    assert recent_history([]) == []


def test_a_tool_call_the_model_wrote_as_text_is_executed_anyway() -> None:
    """Local models sometimes answer with the call instead of making it."""
    written = json.dumps(
        {
            "name": "plan_production",
            "arguments": {"target_item_id": "Iron Plate", "target_rate_per_minute": 20},
        }
    )
    llm = scripted_tool_calling_llm(
        [
            {"content": f"Let me check: {written}", "tool_calls": []},
            {"content": "You need 1 Constructor.", "tool_calls": []},
        ]
    )

    result = ask(
        llm,
        "20 plates a minute please",
        OrchestratorContext(recipes=RECIPES, items=ITEMS),
        response_id="r",
    )

    assert result.chat == "You need 1 Constructor."
    assert result.graph is not None
    assert result.graph.nodes[0].recipe_id == "Recipe_IronPlate_C"


def test_several_tool_calls_written_as_text_between_stray_tokens_all_run() -> None:
    """Qwen sometimes writes junk where its `<tool_call>` tag belongs -- then the backend doesn't
    recognize the calls, and they arrive as text, two JSON objects with the junk around them."""
    junk = "范冰春"  # what came out where <tool_call> belongs
    written = (
        f'{junk}\n{{"name": "plan_production", "arguments": '
        '{"target_item_id": "Iron Plate", "target_rate_per_minute": 20}}\n</tool_call>'
        f'{junk}\n{{"name": "find_item", "arguments": {{"query": "Iron"}}}}\n</tool_call>'
    )
    llm = scripted_tool_calling_llm(
        [
            {"content": written, "tool_calls": []},
            {"content": "You need 1 Constructor.", "tool_calls": []},
        ]
    )

    result = ask(
        llm,
        "20 plates a minute please",
        OrchestratorContext(recipes=RECIPES, items=ITEMS),
        response_id="r",
    )

    tool_messages = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]  # type: ignore[attr-defined]
    assert [m["name"] for m in tool_messages] == ["plan_production", "find_item"]
    assert result.chat == "You need 1 Constructor."
    assert result.graph is not None


def test_a_near_miss_tool_name_and_argument_keys_written_as_text_still_runs() -> None:
    """A model that gets the tool call itself right but drifts on the exact registered spelling --
    'plan_the_production' instead of `plan_production`, 'item_id'/'amount_per_minute' instead of
    `target_item_id`/`target_rate_per_minute` -- should still get its tool call executed rather
    than have the raw JSON leak into the player's answer with no graph attached."""
    written = json.dumps(
        {
            "name": "plan_the_production",
            "arguments": {"item_id": "Iron Plate", "amount_per_minute": 20},
        }
    )
    llm = scripted_tool_calling_llm(
        [
            {"content": f"Let me check: {written}", "tool_calls": []},
            {"content": "You need 1 Constructor.", "tool_calls": []},
        ]
    )

    result = ask(
        llm,
        "20 plates a minute please",
        OrchestratorContext(recipes=RECIPES, items=ITEMS),
        response_id="r",
    )

    assert result.chat == "You need 1 Constructor."
    assert result.graph is not None
    assert result.graph.nodes[0].recipe_id == "Recipe_IronPlate_C"


def test_an_answer_that_merely_contains_braces_stays_an_answer() -> None:
    llm = scripted_tool_calling_llm(
        [{"content": 'Set it to {"clock": 250} in the UI.', "tool_calls": []}]
    )

    result = ask(
        llm,
        "how do I overclock?",
        OrchestratorContext(recipes=RECIPES, items=ITEMS),
        response_id="r",
    )

    assert result.chat == 'Set it to {"clock": 250} in the UI.'
    assert result.graph is None


def test_progress_is_told_as_the_answer_is_worked_out() -> None:
    llm = scripted_tool_calling_llm(
        [
            {
                "content": '{"name": "plan_production"}',
                "tool_calls": [
                    tool_call(
                        "call_1",
                        "plan_productoin",  # a near-miss is described as the tool it runs
                        {"target_item_id": "Desc_IronPlate_C", "target_rate_per_minute": 20},
                    ),
                    tool_call("call_2", "factory_power", {}),
                ],
            },
            {"content": "done", "tool_calls": []},
        ]
    )
    events: list[dict[str, Any]] = []

    ask(
        llm,
        "20/min of plates",
        OrchestratorContext(recipes=RECIPES, items=ITEMS),
        response_id="resp",
        on_event=events.append,
    )

    assert events == [
        {"type": "status", "text": "Thinking"},
        {"type": "discard"},
        {"type": "tool", "name": "plan_production", "text": "Planning 20/min of Iron Plate"},
        {"type": "tool", "name": "factory_power", "text": "Checking your factory's power"},
        {"type": "status", "text": "Reading the results"},
    ]


def test_the_model_is_told_what_the_player_picked() -> None:
    llm = scripted_tool_calling_llm([{"content": "ok", "tool_calls": []}])

    ask(
        llm, "hi", picked("Recipe_IronPlate_C", "Recipe_Alternate_PureIronIngot_C"), response_id="r"
    )

    system = llm.calls[0]["messages"][0]["content"]  # type: ignore[attr-defined]
    assert (
        "Recipe choice: the player picked 2 of 3 recipes for plans -- turning off 1 they have "
        "unlocked and turning on 1 they haven't yet." in system
    )
    assert "missing_recipes" in system
