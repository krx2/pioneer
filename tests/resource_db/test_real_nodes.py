"""Bonus confidence check: the resource node data shipped at `docs/resource_nodes.json` (see
loader.py for where it came from). The fixture-based tests stay the module's "done" bar."""

import json
from collections import Counter
from pathlib import Path

import pytest

from pioneer.resource_db.loader import load_from_file

_DATA_PATH = Path(__file__).parent.parent.parent / "docs" / "resource_nodes.json"

pytestmark = pytest.mark.skipif(
    not _DATA_PATH.exists(), reason="docs/resource_nodes.json not present"
)


@pytest.fixture(scope="module")
def db():
    return load_from_file(_DATA_PATH)


def test_every_node_of_the_default_world_is_there_once(db) -> None:
    assert len(db.nodes) == 607
    assert len({node.node_id for node in db.nodes}) == 607


def test_node_counts_per_resource(db) -> None:
    counts = Counter(node.item_id for node in db.nodes)

    assert counts["Desc_OreIron_C"] == 127
    assert counts["Desc_Geyser_C"] == 31
    assert counts["Desc_OreUranium_C"] == 5
    assert sum(counts.values()) == 607


def test_node_ids_are_the_actor_paths_saves_use(db) -> None:
    assert all(node.node_id.startswith("Persistent_Level:PersistentLevel.") for node in db.nodes)


def test_the_file_records_where_its_data_came_from() -> None:
    provenance = json.loads(_DATA_PATH.read_text(encoding="utf-8"))

    assert "GreyHak/sat_sav_parse" in provenance["source"]
    assert "GPL-3.0" in provenance["source_license"]
