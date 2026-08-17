"""Tests for the pure lookup functions, against a small hand-built `ResourceDatabase` — no loader
or file I/O involved."""

from pioneer.contracts import Coordinates, Purity, ResourceNode
from pioneer.resource_db.queries import ResourceDatabase, nearest_nodes, unclaimed_nodes

_IRON_PURE = ResourceNode(
    node_id="iron_pure",
    item_id="iron_ore",
    purity=Purity.PURE,
    position=Coordinates(x=0.0, y=0.0),
)
_IRON_NORMAL = ResourceNode(
    node_id="iron_normal",
    item_id="iron_ore",
    purity=Purity.NORMAL,
    position=Coordinates(x=100.0, y=0.0),
)
_IRON_IMPURE = ResourceNode(
    node_id="iron_impure",
    item_id="iron_ore",
    purity=Purity.IMPURE,
    position=Coordinates(x=10.0, y=0.0),
)
_COPPER_NORMAL = ResourceNode(
    node_id="copper_normal",
    item_id="copper_ore",
    purity=Purity.NORMAL,
    position=Coordinates(x=5.0, y=0.0),
)

DB = ResourceDatabase(nodes=(_IRON_PURE, _IRON_NORMAL, _IRON_IMPURE, _COPPER_NORMAL))


def test_nearest_nodes_returns_closest_first() -> None:
    result = nearest_nodes(DB, "iron_ore", Coordinates(x=0.0, y=0.0), count=2)
    assert result == (_IRON_PURE, _IRON_IMPURE)


def test_nearest_nodes_default_count_is_one() -> None:
    assert nearest_nodes(DB, "iron_ore", Coordinates(x=0.0, y=0.0)) == (_IRON_PURE,)


def test_nearest_nodes_ignores_other_items() -> None:
    result = nearest_nodes(DB, "iron_ore", Coordinates(x=0.0, y=0.0), count=10)
    assert _COPPER_NORMAL not in result
    assert len(result) == 3


def test_nearest_nodes_unknown_item_returns_empty() -> None:
    assert nearest_nodes(DB, "nonexistent_item", Coordinates(x=0.0, y=0.0)) == ()


def test_unclaimed_nodes_excludes_claimed_positions() -> None:
    result = unclaimed_nodes(DB, "iron_ore", claimed_positions=(_IRON_NORMAL.position,))
    assert set(result) == {_IRON_PURE, _IRON_IMPURE}


def test_unclaimed_nodes_with_no_claims_returns_all_of_type() -> None:
    result = unclaimed_nodes(DB, "iron_ore", claimed_positions=())
    assert set(result) == {_IRON_PURE, _IRON_NORMAL, _IRON_IMPURE}


def test_unclaimed_nodes_ignores_other_items() -> None:
    result = unclaimed_nodes(DB, "copper_ore", claimed_positions=())
    assert result == (_COPPER_NORMAL,)
