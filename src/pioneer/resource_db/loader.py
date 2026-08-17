"""Loads resource node data from a hand-curated JSON export.

`load_from_dict` is the pure entry point every test in `tests/resource_db/test_loader.py`
exercises, against `fixtures/mini_nodes.json`. `load_from_file` is the thin I/O wrapper around it.

Unlike the Knowledge Base (Stage 2), there is no canonical game-shipped export for resource node
locations and purity — the game encodes this in world geometry, not `Docs.json`. This loader's
JSON schema is therefore our own: a flat list of `{node_id, item_id, purity, position: {x, y, z}}`
objects. Full map data can be filled in later without changing this shape or any lookup function.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pioneer.contracts import Coordinates, Purity, ResourceNode
from pioneer.resource_db.queries import ResourceDatabase


def load_from_file(path: Path | str) -> ResourceDatabase:
    with open(path, encoding="utf-8") as f:
        raw_nodes = json.load(f)
    return load_from_dict(raw_nodes)


def load_from_dict(raw_nodes: list[dict[str, Any]]) -> ResourceDatabase:
    nodes = tuple(
        ResourceNode(
            node_id=entry["node_id"],
            item_id=entry["item_id"],
            purity=Purity(entry["purity"]),
            position=Coordinates(**entry["position"]),
        )
        for entry in raw_nodes
    )
    return ResourceDatabase(nodes=nodes)
