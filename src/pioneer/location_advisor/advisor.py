"""Ranks unclaimed resource deposits of a given type as candidate build sites, best first.

**Unclaimed cross-reference.** A deposit counts as claimed if any placement from the save sits
within `claim_radius` of it (a proximity test, not an exact coordinate match — an extractor's
stored transform origin rarely lands exactly on the catalogued node coordinate, and this is the
real `PlacementRecord`-based check that Stage 3's `resource_db.unclaimed_nodes` deliberately left
to this module). Building class isn't considered: if something is built right on a deposit, it's
occupied.

**Ranking** combines purity and distance into a single `score` (higher is better):
`purity_weight / (1 + distance / distance_scale)`, where `purity_weight` is the deposit's
extraction multiplier (impure 0.5, normal 1.0, pure 2.0 — the game's own Miner Mk.1 rate ratios)
and `distance` is measured from `reference` (where the player wants to build). `distance_scale`
sets how fast distance erodes the score — at `distance == distance_scale`, score is halved.

**Distance function** is injectable (`distance_fn`), matching Stage 4's `verifier.distance`
signature. It defaults to a local straight-line implementation so this module needs no dependency
on `verifier`; the orchestrator can pass the real one at integration time.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from pioneer.contracts import Coordinates, PlacementRecord, Purity, RankedLocation, ResourceNode

DistanceFn = Callable[[Coordinates, Coordinates], float]

_PURITY_WEIGHT: dict[Purity, float] = {
    Purity.IMPURE: 0.5,
    Purity.NORMAL: 1.0,
    Purity.PURE: 2.0,
}


def _euclidean_distance(a: Coordinates, b: Coordinates) -> float:
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


def rank_locations(
    item_id: str,
    nodes: tuple[ResourceNode, ...],
    placements: tuple[PlacementRecord, ...],
    reference: Coordinates,
    *,
    distance_fn: DistanceFn = _euclidean_distance,
    claim_radius: float = 1000.0,
    distance_scale: float = 10000.0,
) -> tuple[RankedLocation, ...]:
    """Unclaimed `item_id` deposits ranked best-first. `reference` is the point distances are
    measured from; `claim_radius` and `distance_scale` are in the same units as the coordinates
    (centimetres, for real save data)."""
    claimed_positions = tuple(p.position for p in placements)

    ranked: list[RankedLocation] = []
    for node in nodes:
        if node.item_id != item_id:
            continue
        if _is_claimed(node, claimed_positions, distance_fn, claim_radius):
            continue
        distance = distance_fn(node.position, reference)
        ranked.append(
            RankedLocation(
                resource_node_id=node.node_id,
                position=node.position,
                purity=node.purity,
                distance_to_reference=distance,
                score=_PURITY_WEIGHT[node.purity] / (1.0 + distance / distance_scale),
            )
        )

    ranked.sort(key=lambda location: location.score, reverse=True)
    return tuple(ranked)


def _is_claimed(
    node: ResourceNode,
    claimed_positions: tuple[Coordinates, ...],
    distance_fn: DistanceFn,
    claim_radius: float,
) -> bool:
    return any(distance_fn(node.position, pos) <= claim_radius for pos in claimed_positions)
