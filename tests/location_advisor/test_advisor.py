"""Tests for the location advisor, against hand-built resource-node and placement lists."""

import pytest

from pioneer.contracts import Coordinates, PlacementRecord, Purity, ResourceNode, TransportLink
from pioneer.location_advisor.advisor import find_factory_sites, rank_locations

_ORIGIN = Coordinates(x=0.0, y=0.0, z=0.0)


def _iron(node_id: str, purity: Purity, x: float, y: float = 0.0) -> ResourceNode:
    return ResourceNode(
        node_id=node_id, item_id="Desc_OreIron_C", purity=purity, position=Coordinates(x=x, y=y)
    )


def _placement(x: float, y: float = 0.0) -> PlacementRecord:
    return PlacementRecord(building_id="Build_MinerMk1_C", position=Coordinates(x=x, y=y))


def test_excludes_claimed_nodes() -> None:
    nodes = (
        _iron("free_1", Purity.NORMAL, x=1000),
        _iron("taken", Purity.PURE, x=5000),
        _iron("free_2", Purity.NORMAL, x=2000),
    )
    placements = (_placement(x=5000),)  # a miner sitting on "taken"

    result = rank_locations("Desc_OreIron_C", nodes, placements, _ORIGIN)

    assert {r.resource_node_id for r in result} == {"free_1", "free_2"}


def test_filters_by_item_id() -> None:
    nodes = (
        _iron("iron_1", Purity.NORMAL, x=1000),
        ResourceNode(
            node_id="copper_1",
            item_id="Desc_OreCopper_C",
            purity=Purity.PURE,
            position=Coordinates(x=500, y=0),
        ),
    )

    result = rank_locations("Desc_OreIron_C", nodes, (), _ORIGIN)

    assert [r.resource_node_id for r in result] == ["iron_1"]


def test_ranks_purer_deposit_above_lesser_at_equal_distance() -> None:
    nodes = (
        _iron("impure", Purity.IMPURE, x=1000),
        _iron("pure", Purity.PURE, x=1000),
        _iron("normal", Purity.NORMAL, x=1000),
    )

    result = rank_locations("Desc_OreIron_C", nodes, (), _ORIGIN)

    assert [r.resource_node_id for r in result] == ["pure", "normal", "impure"]
    assert result[0].score > result[1].score > result[2].score


def test_ranks_closer_deposit_first_at_equal_purity() -> None:
    nodes = (
        _iron("far", Purity.NORMAL, x=9000),
        _iron("near", Purity.NORMAL, x=1000),
        _iron("mid", Purity.NORMAL, x=3000),
    )

    result = rank_locations("Desc_OreIron_C", nodes, (), _ORIGIN)

    assert [r.resource_node_id for r in result] == ["near", "mid", "far"]


def test_distance_to_reference_is_computed_from_the_reference_point() -> None:
    nodes = (_iron("n", Purity.NORMAL, x=3000, y=4000),)  # 3-4-5 triangle from origin

    (result,) = rank_locations("Desc_OreIron_C", nodes, (), _ORIGIN)

    assert result.distance_to_reference == pytest.approx(5000.0)


def test_injected_distance_function_is_used() -> None:
    calls: list[tuple] = []

    def stub_distance(a: Coordinates, b: Coordinates) -> float:
        calls.append((a, b))
        return 42.0

    nodes = (_iron("n", Purity.PURE, x=123456),)

    (result,) = rank_locations("Desc_OreIron_C", nodes, (), _ORIGIN, distance_fn=stub_distance)

    assert result.distance_to_reference == 42.0
    assert calls  # the stub, not the real euclidean, did the measuring


def test_claim_radius_boundary() -> None:
    nodes = (
        _iron("inside", Purity.NORMAL, x=100),
        _iron("outside", Purity.NORMAL, x=100_000),
    )
    # A placement 150 units from "inside" (claimed at radius 200) and far from "outside".
    placements = (_placement(x=250),)

    result = rank_locations("Desc_OreIron_C", nodes, placements, _ORIGIN, claim_radius=200.0)

    assert [r.resource_node_id for r in result] == ["outside"]


def test_no_unclaimed_nodes_returns_empty() -> None:
    nodes = (_iron("only", Purity.PURE, x=1000),)
    placements = (_placement(x=1000),)

    assert rank_locations("Desc_OreIron_C", nodes, placements, _ORIGIN) == ()


def test_purer_far_can_still_lose_to_closer_lesser() -> None:
    # distance_scale defaults to 10000: a pure node very far vs a normal node right here.
    nodes = (
        _iron("pure_far", Purity.PURE, x=200_000),
        _iron("normal_here", Purity.NORMAL, x=0),
    )

    result = rank_locations("Desc_OreIron_C", nodes, (), _ORIGIN)

    assert result[0].resource_node_id == "normal_here"


def test_a_node_an_extractor_names_is_claimed_wherever_the_extractor_stands() -> None:
    nodes = (_iron("taken", Purity.PURE, x=5000), _iron("free", Purity.NORMAL, x=9000))
    miner_far_away = PlacementRecord(
        building_id="Build_MinerMk2_C",
        position=Coordinates(x=-50_000, y=0),
        resource_node_id="taken",
    )

    result = rank_locations("Desc_OreIron_C", nodes, (miner_far_away,), _ORIGIN)

    assert [r.resource_node_id for r in result] == ["free"]


def test_proximity_claims_across_grid_cell_boundaries() -> None:
    """A placement just across a cell edge from a node, but within the radius, still claims it."""
    nodes = (_iron("on_the_edge", Purity.NORMAL, x=999.0, y=999.0),)
    placements = (_placement(x=1001.0, y=1001.0),)  # a neighbouring 1000-unit cell, ~3 units away

    assert rank_locations("Desc_OreIron_C", nodes, placements, _ORIGIN) == ()


def _machine(x: float, y: float = 0.0, recipe_id: str | None = "Recipe_IronPlate_C", **fields):
    return PlacementRecord(
        building_id="Build_ConstructorMk1_C",
        position=Coordinates(x=x, y=y),
        recipe_id=recipe_id,
        **fields,
    )


def test_factory_sites_join_machines_through_chains_of_neighbours() -> None:
    """0, 40 m and 80 m chain into one site (each 40 m from the next); 500 m is its own."""
    placements = (_machine(0), _machine(4000), _machine(8000), _machine(50_000, 100))

    big, small = find_factory_sites(placements)

    assert (big.site_id, len(big.placements), big.position.x) == ("site_1", 3, 4000)
    assert (small.site_id, small.placements, small.position) == (
        "site_2",
        (placements[3],),
        Coordinates(x=50_000, y=100),
    )


def test_factory_sites_leave_out_paused_and_non_production_buildings() -> None:
    placements = (
        _machine(0),
        _machine(4000, recipe_id=None),  # a belt or a storage container
        _machine(8000, is_paused=True),
    )

    (site,) = find_factory_sites(placements)

    assert site.placements == (placements[0],)
    assert find_factory_sites(()) == ()


def _built(object_id: str, x: float, building_id: str = "Build_ConstructorMk1_C", **fields):
    return PlacementRecord(
        building_id=building_id, position=Coordinates(x=x, y=0.0), object_id=object_id, **fields
    )


def _belt(source_id: str, target_id: str) -> TransportLink:
    return TransportLink(source_id=source_id, target_id=target_id, carrier="belt")


def test_with_links_a_site_is_what_the_belts_join_however_far_apart() -> None:
    """A miner 1 km out belted to a smelter, belted to a constructor: one factory, the miner in
    it. A constructor standing right next to them that no belt reaches is a factory of its own."""
    miner = _built("miner", 100_000, "Build_MinerMk1_C")
    smelter = _built("smelter", 0, "Build_SmelterMk1_C", recipe_id="Recipe_IngotIron_C")
    plates = _built("plates", 1000, recipe_id="Recipe_IronPlate_C")
    alone = _built("alone", 2000, recipe_id="Recipe_IronRod_C")
    links = (_belt("miner", "smelter"), _belt("smelter", "plates"))

    joined, unlinked = find_factory_sites((miner, smelter, plates, alone), links)

    assert set(joined.placements) == {miner, smelter, plates}
    assert unlinked.placements == (alone,)


def test_with_links_a_group_running_no_recipe_is_no_factory() -> None:
    """A miner filling a container isn't a factory; a paused machine isn't part of one, though
    what's belted through it still is."""
    miner = _built("miner", 0, "Build_MinerMk1_C")
    box = _built("box", 100, "Build_StorageContainerMk1_C")
    paused = _built("paused", 5000, recipe_id="Recipe_IronPlate_C", is_paused=True)
    rods = _built("rods", 6000, recipe_id="Recipe_IronRod_C")
    links = (_belt("miner", "box"), _belt("paused", "rods"))

    (site,) = find_factory_sites((miner, box, paused, rods), links)

    assert site.placements == (rods,)
