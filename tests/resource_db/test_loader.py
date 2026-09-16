"""Tests for the resource node loader, against `fixtures/mini_nodes.json` — three iron nodes
(pure, normal, impure) and one copper node, per that fixture's own layout."""

import json
from pathlib import Path

import pytest

from pioneer.contracts import Purity
from pioneer.resource_db.loader import load_from_dict

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mini_nodes.json"


@pytest.fixture
def db():
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        raw_nodes = json.load(f)
    return load_from_dict(raw_nodes)


def test_loads_all_nodes(db) -> None:
    assert len(db.nodes) == 4


def test_node_fields_are_parsed(db) -> None:
    node = next(n for n in db.nodes if n.node_id == "iron_node_pure_1")
    assert node.item_id == "Desc_OreIron_C"
    assert node.purity == Purity.PURE
    assert (node.position.x, node.position.y, node.position.z) == (0.0, 0.0, 0.0)


def test_purity_values_are_parsed_for_every_node(db) -> None:
    purities = {n.node_id: n.purity for n in db.nodes}
    assert purities == {
        "iron_node_pure_1": Purity.PURE,
        "iron_node_normal_1": Purity.NORMAL,
        "iron_node_impure_1": Purity.IMPURE,
        "copper_node_normal_1": Purity.NORMAL,
    }


def test_nodes_can_come_wrapped_with_their_provenance() -> None:
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        raw_nodes = json.load(f)

    db = load_from_dict({"source": "hand-written", "nodes": raw_nodes})

    assert len(db.nodes) == 4
