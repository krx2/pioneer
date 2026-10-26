"""The assembled resource database and its pure lookup functions."""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.contracts import Coordinates, ResourceNode


@dataclass(frozen=True)
class ResourceDatabase:
    nodes: tuple[ResourceNode, ...]


def nearest_nodes(
    db: ResourceDatabase, item_id: str, position: Coordinates, count: int = 1
) -> tuple[ResourceNode, ...]:
    """The `count` nodes producing `item_id` closest to `position`, nearest first."""
    candidates = [node for node in db.nodes if node.item_id == item_id]
    candidates.sort(key=lambda node: node.position.distance_to(position))
    return tuple(candidates[:count])


def unclaimed_nodes(
    db: ResourceDatabase, item_id: str, claimed_positions: tuple[Coordinates, ...]
) -> tuple[ResourceNode, ...]:
    """Nodes producing `item_id` whose position isn't among `claimed_positions`."""
    claimed = set(claimed_positions)
    return tuple(
        node for node in db.nodes if node.item_id == item_id and node.position not in claimed
    )
