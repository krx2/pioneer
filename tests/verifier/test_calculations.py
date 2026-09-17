"""Tests for the Verifier's pure arithmetic, against a hand-built iron ingot -> iron rod chain
(the same recipe rates as tests/knowledge_base/fixtures/mini_docs.json) — no loader, no Planner,
no Save Parser."""

from dataclasses import replace

import pytest

from pioneer.contracts import (
    Building,
    Coordinates,
    GeneratorFuel,
    Item,
    ItemAmount,
    MaterialFlow,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
    Purity,
    Recipe,
    ResourceNode,
    TransportTier,
)
from pioneer.verifier.calculations import (
    added_machines,
    balance,
    consumption,
    distance,
    extraction_rates,
    extractors_needed,
    generator_byproducts,
    generator_fuel_demand,
    generator_supplemental_demand,
    machine_count,
    minimal_machine_graph,
    placed_generation_capacity_mw,
    placed_power_consumption_mw,
    power_balance,
    power_plants,
    transport_needs,
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
    building_id: str,
    *,
    clock_speed: float = 1.0,
    fuel_item_id: str | None = None,
    resource_node_id: str | None = None,
    is_paused: bool = False,
    production_boost: float = 1.0,
) -> PlacementRecord:
    return PlacementRecord(
        building_id=building_id,
        position=Coordinates(x=0, y=0),
        clock_speed=clock_speed,
        fuel_item_id=fuel_item_id,
        resource_node_id=resource_node_id,
        is_paused=is_paused,
        production_boost=production_boost,
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


_WATER_COOLED = (
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
        building_id="Build_GeneratorNuclear_C",
        name="Nuclear Power Plant",
        power_consumption_mw=-2500,
        input_slots=1,
        output_slots=0,
        fuels=(
            GeneratorFuel(
                fuel_item_id="Desc_NuclearFuelRod_C",
                supplemental_item_id="Desc_Water_C",
                byproduct_item_id="Desc_NuclearWaste_C",
                byproduct_per_fuel_unit=50,
            ),
        ),
        supplemental_per_minute_per_mw=0.096,
    ),
    Building(
        building_id="Build_GeneratorFuel_C",
        name="Fuel Generator",
        power_consumption_mw=-250,
        input_slots=1,
        output_slots=0,
        fuels=(GeneratorFuel(fuel_item_id="Desc_LiquidFuel_C"),),
    ),
)
_FUEL_RODS = (
    *_FUELS,
    Item(item_id="Desc_NuclearFuelRod_C", name="Uranium Fuel Rod", energy_value_mj=750_000),
)


def test_paused_buildings_draw_make_and_burn_nothing() -> None:
    placements = (
        _placed("Build_SmelterMk1_C", is_paused=True),
        _placed("Build_MinerMk1_C"),
        _placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C", is_paused=True),
    )

    assert placed_power_consumption_mw(placements, _PLACED_BUILDINGS) == pytest.approx(5.0)
    assert placed_generation_capacity_mw(placements, _PLACED_BUILDINGS) == 0
    assert generator_fuel_demand(placements, _PLACED_BUILDINGS, _FUELS) == {}
    assert generator_supplemental_demand(placements, _WATER_COOLED) == {}


def test_somersloops_multiply_draw_by_the_boost_squared() -> None:
    draw = placed_power_consumption_mw(
        (_placed("Build_SmelterMk1_C", production_boost=2.0),), _PLACED_BUILDINGS
    )
    assert draw == pytest.approx(16.0)


def test_boost_multiplies_a_nodes_output_but_not_its_input() -> None:
    graph = ProductionGraph(
        nodes=(replace(_graph(smelters=1, constructors=0).nodes[0], production_boost=2.0),),
        flows=(),
    )

    assert balance(graph, RECIPES) == pytest.approx(
        {"Desc_IronIngot_C": 60.0, "Desc_OreIron_C": -30.0}
    )


def test_generators_consume_water_as_in_game() -> None:
    """Coal: 45 m³/min at 100%, half that underclocked to 50%. Nuclear: 240 m³/min."""
    placements = (
        _placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C"),
        _placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C", clock_speed=0.5),
        _placed("Build_GeneratorNuclear_C", fuel_item_id="Desc_NuclearFuelRod_C"),
        _placed("Build_GeneratorFuel_C", fuel_item_id="Desc_LiquidFuel_C"),
    )

    demand = generator_supplemental_demand(placements, _WATER_COOLED)

    assert demand == pytest.approx({"Desc_Water_C": 45 + 22.5 + 240})


def test_unfueled_generators_consume_no_water() -> None:
    placements = (
        _placed("Build_GeneratorCoal_C"),
        _placed("Build_GeneratorCoal_C", fuel_item_id="Desc_NotItsFuel_C"),
    )
    assert generator_supplemental_demand(placements, _WATER_COOLED) == {}


def test_nuclear_waste_accumulates_per_fuel_rod_burned() -> None:
    """2500 MW on 750 000 MJ rods burns 0.2 rods a minute; 50 waste each is 10 a minute."""
    placements = (
        _placed("Build_GeneratorNuclear_C", fuel_item_id="Desc_NuclearFuelRod_C"),
        _placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C"),
    )

    assert generator_byproducts(placements, _WATER_COOLED, _FUEL_RODS) == pytest.approx(
        {"Desc_NuclearWaste_C": 10.0}
    )


def test_consumption_is_gross_demand_by_machine_count() -> None:
    assert consumption(_graph(smelters=1, constructors=3), RECIPES) == pytest.approx(
        {"Desc_OreIron_C": 30.0, "Desc_IronIngot_C": 45.0}
    )


def test_added_machines_keeps_only_what_an_expansion_builds() -> None:
    expanded = ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="smelter_1",
                recipe_id="Recipe_IngotIron_C",
                building_id="Build_SmelterMk1_C",
                machine_count=3,
                is_existing=True,
                existing_machine_count=2,
            ),
            ProductionNode(
                node_id="constructor_1",
                recipe_id="Recipe_IronRod_C",
                building_id="Build_ConstructorMk1_C",
                machine_count=4,
                is_existing=True,
                existing_machine_count=4,
            ),
            ProductionNode(
                node_id="new_constructor",
                recipe_id="Recipe_IronRod_C",
                building_id="Build_ConstructorMk1_C",
                machine_count=2,
            ),
        ),
        flows=(MaterialFlow(item_id="Desc_IronRod_C", amount_per_minute=15),),
    )

    added = added_machines(expanded)

    assert {node.node_id: node.machine_count for node in added.nodes} == {
        "smelter_1": 1,
        "new_constructor": 2,
    }
    assert all(node.existing_machine_count == 0 for node in added.nodes)
    assert added.flows == expanded.flows


def _rod_plan(*, smelters: float, constructors: float) -> ProductionGraph:
    """20 Iron Rod/min, planned in whole machines: 2 constructors (1.33 needed) eat 30 ingots, which
    1 smelter makes (0.67 needed for the 20 ingots 1.33 constructors would eat)."""
    return ProductionGraph(
        nodes=_graph(smelters=smelters, constructors=constructors).nodes,
        flows=(
            MaterialFlow(
                item_id="Desc_OreIron_C", amount_per_minute=30, target_node_id="smelter_1"
            ),
            MaterialFlow(
                item_id="Desc_IronIngot_C",
                amount_per_minute=30,
                source_node_id="smelter_1",
                target_node_id="constructor_1",
            ),
            MaterialFlow(
                item_id="Desc_IronRod_C", amount_per_minute=20, source_node_id="constructor_1"
            ),
        ),
    )


def test_minimal_machine_graph_is_fractional_all_the_way_back() -> None:
    minimal = minimal_machine_graph(_rod_plan(smelters=1, constructors=2), RECIPES)

    counts = {node.node_id: node.machine_count for node in minimal.nodes}
    assert counts == pytest.approx({"constructor_1": 20 / 15, "smelter_1": 20 / 30})


def test_minimal_machine_graph_leaves_nodes_without_outgoing_flows_alone() -> None:
    graph = _graph(smelters=2, constructors=3)
    assert minimal_machine_graph(graph, RECIPES) == graph


_MINER_MK2 = Building(
    building_id="Build_MinerMk2_C",
    name="Miner Mk.2",
    power_consumption_mw=15,
    input_slots=0,
    output_slots=1,
    extraction_rate_per_minute=120,
)
_WATER_EXTRACTOR = Building(
    building_id="Build_WaterPump_C",
    name="Water Extractor",
    power_consumption_mw=20,
    input_slots=0,
    output_slots=1,
    extraction_rate_per_minute=120,
    fixed_resource_id="Desc_Water_C",
)
_NODES = (
    ResourceNode(
        node_id="node_pure_iron",
        item_id="Desc_OreIron_C",
        purity=Purity.PURE,
        position=Coordinates(x=0, y=0),
    ),
    ResourceNode(
        node_id="node_impure_copper",
        item_id="Desc_OreCopper_C",
        purity=Purity.IMPURE,
        position=Coordinates(x=0, y=0),
    ),
)


def test_extraction_rates_scale_with_purity_and_clock_speed() -> None:
    placements = (
        _placed("Build_MinerMk2_C", resource_node_id="node_pure_iron"),  # 120 * 2
        _placed("Build_MinerMk2_C", resource_node_id="node_impure_copper", clock_speed=2.5),
        _placed("Build_WaterPump_C", resource_node_id="Persistent_Level:PersistentLevel.Water1"),
        _placed("Build_SmelterMk1_C"),
    )

    rates = extraction_rates(placements, (_SMELTER, _MINER_MK2, _WATER_EXTRACTOR), _NODES)

    assert rates == pytest.approx(
        {"Desc_OreIron_C": 240.0, "Desc_OreCopper_C": 150.0, "Desc_Water_C": 120.0}
    )


def test_a_miner_on_a_node_the_data_does_not_know_extracts_nothing_known() -> None:
    placements = (_placed("Build_MinerMk2_C", resource_node_id="node_elsewhere"),)

    assert extraction_rates(placements, (_MINER_MK2,), _NODES) == {}


def test_power_plants_cover_the_target_with_whole_generators() -> None:
    """200 MW: three 75 MW coal generators (225 MW) burning 45 coal and drinking 135 m³ a
    minute at full load, or a single 2500 MW nuclear plant."""
    plants = power_plants(200, _WATER_COOLED, _FUEL_RODS)

    by_generator = {(p.generator_id, p.fuel_item_id): p for p in plants}
    coal = by_generator[("Build_GeneratorCoal_C", "Desc_Coal_C")]
    assert (coal.generators, coal.capacity_mw) == (3, 225)
    assert coal.fuel_per_minute == pytest.approx(45.0)
    assert (coal.supplemental_item_id, coal.supplemental_per_minute) == ("Desc_Water_C", 135)
    nuclear = by_generator[("Build_GeneratorNuclear_C", "Desc_NuclearFuelRod_C")]
    assert nuclear.generators == 1
    assert nuclear.fuel_per_minute == pytest.approx(0.2)
    assert nuclear.byproduct_per_minute == pytest.approx(10.0)
    assert [p.generator_id for p in plants] == [  # the closest past 200 MW first
        "Build_GeneratorCoal_C",  # 225 MW
        "Build_GeneratorFuel_C",  # 250 MW
        "Build_GeneratorNuclear_C",
    ]


def test_power_plants_need_fuel_with_known_energy() -> None:
    plants = power_plants(100, _WATER_COOLED, ())

    assert plants == ()


def test_extractors_needed_rounds_up() -> None:
    assert extractors_needed(135, _WATER_EXTRACTOR) == 2
    assert extractors_needed(0, _WATER_EXTRACTOR) == 0
    with pytest.raises(ValueError):
        extractors_needed(10, _SMELTER)


_TIERS = (
    TransportTier(building_id="belt1", name="Mk.1", capacity_per_minute=60, carries_fluids=False),
    TransportTier(building_id="belt2", name="Mk.2", capacity_per_minute=120, carries_fluids=False),
    TransportTier(building_id="pipe1", name="Pipe", capacity_per_minute=300, carries_fluids=True),
)


def test_transport_picks_the_slowest_tier_that_carries_the_flow() -> None:
    flows = (
        MaterialFlow(item_id="Desc_OreIron_C", amount_per_minute=60),
        MaterialFlow(item_id="Desc_OreIron_C", amount_per_minute=61),
        MaterialFlow(item_id="Desc_LiquidFuel_C", amount_per_minute=40),
        MaterialFlow(item_id="Desc_OreIron_C", amount_per_minute=250),
    )

    needs = transport_needs(flows, _FUELS, _TIERS)

    assert [(n.tier.building_id if n.tier else None, n.lines) for n in needs] == [
        ("belt1", 1),
        ("belt2", 1),
        ("pipe1", 1),
        ("belt2", 3),  # faster than any belt: three Mk.2 lines
    ]


def test_transport_without_a_tier_for_the_form_says_so() -> None:
    needs = transport_needs(
        (MaterialFlow(item_id="Desc_LiquidFuel_C", amount_per_minute=40),), _FUELS, _TIERS[:2]
    )

    assert (needs[0].tier, needs[0].lines) == (None, 0)
