"""Tests for the Orchestrator's tool-calling routing loop, against a scripted fake `ToolCallingLLM`
and fake `ChatCompletion` -- no real networking, mirroring every other LLM-touching module's test
style (implementation.md Stage 16): known tool-call sequences must route to the right deterministic
module and land in the right `ResponseArtifact` field, and an unreachable/misbehaving LLM must
come back as a typed `OrchestratorUnavailable`, never a raised exception."""

import json
from dataclasses import replace
from typing import Any

from pioneer.contracts import (
    Building,
    Coordinates,
    FactorySite,
    GameState,
    GeneratorFuel,
    Item,
    ItemAmount,
    ItemCount,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
    Purity,
    Recipe,
    ResourceNode,
    ResponseArtifact,
    Technology,
    TransportTier,
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
_IRON_INGOT = Recipe(
    recipe_id="Recipe_IngotIron_C",
    name="Iron Ingot",
    building_ids=("Build_SmelterMk1_C",),
    inputs=(ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
)
_PURE_IRON_INGOT = Recipe(
    recipe_id="Recipe_Alternate_PureIronIngot_C",
    name="Alternate: Pure Iron Ingot",
    building_ids=("Build_OilRefinery_C",),
    inputs=(
        ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=35),
        ItemAmount(item_id="Desc_Water_C", amount_per_minute=20),
    ),
    outputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=65),),
    is_alternate=True,
)
_ORE_FROM_LIMESTONE = Recipe(
    recipe_id="Recipe_Iron_Limestone_C",
    name="Iron Ore (Limestone)",
    building_ids=("Build_Converter_C",),
    inputs=(ItemAmount(item_id="Desc_Stone_C", amount_per_minute=120),),
    outputs=(ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=60),),
)
_ITEMS = (
    Item(item_id="Desc_IronPlate_C", name="Iron Plate"),
    Item(item_id="Desc_IronIngot_C", name="Iron Ingot"),
    Item(item_id="Desc_OreIron_C", name="Iron Ore", is_raw_resource=True),
    Item(item_id="Desc_Stone_C", name="Limestone", is_raw_resource=True),
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


def _run_single_tool(
    context: OrchestratorContext, name: str, arguments: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    """One turn where the model calls `name` once, then answers "done". Returns the
    `handle_query` result and the tool's parsed result as the model saw it."""
    llm = _scripted_tool_calling_llm(
        [
            {"content": None, "tool_calls": [_tool_call("call_1", name, arguments)]},
            {"content": "done", "tool_calls": []},
        ]
    )
    result = handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "question",
        context,
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="resp",
    )
    messages = llm.calls[1]["messages"]  # type: ignore[attr-defined]
    tool_message = next(m for m in messages if m["role"] == "tool")
    return result, json.loads(tool_message["content"])


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
    assert json.loads(tool_result["content"])["stages"] == [
        {
            "recipe_id": "Recipe_IronPlate_C",
            "building_id": "Build_ConstructorMk1_C",
            "machines": 1,
        }
    ]


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
        OrchestratorContext(recipes=_RECIPES + (_IRON_INGOT,), existing_graph=graph),
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
    context = OrchestratorContext(recipes=_RECIPES, buildings=smelter_only)

    result, tool_result = _run_single_tool(
        context,
        "plan_production",
        {"target_item_id": "Desc_IronPlate_C", "target_rate_per_minute": 20},
    )

    assert isinstance(result, ResponseArtifact)
    assert "Build_ConstructorMk1_C" in tool_result["error"]
    assert result.graph is None


def test_item_names_resolve_and_planning_stops_at_raw_resources() -> None:
    context = OrchestratorContext(
        recipes=_RECIPES + (_ORE_FROM_LIMESTONE, _IRON_INGOT), items=_ITEMS
    )

    result, tool_result = _run_single_tool(
        context, "plan_production", {"target_item_id": "iron plate", "target_rate_per_minute": 20}
    )

    assert "error" not in tool_result
    assert {n.recipe_id for n in result.graph.nodes} == {
        "Recipe_IronPlate_C",
        "Recipe_IngotIron_C",
    }


def test_a_name_several_items_fit_is_reported_with_them() -> None:
    context = OrchestratorContext(recipes=_RECIPES, items=_ITEMS)

    result, tool_result = _run_single_tool(
        context, "plan_production", {"target_item_id": "Iron", "target_rate_per_minute": 20}
    )

    assert result.graph is None
    assert "'Iron' could be several items" in tool_result["error"]
    assert {match["item_id"] for match in tool_result["did_you_mean"]} == {
        "Desc_OreIron_C",
        "Desc_IronIngot_C",
        "Desc_IronPlate_C",
    }


def test_an_unknown_item_is_reported_as_such() -> None:
    context = OrchestratorContext(recipes=_RECIPES, items=_ITEMS)

    _, tool_result = _run_single_tool(
        context, "plan_production", {"target_item_id": "Uranium", "target_rate_per_minute": 20}
    )

    assert tool_result == {"error": "unknown item 'Uranium'", "did_you_mean": []}


def test_a_partial_or_plural_name_one_item_fits_is_taken() -> None:
    context = OrchestratorContext(recipes=_RECIPES, items=_ITEMS)

    for name in ("Iron Plat", "iron plates"):
        result, tool_result = _run_single_tool(
            context, "plan_production", {"target_item_id": name, "target_rate_per_minute": 20}
        )
        assert "error" not in tool_result, name
        assert result.graph.nodes[0].recipe_id == "Recipe_IronPlate_C"


def test_plan_production_honours_recipe_choices_given_by_item_name() -> None:
    context = OrchestratorContext(recipes=_RECIPES + (_IRON_INGOT, _PURE_IRON_INGOT), items=_ITEMS)

    result, _ = _run_single_tool(
        context,
        "plan_production",
        {
            "target_item_id": "Iron Plate",
            "target_rate_per_minute": 20,
            "recipe_choices": {"Iron Ingot": "Recipe_Alternate_PureIronIngot_C"},
        },
    )

    assert "Recipe_Alternate_PureIronIngot_C" in {n.recipe_id for n in result.graph.nodes}


def test_find_item_tool_lists_matching_items() -> None:
    _, tool_result = _run_single_tool(
        OrchestratorContext(items=_ITEMS), "find_item", {"query": "iron"}
    )

    item_ids = [item["item_id"] for item in tool_result["items"]]
    assert item_ids[0] == "Desc_OreIron_C"  # shortest name first among equally good matches
    assert set(item_ids) == {"Desc_OreIron_C", "Desc_IronPlate_C", "Desc_IronIngot_C"}


def test_find_item_without_a_knowledge_base_says_so() -> None:
    _, tool_result = _run_single_tool(OrchestratorContext(), "find_item", {"query": "iron"})
    assert "no knowledge base" in tool_result["error"]


def test_list_recipes_for_item_flags_alternates() -> None:
    context = OrchestratorContext(recipes=(_IRON_INGOT, _PURE_IRON_INGOT), items=_ITEMS)

    _, tool_result = _run_single_tool(context, "list_recipes_for_item", {"item": "Iron Ingot"})

    assert [(r["recipe_id"], r["alternate"]) for r in tool_result["recipes"]] == [
        ("Recipe_IngotIron_C", False),
        ("Recipe_Alternate_PureIronIngot_C", True),
    ]
    assert tool_result["recipes"][1]["inputs_per_machine_per_minute"] == {
        "Desc_OreIron_C": 35,
        "Desc_Water_C": 20,
    }


def _existing_iron(*, smelters: float, constructors: float) -> ProductionGraph:
    return ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="save_Recipe_IngotIron_C",
                recipe_id="Recipe_IngotIron_C",
                building_id="Build_SmelterMk1_C",
                machine_count=smelters,
                is_existing=True,
            ),
            ProductionNode(
                node_id="save_Recipe_IronPlate_C",
                recipe_id="Recipe_IronPlate_C",
                building_id="Build_ConstructorMk1_C",
                machine_count=constructors,
                is_existing=True,
            ),
        ),
        flows=(),
    )


def test_expansion_draws_on_the_existing_factorys_surplus_first() -> None:
    """2 smelters make 60 ingot/min and 1 constructor eats 30 of them making 20 plates: 30 ingots
    and 20 plates spare. 40 more plates/min then takes just one more constructor -- fed by the
    spare ingots, so no new smelter."""
    context = OrchestratorContext(
        recipes=_RECIPES + (_IRON_INGOT,),
        items=_ITEMS,
        existing_graph=_existing_iron(smelters=2, constructors=1),
    )

    result, tool_result = _run_single_tool(
        context,
        "expand_existing_factory",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 40},
    )

    assert tool_result["changes"] == [
        {
            "action": "extend",
            "recipe_id": "Recipe_IronPlate_C",
            "building_id": "Build_ConstructorMk1_C",
            "additional_machine_count": 1,
            "target_node_id": "save_Recipe_IronPlate_C",
        }
    ]
    assert "added_power_draw_mw" not in tool_result  # no building data to tell
    assert tool_result["drawn_from_existing_surplus_per_minute"] == {
        "Desc_IronPlate_C": 20,
        "Desc_IronIngot_C": 30,
    }
    assert tool_result["existing_shortfalls_per_minute"] == {}
    (extended,) = result.graph.nodes
    assert extended.machine_count == 2  # 1 existing + 1 new


def test_an_expansion_the_surplus_covers_publishes_no_graph() -> None:
    """20 plates/min spare: 10 more builds nothing, so there's no graph to show."""
    context = OrchestratorContext(
        recipes=_RECIPES + (_IRON_INGOT,),
        items=_ITEMS,
        existing_graph=_existing_iron(smelters=2, constructors=1),
    )

    result, tool_result = _run_single_tool(
        context,
        "expand_existing_factory",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 10},
    )

    assert tool_result["changes"] == []
    assert result.graph is None


def test_a_rate_that_is_not_positive_is_refused() -> None:
    context = OrchestratorContext(recipes=_RECIPES + (_IRON_INGOT,), items=_ITEMS)

    for rate in (0, -5):
        result, tool_result = _run_single_tool(
            context,
            "plan_production",
            {"target_item_id": "Iron Plate", "target_rate_per_minute": rate},
        )
        assert "positive" in tool_result["error"]
        assert result.graph is None


_IRON_BUILDINGS = (
    Building(
        building_id="Build_SmelterMk1_C",
        name="Smelter",
        power_consumption_mw=4,
        input_slots=1,
        output_slots=1,
    ),
    Building(
        building_id="Build_ConstructorMk1_C",
        name="Constructor",
        power_consumption_mw=4,
        input_slots=1,
        output_slots=1,
    ),
    Building(
        building_id="Build_GeneratorBiomass_C",
        name="Biomass Burner",
        power_consumption_mw=-30,
        input_slots=1,
        output_slots=0,
    ),
)


def test_expansion_names_the_buildings_their_power_and_the_grids_spare() -> None:
    """1 smelter under 2 constructors on one 30 MW burner: 12 MW drawn, 18 spare. 60 more plates
    a minute takes a constructor (4 MW) and a smelter (4 MW)."""
    context = OrchestratorContext(
        recipes=_RECIPES + (_IRON_INGOT,),
        buildings=_IRON_BUILDINGS,
        items=_ITEMS,
        existing_graph=_existing_iron(smelters=1, constructors=2),
        existing_placements=(
            _placed("Build_SmelterMk1_C", recipe_id="Recipe_IngotIron_C"),
            _placed("Build_ConstructorMk1_C", recipe_id="Recipe_IronPlate_C"),
            _placed("Build_ConstructorMk1_C", recipe_id="Recipe_IronPlate_C"),
            _placed("Build_GeneratorBiomass_C"),
        ),
    )

    _, tool_result = _run_single_tool(
        context,
        "expand_existing_factory",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 60},
    )

    assert {
        change["recipe_id"]: (change["building_id"], change["power_mw"])
        for change in tool_result["changes"]
    } == {
        "Recipe_IronPlate_C": ("Build_ConstructorMk1_C", 4),
        "Recipe_IngotIron_C": ("Build_SmelterMk1_C", 4),
    }
    assert tool_result["added_power_draw_mw"] == 8
    assert tool_result["grid_spare_power_mw"] == 18
    assert tool_result["target_item_id"] == "Desc_IronPlate_C"
    assert tool_result["names"]["Build_ConstructorMk1_C"] == "Constructor"


def test_a_plan_names_each_stages_building_and_power() -> None:
    context = OrchestratorContext(
        recipes=_RECIPES + (_IRON_INGOT,), buildings=_IRON_BUILDINGS, items=_ITEMS
    )

    _, tool_result = _run_single_tool(
        context,
        "plan_production",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 40},
    )

    assert tool_result["stages"] == [
        {
            "recipe_id": "Recipe_IronPlate_C",
            "building_id": "Build_ConstructorMk1_C",
            "machines": 2,
            "power_mw": 8,
        },
        {
            "recipe_id": "Recipe_IngotIron_C",
            "building_id": "Build_SmelterMk1_C",
            "machines": 2,
            "power_mw": 8,
        },
    ]
    assert tool_result["net_power_draw_mw"] == 16
    assert "grid_spare_power_mw" not in tool_result  # no save: nothing to tell


def test_expansion_reports_shortfalls_the_existing_factory_already_has() -> None:
    """1 smelter (30 ingot/min) under 2 constructors (60/min): already 30 ingot/min short."""
    context = OrchestratorContext(
        recipes=_RECIPES + (_IRON_INGOT,),
        items=_ITEMS,
        existing_graph=_existing_iron(smelters=1, constructors=2),
    )

    _, tool_result = _run_single_tool(
        context,
        "expand_existing_factory",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 60},
    )

    assert tool_result["existing_shortfalls_per_minute"] == {"Desc_IronIngot_C": 30}
    assert {c["recipe_id"]: c["additional_machine_count"] for c in tool_result["changes"]} == {
        "Recipe_IronPlate_C": 1,
        "Recipe_IngotIron_C": 1,
    }


_REFINERY_FUEL = Recipe(
    recipe_id="Recipe_LiquidFuel_C",
    name="Fuel",
    building_ids=("Build_OilRefinery_C",),
    inputs=(ItemAmount(item_id="Desc_LiquidOil_C", amount_per_minute=60),),
    outputs=(
        ItemAmount(item_id="Desc_LiquidFuel_C", amount_per_minute=40),
        ItemAmount(item_id="Desc_PolymerResin_C", amount_per_minute=30),
    ),
)
_POWER_BUILDINGS = (
    Building(
        building_id="Build_OilRefinery_C",
        name="Refinery",
        power_consumption_mw=30,
        input_slots=1,
        output_slots=2,
    ),
    Building(
        building_id="Build_GeneratorFuel_C",
        name="Fuel Generator",
        power_consumption_mw=-250,
        input_slots=1,
        output_slots=0,
    ),
)
_FUEL_ITEMS = (
    Item(item_id="Desc_LiquidOil_C", name="Crude Oil", is_fluid=True, is_raw_resource=True),
    Item(item_id="Desc_LiquidFuel_C", name="Fuel", is_fluid=True, energy_value_mj=750),
)


def _placed(building_id: str, **fields: Any) -> PlacementRecord:
    return PlacementRecord(building_id=building_id, position=Coordinates(x=0, y=0), **fields)


def test_diagnosis_counts_generators_burning_the_factorys_own_fuel() -> None:
    """One refinery makes 40 m³/min of Fuel and two Fuel Generators burn exactly that: nothing is
    overproduced, crude oil is a raw input, and 500 MW of generation covers 30 MW of draw."""
    refinery_node = ProductionNode(
        node_id="save_Recipe_LiquidFuel_C",
        recipe_id="Recipe_LiquidFuel_C",
        building_id="Build_OilRefinery_C",
        machine_count=1,
        is_existing=True,
    )
    context = OrchestratorContext(
        recipes=(_REFINERY_FUEL,),
        buildings=_POWER_BUILDINGS,
        items=_FUEL_ITEMS,
        existing_graph=ProductionGraph(nodes=(refinery_node,), flows=()),
        existing_placements=(
            _placed("Build_OilRefinery_C", recipe_id="Recipe_LiquidFuel_C"),
            _placed("Build_GeneratorFuel_C", fuel_item_id="Desc_LiquidFuel_C"),
            _placed("Build_GeneratorFuel_C", fuel_item_id="Desc_LiquidFuel_C"),
        ),
    )

    _, tool_result = _run_single_tool(context, "diagnose_factory_problems", {})

    assert tool_result["power_draw_mw"] == 30
    assert tool_result["power_capacity_mw"] == 500
    flagged = {anomaly["item_id"] for anomaly in tool_result["anomalies"]}
    assert "Desc_LiquidFuel_C" not in flagged
    assert "Desc_LiquidOil_C" not in flagged


_COAL_POWER = (
    Building(
        building_id="Build_GeneratorCoal_C",
        name="Coal-Powered Generator",
        power_consumption_mw=-75,
        input_slots=1,
        output_slots=0,
        fuels=(GeneratorFuel(fuel_item_id="Desc_Coal_C", supplemental_item_id="Desc_Water_C"),),
        supplemental_per_minute_per_mw=0.6,
    ),
    Building(
        building_id="Build_WaterPump_C",
        name="Water Extractor",
        power_consumption_mw=20,
        input_slots=0,
        output_slots=1,
        extraction_rate_per_minute=120,
        fixed_resource_id="Desc_Water_C",
    ),
)
_COAL_ITEMS = (
    Item(item_id="Desc_Coal_C", name="Coal", is_raw_resource=True, energy_value_mj=300),
    Item(item_id="Desc_Water_C", name="Water", is_fluid=True, is_raw_resource=True),
)


def test_diagnosis_counts_the_water_coal_generators_drink() -> None:
    """Two coal generators take 90 m³ of water a minute, which one extractor at 75% supplies
    exactly -- the water isn't overproduced, as it would be if only fuel were counted."""
    context = OrchestratorContext(
        buildings=_COAL_POWER,
        items=_COAL_ITEMS,
        existing_graph=ProductionGraph(nodes=(), flows=()),
        existing_placements=(
            _placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C"),
            _placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C"),
            _placed("Build_WaterPump_C", clock_speed=0.75),
        ),
    )

    _, tool_result = _run_single_tool(context, "diagnose_factory_problems", {})

    assert "Desc_Water_C" not in {anomaly["item_id"] for anomaly in tool_result["anomalies"]}


def test_diagnosis_rates_a_water_shortfall_against_what_the_generators_drink() -> None:
    """Three coal generators need 135 m³/min, one extractor gives 120: 15 short of 135 is minor,
    and no single machine is to blame. (Raw supply is only judged with node data loaded.)"""
    context = OrchestratorContext(
        buildings=_COAL_POWER,
        items=_COAL_ITEMS,
        resource_nodes=_NODES,
        existing_graph=ProductionGraph(nodes=(), flows=()),
        existing_placements=(
            *(_placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C") for _ in range(3)),
            _placed("Build_WaterPump_C"),
        ),
    )

    _, tool_result = _run_single_tool(context, "diagnose_factory_problems", {})

    (water,) = [a for a in tool_result["anomalies"] if a["item_id"] == "Desc_Water_C"]
    assert water["kind"] == "resource_deficit"
    assert water["severity"] == "low"
    assert water["node_id"] is None


def test_diagnosis_flags_a_blackout_judged_from_the_placed_buildings() -> None:
    context = OrchestratorContext(
        recipes=(_REFINERY_FUEL,),
        buildings=_POWER_BUILDINGS,
        items=_FUEL_ITEMS,
        existing_graph=ProductionGraph(nodes=(), flows=()),
        existing_placements=tuple(_placed("Build_OilRefinery_C") for _ in range(10)),
    )

    _, tool_result = _run_single_tool(context, "diagnose_factory_problems", {})

    assert tool_result["power_capacity_mw"] == 0
    assert [anomaly["kind"] for anomaly in tool_result["anomalies"]] == ["power_blackout"]


def test_diagnosis_treats_items_no_recipe_makes_as_gathered_not_short() -> None:
    """Leaves come from the player's chainsaw, not from any factory -- never a "deficit"."""
    biomass = Recipe(
        recipe_id="Recipe_Biomass_Leaves_C",
        name="Biomass (Leaves)",
        building_ids=("Build_ConstructorMk1_C",),
        inputs=(ItemAmount(item_id="Desc_Leaves_C", amount_per_minute=120),),
        outputs=(ItemAmount(item_id="Desc_GenericBiomass_C", amount_per_minute=60),),
    )
    graph = ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="save_Recipe_Biomass_Leaves_C",
                recipe_id="Recipe_Biomass_Leaves_C",
                building_id="Build_ConstructorMk1_C",
                machine_count=1,
                is_existing=True,
            ),
        ),
        flows=(),
    )

    _, tool_result = _run_single_tool(
        OrchestratorContext(recipes=(biomass,), existing_graph=graph),
        "diagnose_factory_problems",
        {},
    )

    assert "Desc_Leaves_C" not in {anomaly["item_id"] for anomaly in tool_result["anomalies"]}


_NODES = (
    ResourceNode(
        node_id="Persistent_Level:PersistentLevel.BP_ResourceNode1",
        item_id="Desc_OreIron_C",
        purity=Purity.PURE,
        position=Coordinates(x=1000, y=0),
    ),
    ResourceNode(
        node_id="Persistent_Level:PersistentLevel.BP_ResourceNode2",
        item_id="Desc_OreIron_C",
        purity=Purity.NORMAL,
        position=Coordinates(x=90_000, y=0),
    ),
    ResourceNode(
        node_id="Persistent_Level:PersistentLevel.BP_ResourceNodeGeyser1",
        item_id="Desc_Geyser_C",
        purity=Purity.NORMAL,
        position=Coordinates(x=0, y=5000),
    ),
)
_MINER = Building(
    building_id="Build_MinerMk1_C",
    name="Miner Mk.1",
    power_consumption_mw=5,
    input_slots=0,
    output_slots=1,
    extraction_rate_per_minute=60,
)


def test_rank_build_locations_without_node_data_says_so() -> None:
    _, tool_result = _run_single_tool(
        OrchestratorContext(items=_ITEMS), "rank_build_locations", {"item_id": "Iron Ore"}
    )

    assert "no resource node data" in tool_result["error"]


def test_locations_are_measured_from_the_players_base_by_default() -> None:
    """The base sits around x=90,000: the normal node right there beats the pure one far away."""
    base = tuple(
        PlacementRecord(building_id="Build_ConstructorMk1_C", position=Coordinates(x=x, y=0))
        for x in (80_000, 100_000)
    )
    context = OrchestratorContext(items=_ITEMS, resource_nodes=_NODES, existing_placements=base)

    result, tool_result = _run_single_tool(context, "rank_build_locations", {"item_id": "Iron Ore"})

    assert tool_result["reference"] == {"x": 90_000, "y": 0, "z": 0}
    assert result.map_locations[0].resource_node_id.endswith("BP_ResourceNode2")


def test_the_answer_keeps_the_point_its_distances_are_measured_from() -> None:
    context = OrchestratorContext(items=_ITEMS, resource_nodes=_NODES)

    result, _ = _run_single_tool(
        context, "rank_build_locations", {"item_id": "Iron Ore", "reference_x": 500}
    )

    assert result.map_reference == Coordinates(x=500, y=0, z=0)


def test_a_count_below_one_is_refused() -> None:
    context = OrchestratorContext(items=_ITEMS, resource_nodes=_NODES)

    result, tool_result = _run_single_tool(
        context, "rank_build_locations", {"item_id": "Iron Ore", "count": 0}
    )

    assert "count must be at least 1" in tool_result["error"]
    assert result.map_locations is None


def test_geysers_can_be_asked_for_by_name() -> None:
    context = OrchestratorContext(items=_ITEMS, resource_nodes=_NODES)

    result, _ = _run_single_tool(context, "rank_build_locations", {"item_id": "geyser"})

    assert [location.resource_node_id for location in result.map_locations] == [
        "Persistent_Level:PersistentLevel.BP_ResourceNodeGeyser1"
    ]


def test_with_node_data_diagnosis_judges_ore_supply_too() -> None:
    """One Mk.1 miner on a pure node (120 ore/min) under 5 smelters (150/min): 30/min short."""
    smelters = ProductionNode(
        node_id="save_Recipe_IngotIron_C",
        recipe_id="Recipe_IngotIron_C",
        building_id="Build_SmelterMk1_C",
        machine_count=5,
        is_existing=True,
    )
    context = OrchestratorContext(
        recipes=(_IRON_INGOT,),
        buildings=(_MINER,),
        items=_ITEMS,
        resource_nodes=_NODES,
        existing_graph=ProductionGraph(nodes=(smelters,), flows=()),
        existing_placements=(_placed("Build_MinerMk1_C", resource_node_id=_NODES[0].node_id),),
    )

    _, tool_result = _run_single_tool(context, "diagnose_factory_problems", {})

    ore = [a for a in tool_result["anomalies"] if a["item_id"] == "Desc_OreIron_C"]
    assert [anomaly["kind"] for anomaly in ore] == ["resource_deficit"]
    assert "short by 30/min" in ore[0]["description"]
    assert ore[0]["severity"] == "low"  # 30 of the 150 the smelters eat
    assert ore[0]["node_id"] == "save_Recipe_IngotIron_C"


def test_expansion_reports_raw_resources_needed_against_spare_extraction() -> None:
    """A Mk.1 miner on a pure node feeds nothing yet: 120 ore/min spare. 20 plates/min takes one
    new smelter eating 30 of it."""
    context = OrchestratorContext(
        recipes=_RECIPES + (_IRON_INGOT,),
        buildings=(_MINER,),
        items=_ITEMS,
        resource_nodes=_NODES,
        existing_graph=_existing_iron(smelters=0, constructors=0),
        existing_placements=(_placed("Build_MinerMk1_C", resource_node_id=_NODES[0].node_id),),
    )

    _, tool_result = _run_single_tool(
        context,
        "expand_existing_factory",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 20},
    )

    assert tool_result["raw_resources_needed_per_minute"] == {"Desc_OreIron_C": 30}
    assert tool_result["spare_extraction_per_minute"] == {"Desc_OreIron_C": 120}


def test_the_model_is_told_what_data_it_has_and_has_not() -> None:
    llm = _scripted_tool_calling_llm([{"content": "Hi!", "tool_calls": []}])
    context = OrchestratorContext(
        recipes=_RECIPES,
        game_state=GameState(phase="Phase_2", tech_tier=6, session_name="stal mielec"),
    )

    handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "hi",
        context,
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="resp",
    )

    system = llm.calls[0]["messages"][0]["content"]  # type: ignore[attr-defined]
    assert "Knowledge base: 1 factory recipes." in system
    assert "no save loaded" in system
    assert "Resource node data: not loaded" in system
    assert "session stal mielec, game phase Phase_2, tech tier 6" in system


def test_the_answer_carries_its_question_and_named_grounding() -> None:
    context = OrchestratorContext(recipes=_RECIPES, items=_ITEMS)

    result, _ = _run_single_tool(
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
    result, _ = _run_single_tool(
        OrchestratorContext(qa_corpus=_QA_CORPUS),
        "answer_game_question",
        {"question": "How do I make Iron Ingot?"},
    )

    assert _QA_CORPUS[0].text in result.grounding


# --- Unlocks, recipe comparison, power and transport -------------------------------------------

_START = Technology(technology_id="Schematic_Start_C", name="Start", tier=0, kind="custom")
_PLATES = Technology(
    technology_id="Schematic_1-1_C",
    name="Plates",
    tier=1,
    kind="milestone",
    prerequisites=("Schematic_Start_C",),
    cost=(ItemCount(item_id="Desc_IronIngot_C", amount=50),),
)
_PURE_DRIVE = Technology(
    technology_id="Schematic_Alternate_PureIronIngot_C",
    name="Alternate: Pure Iron Ingot",
    tier=0,
    kind="alternate",
    prerequisites=("Schematic_1-1_C",),
)
_GATED_RECIPES = (
    replace(_RECIPES[0], unlockable_by=("Schematic_1-1_C",)),
    replace(_IRON_INGOT, unlockable_by=("Schematic_Start_C",)),
    replace(_PURE_IRON_INGOT, unlockable_by=("Schematic_Alternate_PureIronIngot_C",)),
)
_WATER_ITEMS = (*_ITEMS, Item(item_id="Desc_Water_C", name="Water", is_raw_resource=True))


def _gated(unlocked: set[str] | None, **fields: Any) -> OrchestratorContext:
    return OrchestratorContext(
        recipes=_GATED_RECIPES,
        items=_WATER_ITEMS,
        technologies=(_START, _PLATES, _PURE_DRIVE),
        unlocked_technology_ids=None if unlocked is None else frozenset(unlocked),
        **fields,
    )


def test_a_plan_the_unlocked_recipes_cannot_make_says_what_to_unlock() -> None:
    result, tool_result = _run_single_tool(
        _gated({"Schematic_Start_C"}),
        "plan_production",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 20},
    )

    assert result.graph is not None
    assert tool_result["needs_unlocking"] == [
        {
            "recipe_id": "Recipe_IronPlate_C",
            "unlock_with": "Schematic_1-1_C",
            "tier": 1,
            "kind": "milestone",
        }
    ]
    assert tool_result["names"]["Schematic_1-1_C"] == "Plates"


def test_a_plan_uses_an_unlocked_alternate_over_a_locked_standard_recipe() -> None:
    context = _gated({"Schematic_1-1_C", "Schematic_Alternate_PureIronIngot_C"})

    result, tool_result = _run_single_tool(
        context, "plan_production", {"target_item_id": "Iron Plate", "target_rate_per_minute": 20}
    )

    assert "Recipe_Alternate_PureIronIngot_C" in {n.recipe_id for n in result.graph.nodes}
    assert "needs_unlocking" not in tool_result


def test_with_unlocks_unknown_every_recipe_is_fair_game() -> None:
    _, tool_result = _run_single_tool(
        _gated(None),
        "plan_production",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 20},
    )

    assert "needs_unlocking" not in tool_result


def test_listed_recipes_say_whether_they_are_unlocked() -> None:
    _, tool_result = _run_single_tool(
        _gated({"Schematic_Start_C"}), "list_recipes_for_item", {"item": "Iron Ingot"}
    )

    assert {r["recipe_id"]: (r["unlocked"], r["unlocked_by"]) for r in tool_result["recipes"]} == {
        "Recipe_IngotIron_C": (True, ["Schematic_Start_C"]),
        "Recipe_Alternate_PureIronIngot_C": (False, ["Schematic_Alternate_PureIronIngot_C"]),
    }


def test_plan_unlocks_orders_what_is_missing_with_prerequisites_and_cost() -> None:
    _, tool_result = _run_single_tool(
        _gated({"Schematic_Start_C"}), "plan_unlocks", {"item": "Iron Plate"}
    )

    assert tool_result["already_unlocked"] is False
    assert tool_result["unlock_order"] == [
        {
            "technology_id": "Schematic_1-1_C",
            "kind": "milestone",
            "tier": 1,
            "cost": {"Desc_IronIngot_C": 50},
            "unlocks_recipes": ["Recipe_IronPlate_C"],
        }
    ]


def test_plan_unlocks_for_an_unlocked_chain_has_nothing_to_do() -> None:
    _, tool_result = _run_single_tool(
        _gated({"Schematic_Start_C", "Schematic_1-1_C"}), "plan_unlocks", {"item": "Iron Plate"}
    )

    assert (tool_result["already_unlocked"], tool_result["unlock_order"]) == (True, [])


def test_plan_unlocks_refuses_raw_resources_and_missing_data() -> None:
    _, raw = _run_single_tool(_gated(set()), "plan_unlocks", {"item": "Iron Ore"})
    _, no_data = _run_single_tool(
        OrchestratorContext(recipes=_RECIPES, items=_ITEMS), "plan_unlocks", {"item": "Iron Plate"}
    )

    assert "raw resource" in raw["error"]
    assert "no technology data" in no_data["error"]


_REFINERY = Building(
    building_id="Build_OilRefinery_C",
    name="Refinery",
    power_consumption_mw=30,
    input_slots=2,
    output_slots=2,
)


def test_compare_recipes_plans_each_one_as_a_whole_chain() -> None:
    """60 ingots a minute: two smelters on 60 ore (8 MW), or one refinery on 35 ore and 20 water
    (30 MW) -- which the player hasn't unlocked."""
    context = _gated({"Schematic_Start_C"}, buildings=(*_IRON_BUILDINGS, _REFINERY))

    _, tool_result = _run_single_tool(
        context, "compare_recipes", {"item": "Iron Ingot", "target_rate_per_minute": 60}
    )

    options = {o["recipe_id"]: o for o in tool_result["options"]}
    standard = options["Recipe_IngotIron_C"]
    pure = options["Recipe_Alternate_PureIronIngot_C"]
    assert (standard["machines"], standard["power_mw"], standard["unlocked"]) == (2, 8, True)
    assert standard["inputs_per_minute"] == {"Desc_OreIron_C": 60}
    assert (pure["machines"], pure["power_mw"], pure["unlocked"]) == (1, 30, False)
    assert pure["inputs_per_minute"] == {"Desc_OreIron_C": 35, "Desc_Water_C": 20}
    assert pure["needs_unlocking"][0]["unlock_with"] == "Schematic_Alternate_PureIronIngot_C"
    assert tool_result["fewest_machines"] == "Recipe_Alternate_PureIronIngot_C"
    assert tool_result["least_power"] == "Recipe_IngotIron_C"
    assert tool_result["least_input"] == "Recipe_Alternate_PureIronIngot_C"


def test_compare_recipes_defaults_to_ten_a_minute() -> None:
    _, tool_result = _run_single_tool(_gated(None), "compare_recipes", {"item": "Iron Plate"})

    assert tool_result["target_rate_per_minute"] == 10
    assert "least_power" not in tool_result  # no building data
    assert [o["recipe_id"] for o in tool_result["options"]] == ["Recipe_IronPlate_C"]


_FUEL_POWER = (
    replace(
        _POWER_BUILDINGS[1],
        fuels=(GeneratorFuel(fuel_item_id="Desc_LiquidFuel_C"),),
    ),
    replace(_COAL_POWER[0]),
    _COAL_POWER[1],
    _POWER_BUILDINGS[0],
)
_FUEL_AND_COAL_ITEMS = (*_FUEL_ITEMS, *_COAL_ITEMS)


def test_plan_power_lists_generators_fuel_water_and_extractors() -> None:
    """300 MW: two 250 MW fuel generators (40 m³ of fuel), or four coal generators (60 coal and
    180 m³ of water -- two water extractors)."""
    context = OrchestratorContext(buildings=_FUEL_POWER, items=_FUEL_AND_COAL_ITEMS)

    _, tool_result = _run_single_tool(context, "plan_power", {"target_mw": 300})

    coal, fuel = tool_result["options"]
    assert (coal["generator_id"], coal["generators"], coal["capacity_mw"]) == (
        "Build_GeneratorCoal_C",
        4,
        300,
    )
    assert (coal["fuel_per_minute"], coal["supplemental_per_minute"]) == (60, 180)
    assert coal["extractors"] == {"building_id": "Build_WaterPump_C", "count": 2, "power_mw": 40}
    assert (fuel["generators"], fuel["fuel_per_minute"]) == (2, 40)
    assert "fuel_supply" not in tool_result


def test_plan_power_for_a_named_fuel_includes_how_to_make_it() -> None:
    context = OrchestratorContext(
        recipes=(_REFINERY_FUEL,), buildings=_FUEL_POWER, items=_FUEL_AND_COAL_ITEMS
    )

    _, tool_result = _run_single_tool(context, "plan_power", {"target_mw": 500, "fuel": "Fuel"})

    (option,) = tool_result["options"]
    assert (option["generators"], option["fuel_per_minute"]) == (2, 40)
    supply = tool_result["fuel_supply"]
    assert supply["per_minute"] == 40
    assert supply["stages"] == [
        {
            "recipe_id": "Recipe_LiquidFuel_C",
            "building_id": "Build_OilRefinery_C",
            "machines": 1,
            "power_mw": 30,
        }
    ]


def test_plan_power_for_a_mined_fuel_says_so() -> None:
    context = OrchestratorContext(buildings=_FUEL_POWER, items=_FUEL_AND_COAL_ITEMS)

    _, tool_result = _run_single_tool(context, "plan_power", {"target_mw": 75, "fuel": "Coal"})

    assert tool_result["fuel_supply"] == {"mined": True, "per_minute": 15}


def test_plan_power_refuses_nonsense() -> None:
    context = OrchestratorContext(buildings=_FUEL_POWER, items=_FUEL_AND_COAL_ITEMS)

    _, negative = _run_single_tool(context, "plan_power", {"target_mw": -5})
    _, unburnable = _run_single_tool(context, "plan_power", {"target_mw": 5, "fuel": "Crude Oil"})

    assert "positive" in negative["error"]
    assert "no generator burns Desc_LiquidOil_C" in unburnable["error"]


def test_a_plan_says_which_belt_each_flow_needs() -> None:
    tiers = (
        TransportTier(
            building_id="Build_ConveyorBeltMk1_C",
            name="Conveyor Belt Mk.1",
            capacity_per_minute=60,
            carries_fluids=False,
        ),
    )
    context = OrchestratorContext(
        recipes=_RECIPES + (_IRON_INGOT,), items=_ITEMS, transport_tiers=tiers
    )

    _, tool_result = _run_single_tool(
        context, "plan_production", {"target_item_id": "Iron Plate", "target_rate_per_minute": 60}
    )

    assert tool_result["transport"] == [
        {
            "item_id": "Desc_IronIngot_C",
            "per_minute": 90,
            "from": "Recipe_IngotIron_C",
            "to": "Recipe_IronPlate_C",
            "tier": "Build_ConveyorBeltMk1_C",
            "lines": 2,
        },
        {
            "item_id": "Desc_OreIron_C",
            "per_minute": 90,
            "from": "outside",
            "to": "Recipe_IngotIron_C",
            "tier": "Build_ConveyorBeltMk1_C",
            "lines": 2,
        },
        {
            "item_id": "Desc_IronPlate_C",
            "per_minute": 60,
            "from": "Recipe_IronPlate_C",
            "to": "output",
            "tier": "Build_ConveyorBeltMk1_C",
            "lines": 1,
        },
    ]


# --- Where: factory sites and deposits ----------------------------------------------------------


def _smelting_site(site_id: str, x: float, smelters: int) -> FactorySite:
    return FactorySite(
        site_id=site_id,
        position=Coordinates(x=x, y=0),
        placements=tuple(
            _placed("Build_SmelterMk1_C", recipe_id="Recipe_IngotIron_C") for _ in range(smelters)
        ),
    )


def test_an_extension_names_the_factory_site_that_runs_the_recipe_most() -> None:
    """60 plates a minute need 90 ingots; 2 smelters spare 60, so 1 more smelter goes to the site
    with the most of them, which is the answer's map. No site makes plates yet."""
    small, big = _smelting_site("site_2", 50_000, 1), _smelting_site("site_1", 0, 3)
    context = OrchestratorContext(
        recipes=_RECIPES + (_IRON_INGOT,),
        items=_ITEMS,
        existing_graph=_existing_iron(smelters=2, constructors=0),
        factory_sites=(small, big),
    )

    result, tool_result = _run_single_tool(
        context,
        "expand_existing_factory",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 60},
    )

    ingots = next(c for c in tool_result["changes"] if c["recipe_id"] == "Recipe_IngotIron_C")
    plates = next(c for c in tool_result["changes"] if c["recipe_id"] == "Recipe_IronPlate_C")
    assert ingots["at_site"] == "site_1"
    assert "at_site" not in plates  # no site runs plates
    assert tool_result["sites"]["site_1"]["buildings"] == 3
    assert tool_result["sites"]["site_1"]["main_recipes"] == ["Recipe_IngotIron_C"]
    assert result.factory_sites == (big,)


def test_a_plan_suggests_a_deposit_for_each_raw_input_and_maps_them() -> None:
    context = OrchestratorContext(
        recipes=_RECIPES + (_IRON_INGOT,), items=_ITEMS, resource_nodes=_NODES
    )

    result, tool_result = _run_single_tool(
        context, "plan_production", {"target_item_id": "Iron Plate", "target_rate_per_minute": 20}
    )

    assert tool_result["suggested_sites"] == {
        "Desc_OreIron_C": {
            "resource_node_id": "Persistent_Level:PersistentLevel.BP_ResourceNode1",
            "purity": "pure",
            "distance_m": 10,
        }
    }
    assert [loc.resource_node_id for loc in result.map_locations] == [
        "Persistent_Level:PersistentLevel.BP_ResourceNode1"
    ]
    assert result.map_reference == Coordinates(x=0, y=0, z=0)


def test_an_explicit_site_ranking_is_not_replaced_by_a_plans_suggestions() -> None:
    context = OrchestratorContext(
        recipes=_RECIPES + (_IRON_INGOT,), items=_ITEMS, resource_nodes=_NODES
    )
    llm = _scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [
                    _tool_call("c1", "rank_build_locations", {"item_id": "geyser"}),
                    _tool_call(
                        "c2",
                        "plan_production",
                        {"target_item_id": "Iron Plate", "target_rate_per_minute": 20},
                    ),
                ],
            },
            {"content": "done", "tool_calls": []},
        ]
    )

    result = handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "q",
        context,
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="r",
    )

    assert [loc.resource_node_id for loc in result.map_locations] == [
        "Persistent_Level:PersistentLevel.BP_ResourceNodeGeyser1"
    ]


def test_ranked_sites_are_reported_in_metres() -> None:
    context = OrchestratorContext(items=_ITEMS, resource_nodes=_NODES)

    _, tool_result = _run_single_tool(context, "rank_build_locations", {"item_id": "Iron Ore"})

    assert [site["distance_m"] for site in tool_result["locations"]] == [10, 900]


def test_earlier_turns_come_before_the_question_and_ground_the_answer() -> None:
    llm = _scripted_tool_calling_llm([{"content": "About 12 MW.", "tool_calls": []}])

    result = handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "and the power?",
        OrchestratorContext(),
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
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


def test_a_tool_call_the_model_wrote_as_text_is_executed_anyway() -> None:
    """Local models sometimes answer with the call instead of making it."""
    written = json.dumps(
        {
            "name": "plan_production",
            "arguments": {"target_item_id": "Iron Plate", "target_rate_per_minute": 20},
        }
    )
    llm = _scripted_tool_calling_llm(
        [
            {"content": f"Let me check: {written}", "tool_calls": []},
            {"content": "You need 1 Constructor.", "tool_calls": []},
        ]
    )

    result = handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "20 plates a minute please",
        OrchestratorContext(recipes=_RECIPES, items=_ITEMS),
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="r",
    )

    assert result.chat == "You need 1 Constructor."
    assert result.graph is not None
    assert result.graph.nodes[0].recipe_id == "Recipe_IronPlate_C"


def test_an_answer_that_merely_contains_braces_stays_an_answer() -> None:
    llm = _scripted_tool_calling_llm(
        [{"content": 'Set it to {"clock": 250} in the UI.', "tool_calls": []}]
    )

    result = handle_query(
        llm,
        _fake_qa_chat_completion(""),
        "how do I overclock?",
        OrchestratorContext(recipes=_RECIPES, items=_ITEMS),
        llm_base_url=_BASE_URL,
        llm_model=_MODEL,
        response_id="r",
    )

    assert result.chat == 'Set it to {"clock": 250} in the UI.'
    assert result.graph is None
