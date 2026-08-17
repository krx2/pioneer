"""The assembled resource database and its pure lookup functions.

`ResourceDatabase` is this module's own aggregate container (not a Stage 1 contract — other
modules never construct one themselves, they only ever receive `ResourceNode` tuples produced by
the lookup functions below).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pioneer.contracts import Coordinates, ResourceNode


@dataclass(frozen=True)
class ResourceDatabase:
    nodes: tuple[ResourceNode, ...]


def _distance(a: Coordinates, b: Coordinates) -> float:
    """Local straight-line distance helper.

    Deliberately not the Stage 4 Verifier's `distance()` — this module only depends on Stage 1
    contracts, and Stage 4 may not exist yet. Once it does, callers above this module (e.g. the
    Location Advisor) are free to use the real one; this stays a private implementation detail.
    """
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


def nearest_nodes(
    db: ResourceDatabase, item_id: str, position: Coordinates, count: int = 1
) -> tuple[ResourceNode, ...]:
    """The `count` nodes producing `item_id` closest to `position`, nearest first."""
    candidates = [node for node in db.nodes if node.item_id == item_id]
    candidates.sort(key=lambda node: _distance(node.position, position))
    return tuple(candidates[:count])


def unclaimed_nodes(
    db: ResourceDatabase, item_id: str, claimed_positions: tuple[Coordinates, ...]
) -> tuple[ResourceNode, ...]:
    """Nodes producing `item_id` whose position isn't among `claimed_positions`.

    Takes plain `Coordinates`, not `PlacementRecord` — this module has no business depending on
    the Save Parser's contract. Cross-referencing real save placements against resource nodes is
    the Location Advisor's job (Stage 9).
    """
    claimed = set(claimed_positions)
    return tuple(
        node for node in db.nodes if node.item_id == item_id and node.position not in claimed
    )
