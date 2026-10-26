"""Loads resource node data from JSON: `{node_id, item_id, purity, position: {x, y, z}}` objects,
this project's own schema, since the game ships no export of its nodes.

The shipped file was converted once from `sav_data/resourcePurity.py` in GreyHak/sat_sav_parse
(game 1.2.0.0; GPL-3.0). It is keyed by the actor path names saves use
(`Persistent_Level:PersistentLevel.BP_ResourceNode103`), so extractors in a save name its nodes
exactly. `Desc_Geyser_C` is the source's own id for geysers. Worlds with 1.2's randomized nodes
won't match it. `BP_FrackingSatellite` nodes are a resource well's satellites.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pioneer.contracts import Coordinates, Purity, ResourceNode
from pioneer.resource_db.queries import ResourceDatabase

_WELL_SATELLITE = "BP_FrackingSatellite"


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
            on_well=_WELL_SATELLITE in entry["node_id"],
        )
        for entry in raw_nodes
    )
    return ResourceDatabase(nodes=nodes)
