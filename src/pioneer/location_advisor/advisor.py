"""Ranks unclaimed resource deposits of a given type as candidate build sites, best first, and
finds where the player's existing factories stand.

**Factory sites.** With the save's belts and pipes known (`TransportLink`s), a site is every
running building they join, through any chain of links: a whole tree of machines together with
the extractors that feed it, the generators it fuels and the containers and stations along the
way. Only a group running at least one recipe is a site; a miner filling a lone container isn't a
factory. A machine no link reaches still belongs somewhere, so those are grouped by distance, as
below, into sites of their own.

Without links, running production buildings (those with a recipe) that stand within
`link_distance` of each other — through any chain of them — form a site. 50 m separates the
factories of real saves well: closer, and one factory floor splits in two; much farther, and
neighbouring factories merge. Belts, foundations and power poles aren't considered; they'd join
everything to everything.

**What feeds a factory.** An extractor feeds the site its belts and pipes lead to: with links
known, the site whose machines they join it to, through splitters, mergers and containers alike
— it's one of that site's placements. One that reaches no site that way, or a save without links,
is put down to the nearest site within `feed_distance`, and marked as a guess: an extractor
standing next to a factory most likely feeds it, but a train or a drone may as well carry its ore
across the map.

**Unclaimed cross-reference.** A deposit is claimed when an extractor in the save names it as what
it extracts from (`PlacementRecord.resource_node_id` — exact, the save's own
`mExtractableResource`), or when any placement sits within `claim_radius` of it. The proximity test
stays for everything that doesn't name a node: whatever is built right on a deposit makes it
occupied, and data without references at all still works. Building class isn't considered.

**Ranking** combines purity and distance into a single `score` (higher is better):
`purity_weight / (1 + distance / distance_scale)`, where `purity_weight` is the deposit's
extraction multiplier (impure 0.5, normal 1.0, pure 2.0 — the game's own Miner Mk.1 rate ratios)
and `distance` is measured from `reference` (where the player wants to build). `distance_scale`
sets how fast distance erodes the score — at `distance == distance_scale`, score is halved.

**Distance function** is injectable (`distance_fn`), matching Stage 4's `verifier.distance`
signature. It defaults to a local straight-line implementation so this module needs no dependency
on `verifier`; the orchestrator can pass the real one at integration time. The proximity test looks
placements up in a grid of `claim_radius`-sized cells instead of measuring every one of a real
save's thousands against every deposit, which assumes `distance_fn` is never shorter than the
straight-line distance on the x/y plane — as the real one isn't.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
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
                score=_PURITY_WEIGHT[node.purity] / (1.0 + distance / distance_scale),
            )
        )

    ranked.sort(key=lambda location: location.score, reverse=True)
    return tuple(ranked)


def claimed_node_ids(
    nodes: Sequence[ResourceNode],
    placements: Sequence[PlacementRecord],
    *,
    distance_fn: DistanceFn = _euclidean_distance,
    claim_radius: float = 1000.0,
) -> frozenset[str]:
    """Which of `nodes` are taken: named by an extractor, or built on (see the module docstring)."""
    named = {p.resource_node_id for p in placements if p.resource_node_id is not None}
    nearby = _PositionGrid((p.position for p in placements), claim_radius)
    return frozenset(
        node.node_id
        for node in nodes
        if node.node_id in named
        or any(distance_fn(node.position, p) <= claim_radius for p in nearby.around(node.position))
    )


def find_factory_sites(
    placements: Sequence[PlacementRecord],
    links: Sequence[TransportLink] = (),
    *,
    link_distance: float = 5000.0,
    distance_fn: DistanceFn = _euclidean_distance,
) -> tuple[FactorySite, ...]:
    """The player's factories, most machines first, ids `site_1`, `site_2`, ... in that order:
    what `links` joins, and machines no link reaches grouped by distance (see module docstring).
    Paused buildings are left out."""
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
    distance_fn: DistanceFn = _euclidean_distance,
) -> dict[str, tuple[SiteFeed, ...]]:
    """Which resource nodes feed each site, by site id (see the module docstring): the extractors
    among a site's own placements -- the links joined them to it -- and, as guesses, every other
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

    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    grid = _PositionGrid((), link_distance)
    for index, machine in enumerate(machines):
        cells[grid.cell(machine.position)].append(index)
    for (column, row), members in cells.items():
        for d_column in (-1, 0, 1):
            for d_row in (-1, 0, 1):
                for other in cells.get((column + d_column, row + d_row), ()):
                    for index in members:
                        if index < other and (
                            distance_fn(machines[index].position, machines[other].position)
                            <= link_distance
                        ):
                            parent[root(index)] = root(other)

    groups: dict[int, list[PlacementRecord]] = defaultdict(list)
    for index, machine in enumerate(machines):
        groups[root(index)].append(machine)
    return list(groups.values())


class _PositionGrid:
    """Positions bucketed into square x/y cells of side `radius`: everything within `radius` of a
    point lies in that point's cell or one of the eight around it."""

    def __init__(self, positions: Iterator[Coordinates] | Sequence[Coordinates], radius: float):
        self._cell_size = max(radius, 1.0)
        self._cells: dict[tuple[int, int], list[Coordinates]] = defaultdict(list)
        for position in positions:
            self._cells[self.cell(position)].append(position)

    def around(self, position: Coordinates) -> Iterator[Coordinates]:
        column, row = self.cell(position)
        for d_column in (-1, 0, 1):
            for d_row in (-1, 0, 1):
                yield from self._cells.get((column + d_column, row + d_row), ())

    def cell(self, position: Coordinates) -> tuple[int, int]:
        return (
            math.floor(position.x / self._cell_size),
            math.floor(position.y / self._cell_size),
        )
