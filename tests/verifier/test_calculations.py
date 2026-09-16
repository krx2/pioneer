"""Tests for the Verifier's pure arithmetic, against a hand-built iron ingot -> iron rod chain
(the same recipe rates as tests/knowledge_base/fixtures/mini_docs.json) — no loader, no Planner,
no Save Parser."""

import pytest

from pioneer.contracts import (
    Building,
    Coordinates,
    Item,
    ItemAmount,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
    Recipe,
)
from pioneer.verifier.calculations import (
    balance,
    distance,
    generator_fuel_demand,
    machine_count,
    placed_generation_capacity_mw,
    placed_power_consumption_mw,
    power_balance,
)

_SMELTER = Building(
    building_id="Build_SmelterMk1_C",
    name="Smelter",
    power_consumption_mw=4,
    input_slots=1,
    output_slots=1,
)
_CONSTRUCTOR = Building(
    building_id="Build_ConstructorMk1_C",
    name="Constructor",
    power_consumption_mw=4,
    input_slots=1,
    output_slots=1,
)
_RECIPE_INGOT = Recipe(
    recipe_id="Recipe_IngotIron_C",
    name="Iron Ingot",
    building_ids=("Build_SmelterMk1_C",),
    inputs=(ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
)
_RECIPE_ROD = Recipe(
    recipe_id="Recipe_IronRod_C",
    name="Iron Rod",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=15),),
    outputs=(ItemAmount(item_id="Desc_IronRod_C", amount_per_minute=15),),
)
RECIPES = (_RECIPE_INGOT, _RECIPE_ROD)
BUILDINGS = (_SMELTER, _CONSTRUCTOR)

# Rated values as the game's own export lists them.
_PLACED_BUILDINGS = (
    _SMELTER,
    Building(
        building_id="Build_MinerMk1_C",
        name="Miner Mk.1",
        power_consumption_mw=5,
        input_slots=0,
        output_slots=1,
    ),
    Building(
        building_id="Build_GeneratorCoal_C",
        name="Coal Generator",
        power_consumption_mw=-75,
        input_slots=1,
        output_slots=0,
    ),
    Building(
        building_id="Build_GeneratorFuel_C",
        name="Fuel Generator",
        power_consumption_mw=-250,
        input_slots=1,
        output_slots=0,
    ),
)
_FUELS = (
    Item(item_id="Desc_Coal_C", name="Coal", is_raw_resource=True, energy_value_mj=300),
    Item(item_id="Desc_LiquidFuel_C", name="Fuel", is_fluid=True, energy_value_mj=750),
)


def _graph(*, smelters: float, constructors: float) -> ProductionGraph:
    return ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="smelter_1",
                recipe_id="Recipe_IngotIron_C",
                building_id="Build_SmelterMk1_C",
                machine_count=smelters,
            ),
            ProductionNode(
                node_id="constructor_1",
                recipe_id="Recipe_IronRod_C",
                building_id="Build_ConstructorMk1_C",
                machine_count=constructors,
            ),
        ),
        flows=(),
    )


def _placed(
    building_id: str, *, clock_speed: float = 1.0, fuel_item_id: str | None = None
) -> PlacementRecord:
    return PlacementRecord(
        building_id=building_id,
        position=Coordinates(x=0, y=0),
        clock_speed=clock_speed,
        fuel_item_id=fuel_item_id,
    )


def test_distance_3_4_5_triangle() -> None:
    assert distance(Coordinates(x=0, y=0), Coordinates(x=3, y=4)) == pytest.approx(5.0)


def test_distance_along_z_axis() -> None:
    assert distance(Coordinates(x=0, y=0, z=0), Coordinates(x=0, y=0, z=5)) == pytest.approx(5.0)


def test_distance_same_point_is_zero() -> None:
    p = Coordinates(x=1, y=2, z=3)
    assert distance(p, p) == 0.0


def test_machine_count_rounds_up() -> None:
    assert machine_count(_RECIPE_ROD, target_rate=100) == 7  # 15/min * 7 = 105 >= 100


def test_machine_count_exact_multiple() -> None:
    assert machine_count(_RECIPE_ROD, target_rate=45) == 3


def test_machine_count_zero_or_negative_target_needs_no_machines() -> None:
    assert machine_count(_RECIPE_ROD, target_rate=0) == 0
    assert machine_count(_RECIPE_ROD, target_rate=-10) == 0


def test_balance_perfectly_consumed_chain() -> None:
    # 1 smelter (30 ingot/min) feeding 2 constructors (2*15 = 30 ingot/min consumed).
    result = balance(_graph(smelters=1, constructors=2), RECIPES)
    assert result["Desc_OreIron_C"] == pytest.approx(-30.0)  # raw input, deficit
    assert result["Desc_IronIngot_C"] == pytest.approx(0.0)  # fully consumed internally
    assert result["Desc_IronRod_C"] == pytest.approx(30.0)  # final output, surplus


def test_balance_reports_surplus_when_underconsumed() -> None:
    # 1 smelter (30 ingot/min) feeding only 1 constructor (15 ingot/min consumed).
    result = balance(_graph(smelters=1, constructors=1), RECIPES)
    assert result["Desc_IronIngot_C"] == pytest.approx(15.0)


def test_balance_unknown_recipe_raises() -> None:
    graph = ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="n1",
                recipe_id="does_not_exist",
                building_id="Build_SmelterMk1_C",
                machine_count=1,
            ),
        ),
        flows=(),
    )
    with pytest.raises(ValueError, match="does_not_exist"):
        balance(graph, RECIPES)


def test_power_balance_sums_by_machine_count() -> None:
    result = power_balance(_graph(smelters=1, constructors=2), BUILDINGS)
    assert result == pytest.approx(4.0 * 1 + 4.0 * 2)


def test_power_balance_unknown_building_raises() -> None:
    graph = ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="n1",
                recipe_id="Recipe_IngotIron_C",
                building_id="does_not_exist",
                machine_count=1,
            ),
        ),
        flows=(),
    )
    with pytest.raises(ValueError, match="does_not_exist"):
        power_balance(graph, BUILDINGS)


def test_placed_power_consumption_counts_consumers_only() -> None:
    placements = (
        _placed("Build_SmelterMk1_C"),
        _placed("Build_MinerMk1_C"),
        _placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C"),
    )
    assert placed_power_consumption_mw(placements, _PLACED_BUILDINGS) == pytest.approx(9.0)


def test_overclocking_raises_draw_faster_than_linearly() -> None:
    draw = placed_power_consumption_mw(
        (_placed("Build_MinerMk1_C", clock_speed=2.5),), _PLACED_BUILDINGS
    )
    assert draw == pytest.approx(5 * 2.5**1.321929)  # ~16.8 MW, not 12.5


def test_placed_buildings_no_list_knows_are_skipped_rather_than_raising() -> None:
    placements = (_placed("Build_ConveyorBeltMk1_C"), _placed("Build_SmelterMk1_C"))
    assert placed_power_consumption_mw(placements, _PLACED_BUILDINGS) == pytest.approx(4.0)


def test_generation_capacity_scales_linearly_with_clock_speed() -> None:
    placements = (
        _placed("Build_GeneratorCoal_C"),
        _placed("Build_GeneratorFuel_C", clock_speed=0.5),
        _placed("Build_SmelterMk1_C"),
    )
    assert placed_generation_capacity_mw(placements, _PLACED_BUILDINGS) == pytest.approx(75 + 125)


def test_generator_fuel_demand_matches_in_game_burn_rates() -> None:
    placements = (
        _placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C"),
        _placed("Build_GeneratorFuel_C", fuel_item_id="Desc_LiquidFuel_C"),
        _placed("Build_GeneratorFuel_C", fuel_item_id="Desc_LiquidFuel_C", clock_speed=0.5),
    )

    demand = generator_fuel_demand(placements, _PLACED_BUILDINGS, _FUELS)

    # Coal Generator: 15 coal/min. Fuel Generator: 20 m³/min, 10 at half clock.
    assert demand == pytest.approx({"Desc_Coal_C": 15.0, "Desc_LiquidFuel_C": 30.0})


def test_unfueled_generators_and_unknown_fuels_burn_nothing() -> None:
    placements = (
        _placed("Build_GeneratorCoal_C"),
        _placed("Build_GeneratorFuel_C", fuel_item_id="Desc_Mystery_C"),
    )
    assert generator_fuel_demand(placements, _PLACED_BUILDINGS, _FUELS) == {}
