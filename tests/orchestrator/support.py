"""What the Orchestrator's tests share: a scripted fake model and QA engine, running one tool
through the whole loop, and the game data and factories they're run on."""

import json
from dataclasses import replace
from typing import Any

from tests.game_data import IRON_INGOT, IRON_PLATE, PURE_IRON_INGOT

from pioneer.contracts import (
    Building,
    Coordinates,
    GeneratorFuel,
    Item,
    ItemCount,
    PlacementRecord,
    Purity,
    ResourceNode,
    Technology,
)
from pioneer.orchestrator.base import OrchestratorContext
from pioneer.orchestrator.orchestrator import handle_query

BASE_URL = "http://localhost:11434/v1"

MODEL = "test-model"

RECIPES = (IRON_PLATE,)

ITEMS = (
    Item(item_id="Desc_IronPlate_C", name="Iron Plate"),
    Item(item_id="Desc_IronIngot_C", name="Iron Ingot"),
    Item(item_id="Desc_OreIron_C", name="Iron Ore", is_raw_resource=True),
    Item(item_id="Desc_Stone_C", name="Limestone", is_raw_resource=True),
)


def scripted_tool_calling_llm(responses: list[dict[str, Any]]):
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


def fake_qa_chat_completion(reply: str):
    def chat_completion(
        base_url: str, model: str, messages: list[dict[str, str]], api_key: str | None
    ) -> str:
        return reply

    return chat_completion


def tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"id": call_id, "name": name, "arguments": arguments}


def ask(
    llm: Any,
    question: str,
    context: OrchestratorContext,
    *,
    qa_reply: str = "",
    **options: Any,
) -> Any:
    """`handle_query` against the scripted `llm`, the QA engine's model answering `qa_reply`."""
    return handle_query(
        llm,
        fake_qa_chat_completion(qa_reply),
        question,
        context,
        llm_base_url=BASE_URL,
        llm_model=MODEL,
        **options,
    )


def run_single_tool(
    context: OrchestratorContext, name: str, arguments: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    """One turn where the model calls `name` once, then answers "done". Returns the
    `handle_query` result and the tool's parsed result as the model saw it."""
    llm = scripted_tool_calling_llm(
        [
            {"content": None, "tool_calls": [tool_call("call_1", name, arguments)]},
            {"content": "done", "tool_calls": []},
        ]
    )
    result = ask(llm, "question", context, response_id="resp")
    messages = llm.calls[1]["messages"]  # type: ignore[attr-defined]
    tool_message = next(m for m in messages if m["role"] == "tool")
    return result, json.loads(tool_message["content"])


IRON_BUILDINGS = (
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

POWER_BUILDINGS = (
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

FUEL_ITEMS = (
    Item(item_id="Desc_LiquidOil_C", name="Crude Oil", is_fluid=True, is_raw_resource=True),
    Item(item_id="Desc_LiquidFuel_C", name="Fuel", is_fluid=True, energy_value_mj=750),
)


def placed(building_id: str, **fields: Any) -> PlacementRecord:
    return PlacementRecord(building_id=building_id, position=Coordinates(x=0, y=0), **fields)


COAL_POWER = (
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

COAL_ITEMS = (
    Item(item_id="Desc_Coal_C", name="Coal", is_raw_resource=True, energy_value_mj=300),
    Item(item_id="Desc_Water_C", name="Water", is_fluid=True, is_raw_resource=True),
)

NODES = (
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

MINER = Building(
    building_id="Build_MinerMk1_C",
    name="Miner Mk.1",
    power_consumption_mw=5,
    input_slots=0,
    output_slots=1,
    extraction_rate_per_minute=60,
)

START = Technology(technology_id="Schematic_Start_C", name="Start", tier=0, kind="custom")

PLATES = Technology(
    technology_id="Schematic_1-1_C",
    name="Plates",
    tier=1,
    kind="milestone",
    prerequisites=("Schematic_Start_C",),
    cost=(ItemCount(item_id="Desc_IronIngot_C", amount=50),),
)

PURE_DRIVE = Technology(
    technology_id="Schematic_Alternate_PureIronIngot_C",
    name="Alternate: Pure Iron Ingot",
    tier=0,
    kind="alternate",
    prerequisites=("Schematic_1-1_C",),
)

GATED_RECIPES = (
    replace(RECIPES[0], unlockable_by=("Schematic_1-1_C",)),
    replace(IRON_INGOT, unlockable_by=("Schematic_Start_C",)),
    replace(PURE_IRON_INGOT, unlockable_by=("Schematic_Alternate_PureIronIngot_C",)),
)

WATER_ITEMS = (*ITEMS, Item(item_id="Desc_Water_C", name="Water", is_raw_resource=True))


def gated(unlocked: set[str] | None, **fields: Any) -> OrchestratorContext:
    return OrchestratorContext(
        recipes=GATED_RECIPES,
        items=WATER_ITEMS,
        technologies=(START, PLATES, PURE_DRIVE),
        unlocked_technology_ids=None if unlocked is None else frozenset(unlocked),
        **fields,
    )


def picked(*recipe_ids: str, **fields: Any) -> OrchestratorContext:
    """The gated context, Start and Plates unlocked, with the player's own pick of recipes."""
    return gated(
        {"Schematic_Start_C", "Schematic_1-1_C"}, enabled_recipe_ids=frozenset(recipe_ids), **fields
    )
