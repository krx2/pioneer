"""Loads resource node data from JSON.

`load_from_dict` is the pure entry point every test in `tests/resource_db/test_loader.py`
exercises, against `fixtures/mini_nodes.json`. `load_from_file` is the thin I/O wrapper around it,
for the real data shipped at `docs/resource_nodes.json`.

Unlike the Knowledge Base (Stage 2), there is no game-shipped export for resource nodes: the game
encodes them in world geometry, and a save doesn't record what a node yields or how pure it is
either. The schema is therefore this project's own — a list of `{node_id, item_id, purity,
position: {x, y, z}}` objects, optionally wrapped as `{"nodes": [...]}` next to provenance fields,
which is how the shipped file carries its source.

That file was converted once from the community table `sav_data/resourcePurity.py` in
GreyHak/sat_sav_parse (game version 1.2.0.0; GPL-3.0, extracted there from SCIM) — the node layout
of the default world never changes, so there's nothing to refresh. It is keyed by exactly the actor
path names saves use (`Persistent_Level:PersistentLevel.BP_ResourceNode103`): both fixture saves
match all 607 of its nodes at the same positions, and every extractor in them names one, which is
what makes occupancy and extraction rates exact. `Desc_Geyser_C` is the source's own id for
geysers, which no item backs. Worlds generated with 1.2's randomized node mode won't match it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pioneer.contracts import Coordinates, Purity, ResourceNode
from pioneer.resource_db.queries import ResourceDatabase


def load_from_file(path: Path | str) -> ResourceDatabase:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return load_from_dict(raw)


def load_from_dict(raw: list[dict[str, Any]] | dict[str, Any]) -> ResourceDatabase:
    """`raw` is the node list itself, or an object carrying it under `nodes` next to its
    provenance, as the shipped file does."""
    raw_nodes = raw["nodes"] if isinstance(raw, dict) else raw
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
