"""Where to build, against a scripted fake model: rank_build_locations, and the deposits and
extractors a plan is to be built on."""

from dataclasses import replace

from tests.game_data import IRON_INGOT
from tests.orchestrator.support import (
    IRON_BUILDINGS,
    ITEMS,
    MINER,
    NODES,
    RECIPES,
    ask,
    run_single_tool,
    scripted_tool_calling_llm,
    tool_call,
)

from pioneer.contracts import (
    Building,
    Coordinates,
    Item,
    PlacementRecord,
    Purity,
    ResourceNode,
    Technology,
    TransportTier,
)
from pioneer.orchestrator.base import OrchestratorContext


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
    llm = scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [
                    tool_call("call_1", "rank_build_locations", {"item_id": "Desc_OreIron_C"})
                ],
            },
            {"content": "Build near node_near, it's pure and close.", "tool_calls": []},
        ]
    )

    result = ask(
        llm,
        "where should I mine iron ore?",
        OrchestratorContext(resource_nodes=nodes),
        response_id="resp-4",
    )

    assert result.map_locations is not None
    assert result.map_locations[0].resource_node_id == "node_near"


def test_rank_build_locations_without_node_data_says_so() -> None:
    _, tool_result = run_single_tool(
        OrchestratorContext(items=ITEMS), "rank_build_locations", {"item_id": "Iron Ore"}
    )

    assert "no resource node data" in tool_result["error"]


def test_locations_are_measured_from_the_players_base_by_default() -> None:
    """The base sits around x=90,000: the normal node right there beats the pure one far away."""
    base = tuple(
        PlacementRecord(building_id="Build_ConstructorMk1_C", position=Coordinates(x=x, y=0))
        for x in (80_000, 100_000)
    )
    context = OrchestratorContext(items=ITEMS, resource_nodes=NODES, existing_placements=base)

    result, tool_result = run_single_tool(context, "rank_build_locations", {"item_id": "Iron Ore"})

    assert tool_result["reference"] == {"x": 90_000, "y": 0, "z": 0}
    assert result.map_locations[0].resource_node_id.endswith("BP_ResourceNode2")


def test_the_answer_keeps_the_point_its_distances_are_measured_from() -> None:
    context = OrchestratorContext(items=ITEMS, resource_nodes=NODES)

    result, _ = run_single_tool(
        context, "rank_build_locations", {"item_id": "Iron Ore", "reference_x": 500}
    )

    assert result.map_reference == Coordinates(x=500, y=0, z=0)


def test_a_count_below_one_is_refused() -> None:
    context = OrchestratorContext(items=ITEMS, resource_nodes=NODES)

    result, tool_result = run_single_tool(
        context, "rank_build_locations", {"item_id": "Iron Ore", "count": 0}
    )

    assert "count must be at least 1" in tool_result["error"]
    assert result.map_locations is None


def test_geysers_can_be_asked_for_by_name() -> None:
    context = OrchestratorContext(items=ITEMS, resource_nodes=NODES)

    result, _ = run_single_tool(context, "rank_build_locations", {"item_id": "geyser"})

    assert [location.resource_node_id for location in result.map_locations] == [
        "Persistent_Level:PersistentLevel.BP_ResourceNodeGeyser1"
    ]


def test_a_plan_says_where_to_build_it_on_the_least_node_that_is_enough() -> None:
    """30 ore a minute: the normal node 900 m off is enough, so the pure one 10 m off is kept."""
    context = OrchestratorContext(
        recipes=RECIPES + (IRON_INGOT,),
        items=ITEMS,
        resource_nodes=NODES,
        buildings=IRON_BUILDINGS + (replace(MINER, extracts_from="node"),),
    )

    result, tool_result = run_single_tool(
        context, "plan_production", {"target_item_id": "Iron Plate", "target_rate_per_minute": 20}
    )

    sites = tool_result["build_sites"]
    assert sites["layout"].startswith("one site")
    (site,) = sites["sites"]
    assert (site["site_id"], site["distance_from_base_m"]) == ("new_site_1", 900)
    assert site["deposits"] == [
        {
            "item_id": "Desc_OreIron_C",
            "purity": "normal",
            "nodes": 1,
            "extractor": "Build_MinerMk1_C",
            "clock_percent": 50.0,
            "power_shards_each": 0,
            "per_minute": 30.0,
        }
    ]
    assert "builds" not in site  # one site builds it all
    assert [loc.resource_node_id for loc in result.map_locations] == [NODES[1].node_id]
    assert result.map_reference == Coordinates(x=0, y=0, z=0)
    assert result.build_sites is not None
    assert [d.resource_node_id for d in result.build_sites.deposits] == [NODES[1].node_id]


def test_deposits_are_worked_by_the_best_extractor_unlocked_up_to_the_fastest_belt() -> None:
    """Miner Mk.2 is unlocked and Mk.3 isn't; of the belts only Mk.1 (60/min) is. A normal node
    takes the Mk.2, but the belt carries no more than 60 of the 100 asked for."""
    miners = tuple(
        replace(
            MINER,
            building_id=f"Build_MinerMk{n}_C",
            name=f"Miner Mk.{n}",
            extraction_rate_per_minute=rate,
            extracts_from="node",
            unlockable_by=(f"Schematic_Miner{n}",),
        )
        for n, rate in ((1, 60), (2, 120), (3, 240))
    )
    belts = tuple(
        TransportTier(
            f"Build_ConveyorBeltMk{n}_C",
            f"Conveyor Belt Mk.{n}",
            rate,
            False,
            unlockable_by=(f"Schematic_Belt{n}",),
        )
        for n, rate in ((1, 60), (2, 120))
    )
    context = OrchestratorContext(
        items=ITEMS,
        resource_nodes=(NODES[1],),
        buildings=miners,
        transport_tiers=belts,
        unlocked_technology_ids=frozenset(
            {"Schematic_Miner1", "Schematic_Miner2", "Schematic_Belt1"}
        ),
    )

    _, low = run_single_tool(
        context, "rank_build_locations", {"item_id": "Iron Ore", "rate_per_minute": 45}
    )
    _, high = run_single_tool(
        context, "rank_build_locations", {"item_id": "Iron Ore", "rate_per_minute": 100}
    )

    (deposit,) = low["build_sites"]["sites"][0]["deposits"]
    assert (deposit["extractor"], deposit["clock_percent"]) == ("Build_MinerMk2_C", 37.5)
    (deposit,) = high["build_sites"]["sites"][0]["deposits"]
    assert (deposit["clock_percent"], deposit["per_minute"]) == (50.0, 60.0)
    assert high["build_sites"]["not_covered_per_minute"] == {"Desc_OreIron_C": 40.0}


def test_a_node_only_a_locked_extractor_can_work_names_what_to_unlock() -> None:
    oil = Item(item_id="Desc_LiquidOil_C", name="Crude Oil", is_raw_resource=True, is_fluid=True)
    node = ResourceNode("oil_1", "Desc_LiquidOil_C", Purity.NORMAL, Coordinates(x=5000, y=0))
    pump = Building(
        "Build_OilPump_C",
        "Oil Extractor",
        40,
        0,
        1,
        extraction_rate_per_minute=120,
        fixed_resource_id="Desc_LiquidOil_C",
        extracts_from="node",
        unlockable_by=("Schematic_5-1_C",),
    )
    context = OrchestratorContext(
        items=(oil,),
        resource_nodes=(node,),
        buildings=(pump, replace(MINER, extracts_from="node")),
        technologies=(Technology("Schematic_5-1_C", "Oil Processing", 5, kind="milestone"),),
        unlocked_technology_ids=frozenset(),
    )

    _, result = run_single_tool(
        context, "rank_build_locations", {"item_id": "Crude Oil", "rate_per_minute": 120}
    )

    (deposit,) = result["build_sites"]["sites"][0]["deposits"]
    assert deposit["extractor"] == "Build_OilPump_C"
    assert result["build_sites"]["extractors_to_unlock"] == [
        {"building_id": "Build_OilPump_C", "unlock_with": "Schematic_5-1_C", "tier": 5}
    ]


def test_an_explicit_site_ranking_is_not_replaced_by_a_plans_suggestions() -> None:
    context = OrchestratorContext(
        recipes=RECIPES + (IRON_INGOT,), items=ITEMS, resource_nodes=NODES
    )
    llm = scripted_tool_calling_llm(
        [
            {
                "content": None,
                "tool_calls": [
                    tool_call("c1", "rank_build_locations", {"item_id": "geyser"}),
                    tool_call(
                        "c2",
                        "plan_production",
                        {"target_item_id": "Iron Plate", "target_rate_per_minute": 20},
                    ),
                ],
            },
            {"content": "done", "tool_calls": []},
        ]
    )

    result = ask(llm, "q", context, response_id="r")

    assert [loc.resource_node_id for loc in result.map_locations] == [
        "Persistent_Level:PersistentLevel.BP_ResourceNodeGeyser1"
    ]


def test_ranked_sites_are_reported_in_metres() -> None:
    context = OrchestratorContext(items=ITEMS, resource_nodes=NODES)

    _, tool_result = run_single_tool(context, "rank_build_locations", {"item_id": "Iron Ore"})

    assert [site["distance_m"] for site in tool_result["locations"]] == [10, 900]
