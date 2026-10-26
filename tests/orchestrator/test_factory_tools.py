"""The tools over what the player has built, against a scripted fake model: show_existing_factory,
factory_item_balance, factory_power and diagnose_factory_problems."""

import json
from dataclasses import replace

from tests.game_data import FUEL, IRON_INGOT
from tests.orchestrator.support import (
    COAL_ITEMS,
    COAL_POWER,
    FUEL_ITEMS,
    IRON_BUILDINGS,
    ITEMS,
    MINER,
    NODES,
    POWER_BUILDINGS,
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
    ItemAmount,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
    Recipe,
    TransportLink,
    TransportTier,
)
from pioneer.orchestrator import site_balance
from pioneer.orchestrator.base import OrchestratorContext


def _built_at(site_x: float, recipe_id: str, building_id: str, clock: float) -> PlacementRecord:
    return PlacementRecord(
        building_id=building_id,
        position=Coordinates(x=site_x, y=0),
        recipe_id=recipe_id,
        clock_speed=clock,
    )


_SMELTING_AND_PLATES = FactorySite(
    site_id="site_1",
    position=Coordinates(x=0, y=0),
    placements=(
        _built_at(0, "Recipe_IngotIron_C", "Build_SmelterMk1_C", 1.0),
        _built_at(0, "Recipe_IngotIron_C", "Build_SmelterMk1_C", 1.0),
        _built_at(0, "Recipe_IronPlate_C", "Build_ConstructorMk1_C", 0.5),
    ),
)

_MORE_PLATES = FactorySite(
    site_id="site_2",
    position=Coordinates(x=90_000, y=0),
    placements=(_built_at(90_000, "Recipe_IronPlate_C", "Build_ConstructorMk1_C", 1.0),),
)


def _built_factory() -> OrchestratorContext:
    return OrchestratorContext(
        recipes=(*RECIPES, IRON_INGOT),
        items=ITEMS,
        existing_graph=ProductionGraph(
            nodes=(
                ProductionNode(
                    "save_Recipe_IngotIron_C",
                    "Recipe_IngotIron_C",
                    "Build_SmelterMk1_C",
                    2.0,
                    is_existing=True,
                    existing_machine_count=2.0,
                ),
                ProductionNode(
                    "save_Recipe_IronPlate_C",
                    "Recipe_IronPlate_C",
                    "Build_ConstructorMk1_C",
                    1.5,
                    is_existing=True,
                    existing_machine_count=1.5,
                ),
            ),
            flows=(),
        ),
        existing_placements=_SMELTING_AND_PLATES.placements + _MORE_PLATES.placements,
        factory_sites=(_SMELTING_AND_PLATES, _MORE_PLATES),
    )


def _at(object_id: str, building_id: str, recipe_id: str | None = None) -> PlacementRecord:
    return PlacementRecord(
        building_id=building_id,
        position=Coordinates(x=0, y=0),
        recipe_id=recipe_id,
        object_id=object_id,
    )


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
    llm = scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [tool_call("call_1", "diagnose_factory_problems", {})],
            },
            {"content": "Your Iron Ingot supply is short.", "tool_calls": []},
        ]
    )

    ask(
        llm,
        "what's wrong with my factory?",
        OrchestratorContext(recipes=RECIPES + (IRON_INGOT,), existing_graph=graph),
        response_id="resp-6",
    )

    messages = llm.calls[1]["messages"]  # type: ignore[attr-defined]
    tool_result = next(m for m in messages if m["role"] == "tool")
    assert "resource_deficit" in tool_result["content"]
    assert "Desc_IronIngot_C" in tool_result["content"]


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
        recipes=(FUEL,),
        buildings=POWER_BUILDINGS,
        items=FUEL_ITEMS,
        existing_graph=ProductionGraph(nodes=(refinery_node,), flows=()),
        existing_placements=(
            placed("Build_OilRefinery_C", recipe_id="Recipe_LiquidFuel_C"),
            placed("Build_GeneratorFuel_C", fuel_item_id="Desc_LiquidFuel_C"),
            placed("Build_GeneratorFuel_C", fuel_item_id="Desc_LiquidFuel_C"),
        ),
    )

    _, tool_result = run_single_tool(context, "diagnose_factory_problems", {})

    assert tool_result["power_draw_mw"] == 30
    assert tool_result["power_capacity_mw"] == 500
    flagged = {anomaly["item_id"] for anomaly in tool_result["anomalies"]}
    assert "Desc_LiquidFuel_C" not in flagged
    assert "Desc_LiquidOil_C" not in flagged


def test_diagnosis_counts_the_water_coal_generators_drink() -> None:
    """Two coal generators take 90 m³ of water a minute, which one extractor at 75% supplies
    exactly -- the water isn't overproduced, as it would be if only fuel were counted."""
    context = OrchestratorContext(
        buildings=COAL_POWER,
        items=COAL_ITEMS,
        existing_graph=ProductionGraph(nodes=(), flows=()),
        existing_placements=(
            placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C"),
            placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C"),
            placed("Build_WaterPump_C", clock_speed=0.75),
        ),
    )

    _, tool_result = run_single_tool(context, "diagnose_factory_problems", {})

    assert "Desc_Water_C" not in {anomaly["item_id"] for anomaly in tool_result["anomalies"]}


def test_diagnosis_rates_a_water_shortfall_against_what_the_generators_drink() -> None:
    """Three coal generators need 135 m³/min, one extractor gives 120: 15 short of 135 is minor,
    and no single machine is to blame. (Raw supply is only judged with node data loaded.)"""
    context = OrchestratorContext(
        buildings=COAL_POWER,
        items=COAL_ITEMS,
        resource_nodes=NODES,
        existing_graph=ProductionGraph(nodes=(), flows=()),
        existing_placements=(
            *(placed("Build_GeneratorCoal_C", fuel_item_id="Desc_Coal_C") for _ in range(3)),
            placed("Build_WaterPump_C"),
        ),
    )

    _, tool_result = run_single_tool(context, "diagnose_factory_problems", {})

    (water,) = [a for a in tool_result["anomalies"] if a["item_id"] == "Desc_Water_C"]
    assert water["kind"] == "resource_deficit"
    assert water["severity"] == "low"
    assert water["node_id"] is None


def test_diagnosis_flags_a_blackout_judged_from_the_placed_buildings() -> None:
    context = OrchestratorContext(
        recipes=(FUEL,),
        buildings=POWER_BUILDINGS,
        items=FUEL_ITEMS,
        existing_graph=ProductionGraph(nodes=(), flows=()),
        existing_placements=tuple(placed("Build_OilRefinery_C") for _ in range(10)),
    )

    _, tool_result = run_single_tool(context, "diagnose_factory_problems", {})

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

    _, tool_result = run_single_tool(
        OrchestratorContext(recipes=(biomass,), existing_graph=graph),
        "diagnose_factory_problems",
        {},
    )

    assert "Desc_Leaves_C" not in {anomaly["item_id"] for anomaly in tool_result["anomalies"]}


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
        recipes=(IRON_INGOT,),
        buildings=(MINER,),
        items=ITEMS,
        resource_nodes=NODES,
        existing_graph=ProductionGraph(nodes=(smelters,), flows=()),
        existing_placements=(placed("Build_MinerMk1_C", resource_node_id=NODES[0].node_id),),
    )

    _, tool_result = run_single_tool(context, "diagnose_factory_problems", {})

    ore = [a for a in tool_result["anomalies"] if a["item_id"] == "Desc_OreIron_C"]
    assert [anomaly["kind"] for anomaly in ore] == ["resource_deficit"]
    assert "short by 30/min" in ore[0]["description"]
    assert ore[0]["severity"] == "low"  # 30 of the 150 the smelters eat
    assert ore[0]["node_id"] == "save_Recipe_IngotIron_C"


def test_one_factory_is_drawn_with_the_flows_its_recipes_imply() -> None:
    result, seen = run_single_tool(_built_factory(), "show_existing_factory", {"site_id": "1"})

    assert seen["drawn"] == "site_1"
    assert [stage["effective_machines"] for stage in seen["stages"]] == [2.0, 0.5]
    assert seen["stages"][1]["makes_per_minute"] == {"Desc_IronPlate_C": 10}
    assert seen["stages"][1]["uses_per_minute"] == {"Desc_IronIngot_C": 15}
    assert seen["raw_resources_in_per_minute"] == {"Desc_OreIron_C": 60}
    assert seen["parts_brought_in_per_minute"] == {}
    assert seen["goes_out_per_minute"] == {"Desc_IronIngot_C": 45, "Desc_IronPlate_C": 10}
    assert all(node.is_existing for node in result.graph.nodes)
    assert result.factory_sites == (_SMELTING_AND_PLATES,)  # the map shows where it stands


def test_the_chain_behind_one_item_is_drawn_across_every_factory() -> None:
    result, seen = run_single_tool(
        _built_factory(), "show_existing_factory", {"item_id": "Iron Plate"}
    )

    assert seen["drawn"] == "Desc_IronPlate_C"
    assert {node.recipe_id for node in result.graph.nodes} == {
        "Recipe_IngotIron_C",
        "Recipe_IronPlate_C",
    }
    assert seen["goes_out_per_minute"] == {"Desc_IronIngot_C": 15, "Desc_IronPlate_C": 30}


def test_a_factory_too_big_to_draw_is_offered_as_a_list_of_its_sites(monkeypatch) -> None:
    monkeypatch.setattr("pioneer.orchestrator.factory_tools._MAX_DRAWN_STAGES", 1)

    result, seen = run_single_tool(_built_factory(), "show_existing_factory", {})

    assert result.graph is None
    assert "too many to draw" in seen["not_drawn"]
    assert [site["site_id"] for site in seen["factories"]] == ["site_1", "site_2"]


def test_showing_the_factory_needs_a_save_and_a_real_site() -> None:
    _, no_save = run_single_tool(OrchestratorContext(recipes=RECIPES), "show_existing_factory", {})
    result, unknown = run_single_tool(
        _built_factory(), "show_existing_factory", {"site_id": "site_9"}
    )

    assert "no save loaded" in no_save["error"]
    assert unknown["error"] == "no factory 'site_9'"
    assert [site["site_id"] for site in unknown["factories"]] == ["site_1", "site_2"]
    assert result.graph is None


def test_a_drawing_follows_the_belts_when_the_save_has_them() -> None:
    """The plate constructor at site_1 is belted to nothing there: its ingots come from elsewhere,
    and the smelters' ingots leave -- however well the recipes alone would pair them."""
    smelters = [_at(f"s{i}", "Build_SmelterMk1_C", "Recipe_IngotIron_C") for i in (1, 2)]
    plates = _at("p1", "Build_ConstructorMk1_C", "Recipe_IronPlate_C")
    site = replace(_SMELTING_AND_PLATES, placements=(*smelters, plates))
    context = replace(
        _built_factory(),
        factory_sites=(site,),
        existing_placements=site.placements,
        existing_links=(TransportLink("elsewhere", "p1", "belt"),),
    )

    result, seen = run_single_tool(context, "show_existing_factory", {"site_id": "site_1"})

    assert seen["flows_follow"] == "the save's belts and pipes"
    ingots = {
        (flow.source_node_id, flow.target_node_id): flow.amount_per_minute
        for flow in result.graph.flows
        if flow.item_id == "Desc_IronIngot_C"
    }
    assert ingots == {("save_Recipe_IngotIron_C", None): 60, (None, "save_Recipe_IronPlate_C"): 30}


def test_the_diagnosis_finds_what_the_belts_leave_undone() -> None:
    smelters = [_at(f"s{i}", "Build_SmelterMk1_C", "Recipe_IngotIron_C") for i in (1, 2, 3)]
    plates = [_at(f"p{i}", "Build_ConstructorMk1_C", "Recipe_IronPlate_C") for i in (1, 2, 3, 4)]
    lonely = _at("p5", "Build_ConstructorMk1_C", "Recipe_IronPlate_C")
    station = _at("station", "Build_TrainDockingStation_C")
    belt = _at("belt", "Build_ConveyorBeltMk1_C")
    links = [TransportLink("station", s.object_id, "belt") for s in smelters]
    links += [
        TransportLink(s.object_id, p.object_id, "belt", via=("belt",))
        for s in smelters
        for p in plates
    ]
    context = replace(
        _built_factory(),
        existing_placements=(*smelters, *plates, lonely, station, belt),
        existing_links=tuple(links),
        factory_sites=(),
        transport_tiers=(
            TransportTier("Build_ConveyorBeltMk1_C", "Conveyor Belt Mk.1", 60, False),
        ),
    )

    _, seen = run_single_tool(context, "diagnose_factory_problems", {})

    assert seen["wiring_problems_found"] == {
        "machine_not_fed": 1,
        "output_blocked": 5,
        "congestion": 1,
    }
    groups = {group["kind"]: group for group in seen["wiring_problems"]}
    assert groups["output_blocked"]["machines"] == 5
    assert groups["output_blocked"]["items"] == ["Desc_IronPlate_C"]
    assert groups["machine_not_fed"]["items"] == ["Desc_IronIngot_C"]
    assert groups["congestion"]["carries_per_minute"] == 90
    assert groups["congestion"]["rated_per_minute"] == 60
    assert "p5" not in json.dumps(seen)  # a save's object ids mean nothing to a player


def test_factory_item_balance_reports_produced_used_and_net() -> None:
    graph = ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="n1",
                recipe_id="Recipe_IronPlate_C",
                building_id="Build_ConstructorMk1_C",
                machine_count=2,
            ),
            ProductionNode(
                node_id="n2",
                recipe_id="Recipe_IngotIron_C",
                building_id="Build_SmelterMk1_C",
                machine_count=1,
            ),
        ),
        flows=(),
    )
    context = OrchestratorContext(
        recipes=RECIPES + (IRON_INGOT,), items=ITEMS, existing_graph=graph
    )

    _, everything = run_single_tool(context, "factory_item_balance", {})
    _, plates = run_single_tool(context, "factory_item_balance", {"item": "Iron Plate"})

    rates = everything["per_minute"]
    assert rates["Desc_IronIngot_C"] == {"produced": 30, "used": 60, "net": -30}
    assert rates["Desc_IronPlate_C"] == {"produced": 40, "used": 0, "net": 40}
    assert [row["net"] for row in rates.values()] == [-30, -30, 40]  # deficits first
    assert plates["per_minute"] == {"Desc_IronPlate_C": rates["Desc_IronPlate_C"]}
    assert everything["names"]["Desc_IronPlate_C"] == "Iron Plate"


def test_factory_item_balance_without_a_save_says_so() -> None:
    _, result = run_single_tool(OrchestratorContext(recipes=RECIPES), "factory_item_balance", {})

    assert "no save loaded" in result["error"]


def test_factory_power_breaks_draw_and_capacity_down_by_building() -> None:
    context = OrchestratorContext(
        recipes=(FUEL,),
        buildings=POWER_BUILDINGS,
        items=FUEL_ITEMS,
        existing_graph=ProductionGraph(nodes=(), flows=()),
        existing_placements=(
            placed("Build_OilRefinery_C", recipe_id="Recipe_LiquidFuel_C"),
            placed("Build_GeneratorFuel_C", fuel_item_id="Desc_LiquidFuel_C"),
            placed("Build_GeneratorFuel_C", fuel_item_id="Desc_LiquidFuel_C"),
        ),
    )

    _, seen = run_single_tool(context, "factory_power", {})

    assert (seen["draw_mw"], seen["capacity_mw"], seen["spare_mw"]) == (30, 500, 470)
    assert seen["draw_by_building"] == {"Build_OilRefinery_C": {"count": 1, "mw": 30}}
    assert seen["generators"] == {"Build_GeneratorFuel_C": {"count": 2, "mw": 500}}
    assert seen["fuel_burned_per_minute"] == {"Desc_LiquidFuel_C": 40}
    assert "anomalies" not in seen


def test_factory_power_without_a_save_says_so() -> None:
    _, seen = run_single_tool(OrchestratorContext(), "factory_power", {})

    assert "no save loaded" in seen["error"]


def test_a_listing_of_the_factories_gives_each_ones_net_rates(monkeypatch) -> None:
    monkeypatch.setattr("pioneer.orchestrator.factory_tools._MAX_DRAWN_STAGES", 1)

    _, seen = run_single_tool(_built_factory(), "show_existing_factory", {})

    first = seen["factories"][0]
    assert first["net_out_per_minute"] == {"Desc_IronIngot_C": 45, "Desc_IronPlate_C": 10}
    assert first["net_in_per_minute"] == {"Desc_OreIron_C": 60}


def test_a_sites_balance_says_what_it_needs_and_what_it_has_to_spare() -> None:
    """A smelter and a constructor: 30 ore a minute in, 20 plates out, the ingots used up."""
    site = FactorySite(
        site_id="site_1",
        position=Coordinates(x=0, y=0),
        placements=(
            PlacementRecord(
                building_id="Build_SmelterMk1_C",
                position=Coordinates(x=0, y=0),
                recipe_id="Recipe_IngotIron_C",
            ),
            PlacementRecord(
                building_id="Build_ConstructorMk1_C",
                position=Coordinates(x=100, y=0),
                recipe_id="Recipe_IronPlate_C",
            ),
        ),
    )
    context = OrchestratorContext(
        recipes=(*RECIPES, IRON_INGOT), items=ITEMS, buildings=IRON_BUILDINGS
    )

    net = site_balance(site, context)

    assert {item: rate for item, rate in net.items() if abs(rate) > 1e-9} == {
        "Desc_OreIron_C": -30,
        "Desc_IronPlate_C": 20,
    }
