"""Tests for the production planner, against a hand-written iron-chain recipe set (own fixture,
not knowledge_base's `docs/en-US.json` or its loader) covering the Reinforced Iron Plate chain
from the source deck — chosen because Iron Ingot demand converges from two branches (Iron Plate
and, via Screw, Iron Rod), which is exactly what demand aggregation needs to get right."""

import pytest

from pioneer.contracts import ItemAmount, MaterialFlow, ProductionNode, Recipe
from pioneer.production_planner.planner import plan_production, recipes_for_output

_IRON_INGOT = Recipe(
    recipe_id="Recipe_IngotIron_C",
    name="Iron Ingot",
    building_ids=("Build_SmelterMk1_C",),
    inputs=(ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
)
_IRON_PLATE = Recipe(
    recipe_id="Recipe_IronPlate_C",
    name="Iron Plate",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronPlate_C", amount_per_minute=20),),
)
_IRON_ROD = Recipe(
    recipe_id="Recipe_IronRod_C",
    name="Iron Rod",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=15),),
    outputs=(ItemAmount(item_id="Desc_IronRod_C", amount_per_minute=15),),
)
_SCREW = Recipe(
    recipe_id="Recipe_Screw_C",
    name="Screw",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronRod_C", amount_per_minute=10),),
    outputs=(ItemAmount(item_id="Desc_Screw_C", amount_per_minute=40),),
)
_REINFORCED_IRON_PLATE = Recipe(
    recipe_id="Recipe_IronPlateReinforced_C",
    name="Reinforced Iron Plate",
    building_ids=("Build_AssemblerMk1_C",),
    inputs=(
        ItemAmount(item_id="Desc_IronPlate_C", amount_per_minute=30),
        ItemAmount(item_id="Desc_Screw_C", amount_per_minute=60),
    ),
    outputs=(ItemAmount(item_id="Desc_IronPlateReinforced_C", amount_per_minute=5),),
)
_IRON_INGOT_PURE = Recipe(
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

RECIPES = (_IRON_INGOT, _IRON_PLATE, _IRON_ROD, _SCREW, _REINFORCED_IRON_PLATE)

# A refinery recipe, plus the Packager's pack/unpack pair that loops Fuel back onto itself.
_FUEL = Recipe(
    recipe_id="Recipe_LiquidFuel_C",
    name="Fuel",
    building_ids=("Build_OilRefinery_C",),
    inputs=(ItemAmount(item_id="Desc_LiquidOil_C", amount_per_minute=60),),
    outputs=(
        ItemAmount(item_id="Desc_LiquidFuel_C", amount_per_minute=40),
        ItemAmount(item_id="Desc_PolymerResin_C", amount_per_minute=30),
    ),
)
_UNPACKAGE_FUEL = Recipe(
    recipe_id="Recipe_UnpackageFuel_C",
    name="Unpackage Fuel",
    building_ids=("Build_Packager_C",),
    inputs=(ItemAmount(item_id="Desc_Fuel_C", amount_per_minute=60),),
    outputs=(
        ItemAmount(item_id="Desc_LiquidFuel_C", amount_per_minute=60),
        ItemAmount(item_id="Desc_FluidCanister_C", amount_per_minute=60),
    ),
)
_PACKAGED_FUEL = Recipe(
    recipe_id="Recipe_Fuel_C",
    name="Packaged Fuel",
    building_ids=("Build_Packager_C",),
    inputs=(
        ItemAmount(item_id="Desc_LiquidFuel_C", amount_per_minute=40),
        ItemAmount(item_id="Desc_FluidCanister_C", amount_per_minute=40),
    ),
    outputs=(ItemAmount(item_id="Desc_Fuel_C", amount_per_minute=40),),
)
_FUEL_RECIPES = (_UNPACKAGE_FUEL, _PACKAGED_FUEL, _FUEL)


def _node(graph, item_id: str) -> ProductionNode:
    return next(n for n in graph.nodes if n.node_id == f"node_{item_id}")


def _flow(graph, item_id: str, target_item_id: str | None) -> MaterialFlow:
    target_node = f"node_{target_item_id}" if target_item_id else None
    return next(f for f in graph.flows if f.item_id == item_id and f.target_node_id == target_node)


def test_reinforced_iron_plate_chain_machine_counts() -> None:
    graph = plan_production("Desc_IronPlateReinforced_C", 10, RECIPES)

    assert len(graph.nodes) == 5
    assert _node(graph, "Desc_IronPlateReinforced_C").machine_count == 2
    assert _node(graph, "Desc_IronPlate_C").machine_count == 3
    assert _node(graph, "Desc_Screw_C").machine_count == 3
    assert _node(graph, "Desc_IronRod_C").machine_count == 2
    # Converges from both the Iron Plate branch (30*3=90) and the Iron Rod branch (15*2=30).
    assert _node(graph, "Desc_IronIngot_C").machine_count == 4


def test_reinforced_iron_plate_chain_node_fields() -> None:
    graph = plan_production("Desc_IronPlateReinforced_C", 10, RECIPES)

    ingot_node = _node(graph, "Desc_IronIngot_C")
    assert ingot_node.recipe_id == "Recipe_IngotIron_C"
    assert ingot_node.building_id == "Build_SmelterMk1_C"
    assert ingot_node.is_existing is False


def test_reinforced_iron_plate_chain_flows() -> None:
    graph = plan_production("Desc_IronPlateReinforced_C", 10, RECIPES)

    assert len(graph.flows) == 7
    assert _flow(graph, "Desc_OreIron_C", "Desc_IronIngot_C") == MaterialFlow(
        item_id="Desc_OreIron_C",
        amount_per_minute=120,
        source_node_id=None,
        target_node_id="node_Desc_IronIngot_C",
    )
    ingot_to_plate = _flow(graph, "Desc_IronIngot_C", "Desc_IronPlate_C")
    ingot_to_rod = _flow(graph, "Desc_IronIngot_C", "Desc_IronRod_C")
    plate_to_rip = _flow(graph, "Desc_IronPlate_C", "Desc_IronPlateReinforced_C")
    screw_to_rip = _flow(graph, "Desc_Screw_C", "Desc_IronPlateReinforced_C")
    assert ingot_to_plate.amount_per_minute == pytest.approx(90)
    assert ingot_to_rod.amount_per_minute == pytest.approx(30)
    assert plate_to_rip.amount_per_minute == pytest.approx(60)
    assert screw_to_rip.amount_per_minute == pytest.approx(120)
    final_output = _flow(graph, "Desc_IronPlateReinforced_C", None)
    assert final_output.amount_per_minute == 10
    assert final_output.source_node_id == "node_Desc_IronPlateReinforced_C"
    assert final_output.target_node_id is None


def test_machine_counts_round_up() -> None:
    # 1/min of Reinforced Iron Plate: ceil(1/5)=1 machine, actually overproducing 5/min.
    graph = plan_production("Desc_IronPlateReinforced_C", 1, RECIPES)
    assert _node(graph, "Desc_IronPlateReinforced_C").machine_count == 1


def test_available_supply_is_used_before_planning_machines() -> None:
    """5/min Reinforced Iron Plate against a factory with 30/min of spare Iron Plate: the surplus
    covers every plate the assembler needs, so no plate constructors -- and no smelters for them."""
    graph = plan_production(
        "Desc_IronPlateReinforced_C", 5, RECIPES, available_supply={"Desc_IronPlate_C": 30}
    )

    assert {n.recipe_id: n.machine_count for n in graph.nodes} == {
        "Recipe_IronPlateReinforced_C": 1,
        "Recipe_Screw_C": 2,
        "Recipe_IronRod_C": 2,
        "Recipe_IngotIron_C": 1,
    }
    assert _flow(graph, "Desc_IronPlate_C", "Desc_IronPlateReinforced_C") == MaterialFlow(
        item_id="Desc_IronPlate_C",
        amount_per_minute=30,
        source_node_id=None,
        target_node_id="node_Desc_IronPlateReinforced_C",
    )


def test_partial_supply_splits_a_flow_between_supply_and_new_machines() -> None:
    # 40 plates -> 2 constructors eating 60 ingot/min; 15 of those come from supply.
    graph = plan_production(
        "Desc_IronPlate_C",
        40,
        (_IRON_INGOT, _IRON_PLATE),
        available_supply={"Desc_IronIngot_C": 15},
    )

    assert _node(graph, "Desc_IronIngot_C").machine_count == 2  # ceil(45 / 30)
    into_plates = sorted(
        (f.source_node_id or "", f.amount_per_minute)
        for f in graph.flows
        if f.item_id == "Desc_IronIngot_C"
    )
    assert into_plates == [("", 15), ("node_Desc_IronIngot_C", 45)]


def test_target_wholly_covered_by_supply_needs_no_machines() -> None:
    graph = plan_production(
        "Desc_IronPlate_C",
        20,
        (_IRON_INGOT, _IRON_PLATE),
        available_supply={"Desc_IronPlate_C": 50},
    )

    assert graph.nodes == ()
    assert graph.flows == (MaterialFlow(item_id="Desc_IronPlate_C", amount_per_minute=20),)


def test_target_with_no_recipe_raises() -> None:
    with pytest.raises(ValueError, match="Desc_OreIron_C"):
        plan_production("Desc_OreIron_C", 100, RECIPES)


def test_raw_resource_target_raises() -> None:
    with pytest.raises(ValueError, match="raw resource"):
        plan_production("Desc_OreIron_C", 60, RECIPES, raw_item_ids={"Desc_OreIron_C"})


def test_expansion_stops_at_raw_resources_even_when_a_recipe_makes_them() -> None:
    """1.0's Converter makes Iron Ore out of Limestone (and Limestone out of Sulfur, and so on).
    Declared raw, ore is extracted -- never crafted."""
    ore_from_limestone = Recipe(
        recipe_id="Recipe_Iron_Limestone_C",
        name="Iron Ore (Limestone)",
        building_ids=("Build_Converter_C",),
        inputs=(ItemAmount(item_id="Desc_Stone_C", amount_per_minute=120),),
        outputs=(ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=60),),
    )
    graph = plan_production(
        "Desc_IronPlate_C",
        20,
        (ore_from_limestone, _IRON_INGOT, _IRON_PLATE),
        raw_item_ids={"Desc_OreIron_C", "Desc_Stone_C"},
    )

    assert {n.recipe_id for n in graph.nodes} == {"Recipe_IronPlate_C", "Recipe_IngotIron_C"}
    assert _flow(graph, "Desc_OreIron_C", "Desc_IronIngot_C").source_node_id is None


def test_recipes_for_output_returns_all_alternates() -> None:
    recipes = (_IRON_INGOT, _IRON_INGOT_PURE)
    result = recipes_for_output("Desc_IronIngot_C", recipes)
    assert set(result) == {_IRON_INGOT, _IRON_INGOT_PURE}


def test_recipes_for_output_unknown_item_is_empty() -> None:
    assert recipes_for_output("nonexistent", RECIPES) == ()


def test_standard_recipe_is_preferred_over_an_alternate_listed_first() -> None:
    recipes = (_IRON_INGOT_PURE, _IRON_INGOT, _IRON_PLATE)
    graph = plan_production("Desc_IronPlate_C", 20, recipes)
    assert _node(graph, "Desc_IronIngot_C").recipe_id == "Recipe_IngotIron_C"


def test_alternate_is_used_when_an_item_has_no_standard_recipe() -> None:
    graph = plan_production("Desc_IronPlate_C", 20, (_IRON_INGOT_PURE, _IRON_PLATE))
    assert _node(graph, "Desc_IronIngot_C").recipe_id == "Recipe_Alternate_PureIronIngot_C"


def test_alternates_are_the_fallback_when_every_standard_recipe_loops() -> None:
    """1.0's Turbofuel needs Compacted Coal, whose only standard source is a byproduct of Rocket
    Fuel -- made from Turbofuel. Only "Alternate: Compacted Coal" (Coal + Sulfur) breaks that."""
    turbofuel = Recipe(
        recipe_id="Recipe_Alternate_Turbofuel_C",
        name="Turbofuel",
        building_ids=("Build_OilRefinery_C",),
        inputs=(
            ItemAmount(item_id="Desc_LiquidFuel_C", amount_per_minute=22.5),
            ItemAmount(item_id="Desc_CompactedCoal_C", amount_per_minute=15),
        ),
        outputs=(ItemAmount(item_id="Desc_LiquidTurboFuel_C", amount_per_minute=18.75),),
    )
    rocket_fuel = Recipe(
        recipe_id="Recipe_RocketFuel_C",
        name="Rocket Fuel",
        building_ids=("Build_Blender_C",),
        inputs=(
            ItemAmount(item_id="Desc_LiquidTurboFuel_C", amount_per_minute=60),
            ItemAmount(item_id="Desc_NitricAcid_C", amount_per_minute=10),
        ),
        outputs=(
            ItemAmount(item_id="Desc_RocketFuel_C", amount_per_minute=100),
            ItemAmount(item_id="Desc_CompactedCoal_C", amount_per_minute=10),
        ),
    )
    compacted_coal = Recipe(
        recipe_id="Recipe_Alternate_EnrichedCoal_C",
        name="Alternate: Compacted Coal",
        building_ids=("Build_AssemblerMk1_C",),
        inputs=(
            ItemAmount(item_id="Desc_Coal_C", amount_per_minute=25),
            ItemAmount(item_id="Desc_Sulfur_C", amount_per_minute=25),
        ),
        outputs=(ItemAmount(item_id="Desc_CompactedCoal_C", amount_per_minute=25),),
        is_alternate=True,
    )

    graph = plan_production(
        "Desc_LiquidTurboFuel_C", 18.75, (turbofuel, rocket_fuel, compacted_coal, _FUEL)
    )

    assert _node(graph, "Desc_CompactedCoal_C").recipe_id == "Recipe_Alternate_EnrichedCoal_C"
    assert "Recipe_RocketFuel_C" not in {n.recipe_id for n in graph.nodes}


def test_primary_output_recipe_is_preferred_over_a_byproduct_one() -> None:
    resin_first = Recipe(
        recipe_id="Recipe_PolymerResin_C",
        name="Polymer Resin",
        building_ids=("Build_OilRefinery_C",),
        inputs=(ItemAmount(item_id="Desc_LiquidOil_C", amount_per_minute=60),),
        outputs=(
            ItemAmount(item_id="Desc_PolymerResin_C", amount_per_minute=130),
            ItemAmount(item_id="Desc_HeavyOilResidue_C", amount_per_minute=20),
        ),
    )
    graph = plan_production("Desc_PolymerResin_C", 130, (_FUEL, resin_first))
    assert _node(graph, "Desc_PolymerResin_C").recipe_id == "Recipe_PolymerResin_C"


def test_a_candidate_that_loops_back_is_skipped_for_the_next_one() -> None:
    """Unpackaging Fuel needs Packaged Fuel, which is made from Fuel: taking it would loop, so the
    refinery recipe is used instead -- even though unpackaging is listed first."""
    graph = plan_production("Desc_LiquidFuel_C", 40, _FUEL_RECIPES)
    assert [n.recipe_id for n in graph.nodes] == ["Recipe_LiquidFuel_C"]


def test_packaging_plans_through_the_loop_free_direction() -> None:
    """Packaged Fuel needs Fuel (refinery, not unpackaging) and Empty Canisters -- which come from
    their own recipe, not as the byproduct of unpackaging the very Packaged Fuel being planned."""
    empty_canister = Recipe(
        recipe_id="Recipe_FluidCanister_C",
        name="Empty Canister",
        building_ids=("Build_ConstructorMk1_C",),
        inputs=(ItemAmount(item_id="Desc_Plastic_C", amount_per_minute=30),),
        outputs=(ItemAmount(item_id="Desc_FluidCanister_C", amount_per_minute=60),),
    )
    graph = plan_production("Desc_Fuel_C", 40, _FUEL_RECIPES + (empty_canister,))
    assert {n.recipe_id for n in graph.nodes} == {
        "Recipe_Fuel_C",
        "Recipe_LiquidFuel_C",
        "Recipe_FluidCanister_C",
    }


def test_recipe_choices_overrides_the_default() -> None:
    recipes = (_IRON_INGOT, _IRON_INGOT_PURE, _IRON_PLATE)
    graph = plan_production(
        "Desc_IronPlate_C",
        20,
        recipes,
        recipe_choices={"Desc_IronIngot_C": "Recipe_Alternate_PureIronIngot_C"},
    )
    ingot_node = _node(graph, "Desc_IronIngot_C")
    assert ingot_node.recipe_id == "Recipe_Alternate_PureIronIngot_C"
    # Pure Iron Ingot needs Water too, on top of Iron Ore.
    assert any(f.item_id == "Desc_Water_C" for f in graph.flows)


def test_invalid_recipe_choice_raises() -> None:
    with pytest.raises(ValueError, match="does_not_exist"):
        plan_production(
            "Desc_IronPlate_C",
            20,
            (_IRON_INGOT, _IRON_PLATE),
            recipe_choices={"Desc_IronIngot_C": "does_not_exist"},
        )


def test_an_explicit_recipe_choice_that_loops_raises() -> None:
    with pytest.raises(ValueError, match="cyclic"):
        plan_production(
            "Desc_LiquidFuel_C",
            40,
            _FUEL_RECIPES,
            recipe_choices={"Desc_LiquidFuel_C": "Recipe_UnpackageFuel_C"},
        )


def test_cyclic_recipe_dependency_raises() -> None:
    a = Recipe(
        recipe_id="A",
        name="A",
        building_ids=("b",),
        inputs=(ItemAmount(item_id="item_b", amount_per_minute=1),),
        outputs=(ItemAmount(item_id="item_a", amount_per_minute=1),),
    )
    b = Recipe(
        recipe_id="B",
        name="B",
        building_ids=("b",),
        inputs=(ItemAmount(item_id="item_a", amount_per_minute=1),),
        outputs=(ItemAmount(item_id="item_b", amount_per_minute=1),),
    )
    with pytest.raises(ValueError, match="cyclic"):
        plan_production("item_a", 10, (a, b))
