"""Items by name, as the model passes them to tools: ids, in-game names, plurals, names several
items fit, ids made up from a name -- and the find_item and list_recipes_for_item tools."""

from tests.game_data import IRON_INGOT, PURE_IRON_INGOT
from tests.orchestrator.support import ITEMS, RECIPES, run_single_tool

from pioneer.contracts import (
    Item,
    ItemAmount,
    Recipe,
)
from pioneer.orchestrator.base import OrchestratorContext

_ORE_FROM_LIMESTONE = Recipe(
    recipe_id="Recipe_Iron_Limestone_C",
    name="Iron Ore (Limestone)",
    building_ids=("Build_Converter_C",),
    inputs=(ItemAmount(item_id="Desc_Stone_C", amount_per_minute=120),),
    outputs=(ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=60),),
)


def test_item_names_resolve_and_planning_stops_at_raw_resources() -> None:
    context = OrchestratorContext(recipes=RECIPES + (_ORE_FROM_LIMESTONE, IRON_INGOT), items=ITEMS)

    result, tool_result = run_single_tool(
        context, "plan_production", {"target_item_id": "iron plate", "target_rate_per_minute": 20}
    )

    assert "error" not in tool_result
    assert {n.recipe_id for n in result.graph.nodes} == {
        "Recipe_IronPlate_C",
        "Recipe_IngotIron_C",
    }


def test_a_name_several_items_fit_is_reported_with_them() -> None:
    context = OrchestratorContext(recipes=RECIPES, items=ITEMS)

    result, tool_result = run_single_tool(
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
    context = OrchestratorContext(recipes=RECIPES, items=ITEMS)

    _, tool_result = run_single_tool(
        context, "plan_production", {"target_item_id": "Uranium", "target_rate_per_minute": 20}
    )

    assert tool_result == {"error": "unknown item 'Uranium'", "did_you_mean": []}


def test_a_partial_or_plural_name_one_item_fits_is_taken() -> None:
    context = OrchestratorContext(recipes=RECIPES, items=ITEMS)

    for name in ("Iron Plat", "iron plates"):
        result, tool_result = run_single_tool(
            context, "plan_production", {"target_item_id": name, "target_rate_per_minute": 20}
        )
        assert "error" not in tool_result, name
        assert result.graph.nodes[0].recipe_id == "Recipe_IronPlate_C"


def test_find_item_tool_lists_matching_items() -> None:
    _, tool_result = run_single_tool(
        OrchestratorContext(items=ITEMS), "find_item", {"query": "iron"}
    )

    item_ids = [item["item_id"] for item in tool_result["items"]]
    assert item_ids[0] == "Desc_OreIron_C"  # shortest name first among equally good matches
    assert set(item_ids) == {"Desc_OreIron_C", "Desc_IronPlate_C", "Desc_IronIngot_C"}


def test_find_item_without_a_knowledge_base_says_so() -> None:
    _, tool_result = run_single_tool(OrchestratorContext(), "find_item", {"query": "iron"})
    assert "no knowledge base" in tool_result["error"]


def test_list_recipes_for_item_flags_alternates() -> None:
    context = OrchestratorContext(recipes=(IRON_INGOT, PURE_IRON_INGOT), items=ITEMS)

    _, tool_result = run_single_tool(context, "list_recipes_for_item", {"item": "Iron Ingot"})

    assert [(r["recipe_id"], r["alternate"]) for r in tool_result["recipes"]] == [
        ("Recipe_IngotIron_C", False),
        ("Recipe_Alternate_PureIronIngot_C", True),
    ]
    assert tool_result["recipes"][1]["inputs_per_machine_per_minute"] == {
        "Desc_OreIron_C": 35,
        "Desc_Water_C": 20,
    }


def test_an_id_the_model_made_up_from_a_name_resolves_by_that_name() -> None:
    engine = Item(item_id="Desc_SpaceElevatorPart_4_C", name="Modular Engine")
    context = OrchestratorContext(
        recipes=(
            Recipe(
                recipe_id="Recipe_SpaceElevatorPart_4_C",
                name="Modular Engine",
                building_ids=("Build_ManufacturerMk1_C",),
                inputs=(ItemAmount(item_id="Desc_IronPlate_C", amount_per_minute=2),),
                outputs=(ItemAmount(item_id=engine.item_id, amount_per_minute=1),),
            ),
        ),
        items=(*ITEMS, engine),
    )

    _, seen = run_single_tool(
        context,
        "plan_production",
        {"target_item_id": "Desc_ModularEngine_C", "target_rate_per_minute": 6},
    )

    assert seen["target_item_id"] == "Desc_SpaceElevatorPart_4_C"
