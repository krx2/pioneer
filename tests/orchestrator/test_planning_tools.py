"""The planning tools, against a scripted fake model: plan_production, compare_recipes, plan_power
and plan_unlocks, and planning from the recipes the player has unlocked or picked."""

import json
from dataclasses import replace

from tests.game_data import FUEL, IRON_INGOT, PURE_IRON_INGOT
from tests.orchestrator.support import (
    COAL_ITEMS,
    COAL_POWER,
    FUEL_ITEMS,
    IRON_BUILDINGS,
    ITEMS,
    POWER_BUILDINGS,
    RECIPES,
    WATER_ITEMS,
    ask,
    gated,
    picked,
    run_single_tool,
    scripted_tool_calling_llm,
    tool_call,
)

from pioneer.contracts import (
    Building,
    GeneratorFuel,
    Item,
    TransportTier,
)
from pioneer.orchestrator.base import OrchestratorContext

_REFINERY = Building(
    building_id="Build_OilRefinery_C",
    name="Refinery",
    power_consumption_mw=30,
    input_slots=2,
    output_slots=2,
)

_FUEL_POWER = (
    replace(
        POWER_BUILDINGS[1],
        fuels=(GeneratorFuel(fuel_item_id="Desc_LiquidFuel_C"),),
    ),
    replace(COAL_POWER[0]),
    COAL_POWER[1],
    POWER_BUILDINGS[0],
)

_FUEL_AND_COAL_ITEMS = (*FUEL_ITEMS, *COAL_ITEMS)


def test_plan_production_tool_populates_graph() -> None:
    llm = scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [
                    tool_call(
                        "call_1",
                        "plan_production",
                        {"target_item_id": "Desc_IronPlate_C", "target_rate_per_minute": 20},
                    )
                ],
            },
            {"content": "You need 1 Constructor making Iron Plates.", "tool_calls": []},
        ]
    )

    result = ask(
        llm,
        "I want to produce 20/min of Iron Plate",
        OrchestratorContext(recipes=RECIPES),
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


def test_plan_production_honours_recipe_choices_given_by_item_name() -> None:
    context = OrchestratorContext(recipes=RECIPES + (IRON_INGOT, PURE_IRON_INGOT), items=ITEMS)

    result, _ = run_single_tool(
        context,
        "plan_production",
        {
            "target_item_id": "Iron Plate",
            "target_rate_per_minute": 20,
            "recipe_choices": {"Iron Ingot": "Recipe_Alternate_PureIronIngot_C"},
        },
    )

    assert "Recipe_Alternate_PureIronIngot_C" in {n.recipe_id for n in result.graph.nodes}


def test_a_rate_that_is_not_positive_is_refused() -> None:
    context = OrchestratorContext(recipes=RECIPES + (IRON_INGOT,), items=ITEMS)

    for rate in (0, -5):
        result, tool_result = run_single_tool(
            context,
            "plan_production",
            {"target_item_id": "Iron Plate", "target_rate_per_minute": rate},
        )
        assert "positive" in tool_result["error"]
        assert result.graph is None


def test_a_plan_names_each_stages_building_and_power() -> None:
    context = OrchestratorContext(
        recipes=RECIPES + (IRON_INGOT,), buildings=IRON_BUILDINGS, items=ITEMS
    )

    _, tool_result = run_single_tool(
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


def test_a_plan_the_unlocked_recipes_cannot_make_says_what_to_unlock() -> None:
    result, tool_result = run_single_tool(
        gated({"Schematic_Start_C"}),
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
    context = gated({"Schematic_1-1_C", "Schematic_Alternate_PureIronIngot_C"})

    result, tool_result = run_single_tool(
        context, "plan_production", {"target_item_id": "Iron Plate", "target_rate_per_minute": 20}
    )

    assert "Recipe_Alternate_PureIronIngot_C" in {n.recipe_id for n in result.graph.nodes}
    assert "needs_unlocking" not in tool_result


def test_with_unlocks_unknown_every_recipe_is_fair_game() -> None:
    _, tool_result = run_single_tool(
        gated(None),
        "plan_production",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 20},
    )

    assert "needs_unlocking" not in tool_result


def test_listed_recipes_say_whether_they_are_unlocked() -> None:
    _, tool_result = run_single_tool(
        gated({"Schematic_Start_C"}), "list_recipes_for_item", {"item": "Iron Ingot"}
    )

    assert {r["recipe_id"]: (r["unlocked"], r["unlocked_by"]) for r in tool_result["recipes"]} == {
        "Recipe_IngotIron_C": (True, ["Schematic_Start_C"]),
        "Recipe_Alternate_PureIronIngot_C": (False, ["Schematic_Alternate_PureIronIngot_C"]),
    }


def test_plan_unlocks_orders_what_is_missing_with_prerequisites_and_cost() -> None:
    _, tool_result = run_single_tool(
        gated({"Schematic_Start_C"}), "plan_unlocks", {"item": "Iron Plate"}
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
    _, tool_result = run_single_tool(
        gated({"Schematic_Start_C", "Schematic_1-1_C"}), "plan_unlocks", {"item": "Iron Plate"}
    )

    assert (tool_result["already_unlocked"], tool_result["unlock_order"]) == (True, [])


def test_plan_unlocks_refuses_raw_resources_and_missing_data() -> None:
    _, raw = run_single_tool(gated(set()), "plan_unlocks", {"item": "Iron Ore"})
    _, no_data = run_single_tool(
        OrchestratorContext(recipes=RECIPES, items=ITEMS), "plan_unlocks", {"item": "Iron Plate"}
    )

    assert "raw resource" in raw["error"]
    assert "no technology data" in no_data["error"]


def test_compare_recipes_plans_each_one_as_a_whole_chain() -> None:
    """60 ingots a minute: two smelters on 60 ore (8 MW), or one refinery on 35 ore and 20 water
    (30 MW) -- which the player hasn't unlocked."""
    context = gated({"Schematic_Start_C"}, buildings=(*IRON_BUILDINGS, _REFINERY))

    _, tool_result = run_single_tool(
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
    _, tool_result = run_single_tool(gated(None), "compare_recipes", {"item": "Iron Plate"})

    assert tool_result["target_rate_per_minute"] == 10
    assert "least_power" not in tool_result  # no building data
    assert [o["recipe_id"] for o in tool_result["options"]] == ["Recipe_IronPlate_C"]


def test_plan_power_lists_generators_fuel_water_and_extractors() -> None:
    """300 MW: two 250 MW fuel generators (40 m³ of fuel), or four coal generators (60 coal and
    180 m³ of water -- two water extractors)."""
    context = OrchestratorContext(buildings=_FUEL_POWER, items=_FUEL_AND_COAL_ITEMS)

    _, tool_result = run_single_tool(context, "plan_power", {"target_mw": 300})

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
        recipes=(FUEL,), buildings=_FUEL_POWER, items=_FUEL_AND_COAL_ITEMS
    )

    _, tool_result = run_single_tool(context, "plan_power", {"target_mw": 500, "fuel": "Fuel"})

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

    _, tool_result = run_single_tool(context, "plan_power", {"target_mw": 75, "fuel": "Coal"})

    assert tool_result["fuel_supply"] == {"mined": True, "per_minute": 15}


def test_plan_power_refuses_nonsense() -> None:
    context = OrchestratorContext(buildings=_FUEL_POWER, items=_FUEL_AND_COAL_ITEMS)

    _, negative = run_single_tool(context, "plan_power", {"target_mw": -5})
    _, unburnable = run_single_tool(context, "plan_power", {"target_mw": 5, "fuel": "Crude Oil"})

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
        recipes=RECIPES + (IRON_INGOT,), items=ITEMS, transport_tiers=tiers
    )

    _, tool_result = run_single_tool(
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


def test_a_turned_off_recipe_is_not_planned_and_a_turned_on_locked_one_is() -> None:
    context = picked("Recipe_IronPlate_C", "Recipe_Alternate_PureIronIngot_C")

    result, tool_result = run_single_tool(
        context, "plan_production", {"target_item_id": "Iron Plate", "target_rate_per_minute": 20}
    )

    assert {node.recipe_id for node in result.graph.nodes} == {
        "Recipe_IronPlate_C",
        "Recipe_Alternate_PureIronIngot_C",
    }
    assert [entry["recipe_id"] for entry in tool_result["needs_unlocking"]] == [
        "Recipe_Alternate_PureIronIngot_C"
    ]
    assert result.missing_recipes == ()


def test_a_plan_the_picked_recipes_cannot_make_says_which_recipes_it_would_take() -> None:
    result, tool_result = run_single_tool(
        picked("Recipe_IronPlate_C"),
        "plan_production",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 20},
    )

    assert result.graph is None  # no plan without them: nothing to draw
    assert tool_result["missing_recipes"] == [
        {
            "item_id": "Desc_IronIngot_C",
            "any_of": [
                {"recipe_id": "Recipe_IngotIron_C", "unlocked": True},
                {
                    "recipe_id": "Recipe_Alternate_PureIronIngot_C",
                    "unlocked": False,
                    "unlock_with": "Schematic_Alternate_PureIronIngot_C",
                    "tier": 0,
                    "kind": "alternate",
                },
            ],
        }
    ]
    assert "no plan" in tool_result["error"]
    assert result.missing_recipes == ("Recipe_IngotIron_C", "Recipe_Alternate_PureIronIngot_C")


def test_no_missing_recipes_when_no_recipe_at_all_would_help() -> None:
    leaves = Item(item_id="Desc_Leaves_C", name="Leaves")
    context = replace(picked("Recipe_IronPlate_C"), items=(*WATER_ITEMS, leaves))

    result, tool_result = run_single_tool(
        context, "plan_production", {"target_item_id": "Leaves", "target_rate_per_minute": 1}
    )

    assert "no recipe produces" in tool_result["error"]
    assert "missing_recipes" not in tool_result
    assert result.missing_recipes == ()


def test_the_model_cannot_ask_for_a_recipe_the_player_turned_off() -> None:
    _, tool_result = run_single_tool(
        picked("Recipe_IronPlate_C", "Recipe_IngotIron_C"),
        "plan_production",
        {
            "target_item_id": "Iron Plate",
            "target_rate_per_minute": 20,
            "recipe_choices": {"Iron Ingot": "Recipe_Alternate_PureIronIngot_C"},
        },
    )

    assert "turned off in the player's recipe list" in tool_result["error"]
    assert tool_result["turned_off"] == "Recipe_Alternate_PureIronIngot_C"


def test_listed_and_compared_recipes_say_whether_the_player_turned_them_on() -> None:
    context = picked(
        "Recipe_IronPlate_C", "Recipe_IngotIron_C", buildings=(*IRON_BUILDINGS, _REFINERY)
    )

    _, listed = run_single_tool(context, "list_recipes_for_item", {"item": "Iron Ingot"})
    _, compared = run_single_tool(
        context, "compare_recipes", {"item": "Iron Ingot", "target_rate_per_minute": 60}
    )

    assert {r["recipe_id"]: r["enabled"] for r in listed["recipes"]} == {
        "Recipe_IngotIron_C": True,
        "Recipe_Alternate_PureIronIngot_C": False,
    }
    options = {o["recipe_id"]: o for o in compared["options"]}
    assert options["Recipe_Alternate_PureIronIngot_C"]["enabled"] is False
    assert options["Recipe_Alternate_PureIronIngot_C"]["machines"] == 1  # compared as if on
    assert options["Recipe_IngotIron_C"]["enabled"] is True
