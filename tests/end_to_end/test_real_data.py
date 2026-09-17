"""End-to-end checks over real data — implementation.md Stage 16's top-level confidence check.

The real Knowledge Base from `docs/en-US.json` and a real save from `tests/save_parser/fixtures/`,
wired through `app.build_context` exactly as the CLI wires them, driven by a scripted stand-in for
the LLM. Only the model is faked: every module call underneath is the real one, on real data —
which is what catches problems no fixture-based module test can, like the planner walking in
circles through 1.0's ore-conversion recipes.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from pioneer.app import (
    DOCS_JSON,
    RESOURCE_NODES_JSON,
    build_context,
    load_knowledge_base,
    load_resource_nodes,
)
from pioneer.contracts import ResponseArtifact
from pioneer.knowledge_base import KnowledgeBase, raw_resource_ids
from pioneer.orchestrator import OrchestratorContext, handle_query
from pioneer.production_planner.planner import plan_production
from pioneer.save_parser import load_save_state
from pioneer.verifier import power_balance

_SAVES = Path(__file__).parent.parent / "save_parser" / "fixtures"
_SAVE = _SAVES / "stal_mielec.sav"

pytestmark = pytest.mark.skipif(not DOCS_JSON.exists(), reason="docs/en-US.json not present")


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    kb = load_knowledge_base()
    assert kb is not None
    return kb


@pytest.fixture(scope="module")
def context(kb: KnowledgeBase) -> OrchestratorContext:
    return build_context(kb, load_save_state(_SAVE))


def _ask(
    context: OrchestratorContext, *tool_calls: tuple[str, dict[str, Any]]
) -> tuple[ResponseArtifact, list[dict[str, Any]]]:
    """One turn in which the scripted model makes `tool_calls`, then answers. Returns the
    artifact and each tool's parsed result, in call order."""
    rounds = [
        {
            "content": None,
            "tool_calls": [
                {"id": f"call_{i}", "name": name, "arguments": arguments}
                for i, (name, arguments) in enumerate(tool_calls)
            ],
        },
        {"content": "done", "tool_calls": []},
    ]
    seen: list[list[dict[str, Any]]] = []

    def scripted_llm(base_url, model, messages, tools, api_key):
        seen.append(list(messages))
        return rounds[len(seen) - 1]

    def unused_qa(base_url, model, messages, api_key):
        return ""

    artifact = handle_query(
        scripted_llm,
        unused_qa,
        "question",
        context,
        llm_base_url="http://llm.invalid/v1",
        llm_model="model",
        response_id="e2e",
    )
    assert isinstance(artifact, ResponseArtifact)
    return artifact, [json.loads(m["content"]) for m in seen[-1] if m["role"] == "tool"]


def test_planning_by_in_game_name_yields_the_standard_iron_chain(context) -> None:
    artifact, [result] = _ask(
        context,
        (
            "plan_production",
            {"target_item_id": "Reinforced Iron Plate", "target_rate_per_minute": 5},
        ),
    )

    assert "error" not in result
    assert artifact.graph is not None
    assert {n.recipe_id: n.machine_count for n in artifact.graph.nodes} == {
        "Recipe_IronPlateReinforced_C": 1,
        "Recipe_IronPlate_C": 2,
        "Recipe_Screw_C": 2,
        "Recipe_IronRod_C": 2,
        "Recipe_IngotIron_C": 3,
    }
    raw_inputs = [
        (f.item_id, f.amount_per_minute) for f in artifact.graph.flows if f.source_node_id is None
    ]
    assert raw_inputs == [("Desc_OreIron_C", 90.0)]


def test_find_item_resolves_a_partial_name(context) -> None:
    _, [result] = _ask(context, ("find_item", {"query": "heavy modular"}))
    assert result["items"][0]["item_id"] == "Desc_ModularFrameHeavy_C"


def test_unknown_item_comes_back_with_suggestions(context) -> None:
    artifact, [result] = _ask(
        context,
        ("plan_production", {"target_item_id": "Reinforced Plates", "target_rate_per_minute": 5}),
    )
    assert artifact.graph is None
    assert "Reinforced Plates" in result["error"]
    assert "Desc_IronPlateReinforced_C" in [s["item_id"] for s in result["did_you_mean"]]


@pytest.mark.parametrize(
    "item_id",
    [
        "Desc_IronPlate_C",
        "Desc_IronPlateReinforced_C",
        "Desc_ModularFrame_C",
        "Desc_ModularFrameHeavy_C",
        "Desc_Computer_C",
        "Desc_SteelPlate_C",
        "Desc_Plastic_C",
        "Desc_LiquidFuel_C",
        "Desc_AluminumIngot_C",
        "Desc_Motor_C",
    ],
)
def test_real_targets_plan_with_standard_recipes_on_real_buildings(kb, item_id) -> None:
    graph = plan_production(item_id, 10, kb.recipes, raw_item_ids=raw_resource_ids(kb))
    recipes = {r.recipe_id: r for r in kb.recipes}

    assert graph.nodes
    assert [n.recipe_id for n in graph.nodes if recipes[n.recipe_id].is_alternate] == []
    power_balance(graph, kb.buildings)  # raises if any node sits on a building the KB doesn't know


def test_every_item_with_a_recipe_can_be_planned(kb) -> None:
    raw = raw_resource_ids(kb)
    craftable = sorted({o.item_id for r in kb.recipes for o in r.outputs} - raw)
    failures = {}
    for item_id in craftable:
        try:
            plan_production(item_id, 1, kb.recipes, raw_item_ids=raw)
        except ValueError as error:
            failures[item_id] = str(error)

    assert len(craftable) > 100  # guard: the sweep is meaningless if it covered nothing
    assert failures == {}


def test_expansion_extends_what_the_save_already_runs(context) -> None:
    """stal_mielec already runs every stage of the Reinforced Iron Plate chain, with Iron Plate
    and Iron Rod to spare: 5/min more extends four of its factories, and takes the spare plates
    and rods before planning a single new machine for them."""
    artifact, [result] = _ask(
        context,
        (
            "expand_existing_factory",
            {"target_item_id": "Reinforced Iron Plate", "target_rate_per_minute": 5},
        ),
    )

    assert {change["recipe_id"]: change["action"] for change in result["changes"]} == {
        "Recipe_IronPlateReinforced_C": "extend",
        "Recipe_Screw_C": "extend",
        "Recipe_IronPlate_C": "extend",
        "Recipe_IngotIron_C": "extend",
    }
    assert set(result["drawn_from_existing_surplus_per_minute"]) == {
        "Desc_IronPlate_C",
        "Desc_IronRod_C",
    }
    assert artifact.graph is not None
    node_ids = {node.node_id for node in artifact.graph.nodes}
    for flow in artifact.graph.flows:
        assert flow.source_node_id in node_ids | {None}
        assert flow.target_node_id in node_ids | {None}


def test_expansion_already_covered_by_surplus_builds_nothing(kb) -> None:
    """wielka_polska_niesmiertelna overproduces Reinforced Iron Plate: 5/min more is output it
    already has spare, not a reason to build anything."""
    context = build_context(kb, load_save_state(_SAVES / "wielka_polska_niesmiertelna.sav"))

    _, [result] = _ask(
        context,
        (
            "expand_existing_factory",
            {"target_item_id": "Reinforced Iron Plate", "target_rate_per_minute": 5},
        ),
    )

    assert result["changes"] == []
    assert result["drawn_from_existing_surplus_per_minute"] == {"Desc_IronPlateReinforced_C": 5}


@pytest.mark.parametrize("save_name", ["stal_mielec", "wielka_polska_niesmiertelna"])
def test_diagnosis_of_a_real_save_flags_only_craftable_shortfalls(kb, save_name) -> None:
    """Every extractor's ore and every hand-gathered item (Wood, Mycelia) is an input from outside
    the factory, not a shortfall; the placed generators out-produce the placed consumers, with the
    generators' own fuel counted as consumed."""
    context = build_context(kb, load_save_state(_SAVES / f"{save_name}.sav"))

    _, [result] = _ask(context, ("diagnose_factory_problems", {}))

    craftable = {o.item_id for r in kb.recipes for o in r.outputs} - raw_resource_ids(kb)
    deficits = {a["item_id"] for a in result["anomalies"] if a["kind"] == "resource_deficit"}
    assert deficits
    assert deficits <= craftable
    assert "power_blackout" not in {a["kind"] for a in result["anomalies"]}
    assert result["power_capacity_mw"] > result["power_draw_mw"] > 0


needs_node_data = pytest.mark.skipif(
    not RESOURCE_NODES_JSON.exists(),
    reason="docs/resource_nodes.json not present",
)


@needs_node_data
@pytest.mark.parametrize("save_name", ["stal_mielec", "wielka_polska_niesmiertelna"])
def test_every_node_a_real_save_mines_is_in_the_node_data(save_name) -> None:
    known = {node.node_id for node in load_resource_nodes(RESOURCE_NODES_JSON)}
    state = load_save_state(_SAVES / f"{save_name}.sav")

    mined = {
        p.resource_node_id
        for p in state.placements
        if p.resource_node_id and "FGWaterVolume" not in p.resource_node_id
    }

    assert mined
    assert mined <= known


@needs_node_data
def test_location_ranking_on_a_real_save_skips_the_nodes_it_already_mines(kb) -> None:
    state = load_save_state(_SAVE)
    context = build_context(kb, state, load_resource_nodes(RESOURCE_NODES_JSON))

    artifact, [result] = _ask(
        context, ("rank_build_locations", {"item_id": "Iron Ore", "count": 200})
    )

    mined = {p.resource_node_id for p in state.placements if p.resource_node_id}
    ranked = {location["resource_node_id"] for location in result["locations"]}
    assert ranked
    assert not ranked & mined
    assert artifact.map_locations is not None
    assert len(artifact.map_locations) == len(ranked)


@needs_node_data
@pytest.mark.parametrize("save_name", ["stal_mielec", "wielka_polska_niesmiertelna"])
def test_with_node_data_the_real_saves_power_and_ore_add_up(kb, save_name) -> None:
    """With extraction known, ore gets judged like everything else; hand-gathered items still
    don't, and neither save is in a blackout."""
    state = load_save_state(_SAVES / f"{save_name}.sav")
    context = build_context(kb, state, load_resource_nodes(RESOURCE_NODES_JSON))

    _, [result] = _ask(context, ("diagnose_factory_problems", {}))

    craftable_or_raw = {o.item_id for r in kb.recipes for o in r.outputs} | raw_resource_ids(kb)
    deficits = {a["item_id"] for a in result["anomalies"] if a["kind"] == "resource_deficit"}
    assert deficits <= craftable_or_raw
    assert "power_blackout" not in {a["kind"] for a in result["anomalies"]}


@needs_node_data
def test_the_water_a_real_saves_coal_generators_drink_is_not_spare(kb) -> None:
    """stal_mielec runs 36 coal generators, 45 m³ of water a minute each: counting only their coal
    left 1620 m³/min of water looking overproduced."""
    state = load_save_state(_SAVE)
    context = build_context(kb, state, load_resource_nodes(RESOURCE_NODES_JSON))

    _, [result] = _ask(context, ("diagnose_factory_problems", {}))

    water = [a for a in result["anomalies"] if a["item_id"] == "Desc_Water_C"]
    for anomaly in water:
        off_by = float(re.search(r"by ([-+.\de]+)/min", anomaly["description"]).group(1))
        assert off_by < 45  # less than one generator's worth
    severities = {a["severity"] for a in result["anomalies"] if a["kind"] == "resource_deficit"}
    assert len(severities) > 1  # rated against real demand, not all the same for want of one


def test_a_game_question_is_answered_from_the_real_corpus(context) -> None:
    """The Q&A tool retrieves from the game's own text: a belt question finds the belt."""
    answered = []

    def qa_model(base_url, model, messages, api_key):
        answered.append(messages[1]["content"])
        return "Mk.1 belts move 60 items per minute."

    question = "How many resources per minute does a Conveyor Belt Mk.1 move?"
    rounds = [
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_0",
                    "name": "answer_game_question",
                    "arguments": {"question": question},
                }
            ],
        },
        {"content": "done", "tool_calls": []},
    ]
    calls = []

    def scripted_llm(base_url, model, messages, tools, api_key):
        calls.append(messages)
        return rounds[len(calls) - 1]

    handle_query(
        scripted_llm,
        qa_model,
        "question",
        context,
        llm_base_url="http://llm.invalid/v1",
        llm_model="model",
        response_id="e2e-qa",
    )

    assert answered
    assert "Transports up to 60 resources per minute" in answered[0]
