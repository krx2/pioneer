"""Tests for the location advisor, against hand-built resource-node and placement lists."""

import pytest

from pioneer.contracts import Coordinates, PlacementRecord, Purity, ResourceNode
from pioneer.location_advisor.advisor import rank_locations

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
