"""Ranks unclaimed resource deposits as candidate build sites, and finds the player's factories.

- Factory sites: running buildings the save's belts and pipes join, through any chain of links,
  running at least one recipe. Machines no link reaches — or every machine, without links — are
  grouped by distance: within `link_distance` (50 m) of each other through any chain.
- What feeds a factory: the extractors its links join it to; failing that, the nearest site
  within `feed_distance`, marked as a guess.
- Claimed deposits: named by an extractor (`PlacementRecord.resource_node_id`), or with any
  placement within `claim_radius`.
- Ranking: `purity_weight / (1 + distance / distance_scale)`, purity weights 0.5/1/2 and distance
  from `reference`.

The proximity test buckets placements into a grid of `claim_radius` cells, which assumes
`distance_fn` is never shorter than the straight-line x/y distance.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pioneer.contracts import (
    Coordinates,
    FactorySite,
    PlacementRecord,
    Purity,
    RankedLocation,
    ResourceNode,
    TransportLink,
)
from pioneer.location_advisor.grid import SpatialGrid

DistanceFn = Callable[[Coordinates, Coordinates], float]

PURITY_WEIGHT: dict[Purity, float] = {
    Purity.IMPURE: 0.5,
    Purity.NORMAL: 1.0,
    Purity.PURE: 2.0,
}


def rank_locations(
    item_id: str,
    nodes: tuple[ResourceNode, ...],
    placements: tuple[PlacementRecord, ...],
    reference: Coordinates,
    *,
    distance_fn: DistanceFn = Coordinates.distance_to,
    claim_radius: float = 1000.0,
    distance_scale: float = 10000.0,
) -> tuple[RankedLocation, ...]:
    """Unclaimed `item_id` deposits ranked best-first. `reference` is the point distances are
    measured from; `claim_radius` and `distance_scale` are in the same units as the coordinates
    (centimetres, for real save data)."""
    wanted = tuple(node for node in nodes if node.item_id == item_id)
    claimed = claimed_node_ids(
        wanted, placements, distance_fn=distance_fn, claim_radius=claim_radius
    )

    ranked: list[RankedLocation] = []
    for node in wanted:
        if node.node_id in claimed:
            continue
        distance = distance_fn(node.position, reference)
        ranked.append(
            RankedLocation(
                resource_node_id=node.node_id,
                position=node.position,
                purity=node.purity,
                distance_to_reference=distance,
                score=PURITY_WEIGHT[node.purity] / (1.0 + distance / distance_scale),
            )
        )

    ranked.sort(key=lambda location: location.score, reverse=True)
    return tuple(ranked)


def claimed_node_ids(
    nodes: Sequence[ResourceNode],
    placements: Sequence[PlacementRecord],
    *,
    distance_fn: DistanceFn = Coordinates.distance_to,
    claim_radius: float = 1000.0,
) -> frozenset[str]:
    """Which of `nodes` are taken: named by an extractor, or built on."""
    named = {p.resource_node_id for p in placements if p.resource_node_id is not None}
    nearby = SpatialGrid(placements, claim_radius, lambda placement: placement.position)
    return frozenset(
        node.node_id
        for node in nodes
        if node.node_id in named
        or any(
            distance_fn(node.position, p.position) <= claim_radius
            for p in nearby.around(node.position)
        )
    )


def find_factory_sites(
    placements: Sequence[PlacementRecord],
    links: Sequence[TransportLink] = (),
    *,
    link_distance: float = 5000.0,
    distance_fn: DistanceFn = Coordinates.distance_to,
) -> tuple[FactorySite, ...]:
    """The player's factories, most machines first, ids `site_1`, `site_2`, ... in that order:
    what `links` joins, and machines no link reaches grouped by distance. Paused buildings are left
    out."""
    running = {p.object_id: p for p in placements if p.object_id and not p.is_paused}
    parent: dict[str, str] = {}

    def root(object_id: str) -> str:
        parent.setdefault(object_id, object_id)
        while parent[object_id] != object_id:
            parent[object_id] = parent[parent[object_id]]
            object_id = parent[object_id]
        return object_id

    for link in links:
        parent[root(link.source_id)] = root(link.target_id)
    joined: dict[str, list[PlacementRecord]] = defaultdict(list)
    for object_id in parent:
        if object_id in running:
            joined[root(object_id)].append(running[object_id])

    groups = [group for group in joined.values() if any(p.recipe_id for p in group)]
    unlinked = [
        p
        for p in placements
        if p.recipe_id is not None and not p.is_paused and p.object_id not in parent
    ]
    groups += _groups_by_distance(unlinked, link_distance, distance_fn)
    ordered = sorted(
        groups,
        key=lambda group: (
            -sum(1 for p in group if p.recipe_id is not None),
            -len(group),
            group[0].position.x,
            group[0].position.y,
        ),
    )
    return tuple(
        FactorySite(
            site_id=f"site_{number}",
            position=Coordinates(
                x=sum(p.position.x for p in group) / len(group),
                y=sum(p.position.y for p in group) / len(group),
                z=sum(p.position.z for p in group) / len(group),
            ),
            placements=tuple(group),
        )
        for number, group in enumerate(ordered, start=1)
    )


@dataclass(frozen=True)
class SiteFeed:
    """A resource node an extractor works for a factory site."""

    node_id: str
    """The node, as `PlacementRecord.resource_node_id` names it."""
    extractor: PlacementRecord
    approximate: bool
    """Put down to the nearest site rather than followed along belts or pipes."""


def site_feeds(
    sites: Sequence[FactorySite],
    placements: Sequence[PlacementRecord],
    *,
    feed_distance: float = 30000.0,
    distance_fn: DistanceFn = Coordinates.distance_to,
) -> dict[str, tuple[SiteFeed, ...]]:
    """Which resource nodes feed each site, by site id: the extractors among a site's own
    placements -- the links joined them to it -- and, as guesses, every other
    running extractor within `feed_distance` (300 m, in save centimetres) of a site, nearest one.
    A site no extractor feeds has no entry."""
    feeds: dict[str, list[SiteFeed]] = defaultdict(list)
    placed: set[int] = set()
    for site in sites:
        for placement in site.placements:
            if placement.resource_node_id is not None:
                feeds[site.site_id].append(SiteFeed(placement.resource_node_id, placement, False))
                placed.add(id(placement))
    for placement in placements:
        if placement.resource_node_id is None or placement.is_paused or id(placement) in placed:
            continue
        nearest = min(
            sites, key=lambda site: distance_fn(placement.position, site.position), default=None
        )
        if nearest is None or distance_fn(placement.position, nearest.position) > feed_distance:
            continue
        feeds[nearest.site_id].append(SiteFeed(placement.resource_node_id, placement, True))
    return {site_id: tuple(found) for site_id, found in feeds.items()}


def _groups_by_distance(
    machines: Sequence[PlacementRecord], link_distance: float, distance_fn: DistanceFn
) -> list[list[PlacementRecord]]:
    """`machines` grouped so that any within `link_distance` of each other — through any chain of
    them — share a group."""
    parent = list(range(len(machines)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    grid = SpatialGrid(range(len(machines)), link_distance, lambda i: machines[i].position)
    for index, machine in enumerate(machines):
        for other in grid.around(machine.position):
            if index < other and (
                distance_fn(machine.position, machines[other].position) <= link_distance
            ):
                parent[root(index)] = root(other)

    groups: dict[int, list[PlacementRecord]] = defaultdict(list)
    for index, machine in enumerate(machines):
        groups[root(index)].append(machine)
    return list(groups.values())
