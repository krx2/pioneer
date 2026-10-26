"""expand_existing_factory, against a scripted fake model: what extending the player's factory
takes, drawing on its spare output first."""

from tests.game_data import IRON_INGOT, iron_factory
from tests.orchestrator.support import (
    IRON_BUILDINGS,
    ITEMS,
    MINER,
    NODES,
    RECIPES,
    ask,
    placed,
    run_single_tool,
    scripted_tool_calling_llm,
    tool_call,
)

from pioneer.contracts import (
    Coordinates,
    FactorySite,
)
from pioneer.orchestrator.base import OrchestratorContext


def _smelting_site(site_id: str, x: float, smelters: int) -> FactorySite:
    return FactorySite(
        site_id=site_id,
        position=Coordinates(x=x, y=0),
        placements=tuple(
            placed("Build_SmelterMk1_C", recipe_id="Recipe_IngotIron_C") for _ in range(smelters)
        ),
    )


def test_expand_existing_factory_without_save_reports_error_gracefully() -> None:
    llm = scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [
                    tool_call(
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

    result = ask(
        llm,
        "extend my factory to make 20/min Iron Plate",
        OrchestratorContext(recipes=RECIPES),
        response_id="resp-3",
    )

    assert result.graph is None
    assert "don't have your save loaded" in result.chat
    messages = llm.calls[1]["messages"]  # type: ignore[attr-defined]
    tool_result = next(m for m in messages if m["role"] == "tool")
    assert "error" in tool_result["content"]


def test_expansion_draws_on_the_existing_factorys_surplus_first() -> None:
    """2 smelters make 60 ingot/min and 1 constructor eats 30 of them making 20 plates: 30 ingots
    and 20 plates spare. 40 more plates/min then takes just one more constructor -- fed by the
    spare ingots, so no new smelter."""
    context = OrchestratorContext(
        recipes=RECIPES + (IRON_INGOT,),
        items=ITEMS,
        existing_graph=iron_factory(smelters=2, constructors=1),
    )

    result, tool_result = run_single_tool(
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
        recipes=RECIPES + (IRON_INGOT,),
        items=ITEMS,
        existing_graph=iron_factory(smelters=2, constructors=1),
    )

    result, tool_result = run_single_tool(
        context,
        "expand_existing_factory",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 10},
    )

    assert tool_result["changes"] == []
    assert result.graph is None


def test_expansion_names_the_buildings_their_power_and_the_grids_spare() -> None:
    """1 smelter under 2 constructors on one 30 MW burner: 12 MW drawn, 18 spare. 60 more plates
    a minute takes a constructor (4 MW) and a smelter (4 MW)."""
    context = OrchestratorContext(
        recipes=RECIPES + (IRON_INGOT,),
        buildings=IRON_BUILDINGS,
        items=ITEMS,
        existing_graph=iron_factory(smelters=1, constructors=2),
        existing_placements=(
            placed("Build_SmelterMk1_C", recipe_id="Recipe_IngotIron_C"),
            placed("Build_ConstructorMk1_C", recipe_id="Recipe_IronPlate_C"),
            placed("Build_ConstructorMk1_C", recipe_id="Recipe_IronPlate_C"),
            placed("Build_GeneratorBiomass_C"),
        ),
    )

    _, tool_result = run_single_tool(
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


def test_expansion_reports_shortfalls_the_existing_factory_already_has() -> None:
    """1 smelter (30 ingot/min) under 2 constructors (60/min): already 30 ingot/min short."""
    context = OrchestratorContext(
        recipes=RECIPES + (IRON_INGOT,),
        items=ITEMS,
        existing_graph=iron_factory(smelters=1, constructors=2),
    )

    _, tool_result = run_single_tool(
        context,
        "expand_existing_factory",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 60},
    )

    assert tool_result["existing_shortfalls_per_minute"] == {"Desc_IronIngot_C": 30}
    assert {c["recipe_id"]: c["additional_machine_count"] for c in tool_result["changes"]} == {
        "Recipe_IronPlate_C": 1,
        "Recipe_IngotIron_C": 1,
    }


def test_expansion_reports_raw_resources_needed_against_spare_extraction() -> None:
    """A Mk.1 miner on a pure node feeds nothing yet: 120 ore/min spare. 20 plates/min takes one
    new smelter eating 30 of it."""
    context = OrchestratorContext(
        recipes=RECIPES + (IRON_INGOT,),
        buildings=(MINER,),
        items=ITEMS,
        resource_nodes=NODES,
        existing_graph=iron_factory(smelters=0, constructors=0),
        existing_placements=(placed("Build_MinerMk1_C", resource_node_id=NODES[0].node_id),),
    )

    _, tool_result = run_single_tool(
        context,
        "expand_existing_factory",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 20},
    )

    assert tool_result["raw_resources_needed_per_minute"] == {"Desc_OreIron_C": 30}
    assert tool_result["spare_extraction_per_minute"] == {"Desc_OreIron_C": 120}


def test_an_extension_names_the_factory_site_that_runs_the_recipe_most() -> None:
    """60 plates a minute need 90 ingots; 2 smelters spare 60, so 1 more smelter goes to the site
    with the most of them, which is the answer's map. No site makes plates yet."""
    small, big = _smelting_site("site_2", 50_000, 1), _smelting_site("site_1", 0, 3)
    context = OrchestratorContext(
        recipes=RECIPES + (IRON_INGOT,),
        items=ITEMS,
        existing_graph=iron_factory(smelters=2, constructors=0),
        factory_sites=(small, big),
    )

    result, tool_result = run_single_tool(
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
