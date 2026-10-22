"""Where a plan is to be built: which free resource nodes feed it, worked by which extractor at what
clock speed, and whether it all fits on one site or is split across several, each making its part.

**What a node can give.** `extractor_for` says which extractor a node would be worked with — the
best one the player has, as the caller knows from their unlocks — or `None` if none can work it.
A node only an extractor the player hasn't unlocked can work is used only when the others can't
cover the need: a well's satellite before the Resource Well Extractor, say.
A node gives that extractor's rate, times its purity's multiplier (impure 0.5, normal 1, pure 2),
times the clock speed it can be pushed to (2.5 overclocked with three power shards), but no more
than the belt or pipe out of it carries (`Extractor.output_capacity`).

**No better node than needed.** For each resource the plan needs, a set of nodes is chosen that
covers the need with the least capacity to spare — then the fewest nodes, then the nearest. So a
normal node overclocked to 167% is chosen over a pure one underclocked, and the pure node is left
for when it's needed. The chosen nodes run at the same share of what each can give, so between
them they give exactly the need; one past 100% takes a power shard per 50 points.

**One site if it fits.** Every free node of a needed resource is tried as the middle of a site,
each resource's nodes within `site_radius` of it chosen as above. Of the middles where every
resource is covered, those sparing the least capacity — within a tenth of a need — and of them
the cheapest: how far a unit of resource travels on average to the middle of the chosen nodes
(weighed by what each gives), plus a quarter of how far that middle is from `reference` (where the
player is based), plus 500 m for every node worked. So a pure node next to the base doesn't win
over a normal one that's enough, and one normal node wins over two impure ones.

**Several sites if not.** Then each resource, the biggest need first, gets deposits of its own: the
nodes within `site_radius` of one of them that cover what's left of its need, chosen and picked
as above — costed with half the distance to the nearest deposits already chosen besides, so that
different resources' deposits gather. When no such area covers it, the one that gives the most
does, all it can, and the rest comes from the next — capacity spared is reckoned against the
whole need, not what's left of it, so the last few units don't send it to a lone node across the
map for want of one that spares less. Deposits closer than `site_radius` to each other make one
site; one with nothing to make but its resource is an outpost, sending it on.

**What each site makes.** Given the plan's graph, its stages are placed from the raw resources
down. A stage taking in resources mined for the plan goes whole to the site with enough of them
left — those taking in several first, as they're the hardest to place — or, short of that, to the
one importing the least; a stage taking in one resource no site has enough of is split among its
sites by what each has left, as smelters at every mine. Every other stage goes whole to the site
most of what it takes in comes from. So each site makes certain parts, not a slice of the whole
chain. What a stage needs from another site — a part, or a resource mined there — is a
`SiteTransfer`. A site's machines are its share of the stage's, rounded up.

**Revisions keep what's there.** Given the plan as it was (`kept`), its deposits stay: for a
resource needed less, the fewest of them that are still enough — those the player has already
built an extractor on first — and for one needed more, all of them and new deposits for the rest,
sought near what's kept; a new deposit within `site_radius` of a kept site joins it. Kept sites
keep their ids and where they are, and a stage stays at the site it was at while that site still
gets what the stage takes in. Nodes other plans work (`reserved`) are never offered.

Water is never asked for here: Water Extractors go on any water, not on nodes.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass

from pioneer.contracts import (
    BuildSite,
    Coordinates,
    Extractor,
    MaterialFlow,
    NodeAssignment,
    PlacementRecord,
    ProductionGraph,
    ResourceNode,
    SitePlan,
    SiteStage,
    SiteTransfer,
)
from pioneer.location_advisor.advisor import (
    _PURITY_WEIGHT,
    DistanceFn,
    _euclidean_distance,
    claimed_node_ids,
)

ExtractorFor = Callable[[ResourceNode], Extractor | None]

SITE_RADIUS = 40_000.0
"""400 m, in save centimetres: how far from a site's middle its deposits may lie."""
_BASE_WEIGHT = 0.25
_GATHER_WEIGHT = 0.5
_NODE_COST = 50_000.0
"""500 m of hauling, for each node worked: one more extractor, belt and its shards to build."""
_SPARE_TOLERANCE = 0.1
"""How much more capacity than the least, as a share of a need, a choice of nodes may spare and
still be picked for being closer."""
_SHARD_STEP = 0.5
_EPSILON = 1e-6


@dataclass(frozen=True)
class _Candidate:
    node: ResourceNode
    extractor: Extractor
    base_rate: float
    """What it gives at 100% clock speed."""
    capacity: float
    """The most it gives: overclocked as far as its extractor goes, and its belt or pipe carries."""


@dataclass(frozen=True)
class _Deposits:
    """Nodes of one resource chosen together, and how much of its need they give."""

    item_id: str
    nodes: tuple[_Candidate, ...]
    rate: float
    middle: Coordinates


def plan_sites(
    needs: Mapping[str, float],
    nodes: Sequence[ResourceNode],
    placements: Sequence[PlacementRecord],
    extractor_for: ExtractorFor,
    reference: Coordinates,
    graph: ProductionGraph | None = None,
    *,
    kept: SitePlan | None = None,
    reserved: Collection[str] = (),
    site_radius: float = SITE_RADIUS,
    claim_radius: float = 1000.0,
    distance_fn: DistanceFn = _euclidean_distance,
) -> SitePlan:
    """Where to build for `needs` (raw resource item id -> per minute): see the module docstring.
    Nodes `placements` claim are left out (`claimed_node_ids`), and so are those `reserved` for
    other plans; with `graph`, the plan's stages are shared out among the sites, else only its
    deposits are chosen. With `kept` — the plan as it was — it's revised, not chosen anew."""
    wanted = {item: rate for item, rate in needs.items() if rate > _EPSILON}
    kept = kept if kept is not None and kept.deposits else None
    own = frozenset(d.resource_node_id for d in kept.deposits) if kept is not None else frozenset()
    candidates = _candidates(
        wanted, nodes, placements, extractor_for, distance_fn, claim_radius, own | set(reserved)
    )
    if kept is not None:
        groups, positions, shortfall = _revised(
            wanted,
            kept,
            nodes,
            placements,
            extractor_for,
            candidates,
            reference,
            site_radius,
            distance_fn,
        )
        site_ids = list(groups)
    else:
        shortfall = {item: rate for item, rate in wanted.items() if not candidates.get(item)}
        coverable = {item: rate for item, rate in wanted.items() if item not in shortfall}
        one = _one_site(coverable, candidates, reference, site_radius, distance_fn)
        if one is not None:
            found = [list(one)]
        else:
            chosen, left = _split(coverable, candidates, reference, site_radius, distance_fn)
            shortfall.update(left)
            found = _gathered(chosen, site_radius, distance_fn)
        found.sort(key=lambda group: (-sum(d.rate for d in group), _middle_of(group).x))
        site_ids = [f"new_site_{number}" for number in range(1, len(found) + 1)]
        groups = dict(zip(site_ids, found, strict=True))
        positions = {site_id: _middle_of(group) for site_id, group in groups.items()}

    deposits = tuple(
        assignment
        for site_id in site_ids
        for found in groups[site_id]
        for assignment in _worked(found, site_id)
    )
    supply: dict[str, dict[str, float]] = {site_id: defaultdict(float) for site_id in site_ids}
    for assignment in deposits:
        supply[assignment.site_id][assignment.item_id] += assignment.rate_per_minute
    previous = {
        stage.recipe_id: site.site_id
        for site in (kept.sites if kept is not None else ())
        for stage in site.stages
        if stage.share > 1 - _EPSILON
    }
    stages, transfers = (
        _shared_out(graph, site_ids, supply, frozenset(wanted), previous)
        if graph is not None
        else ({}, ())
    )
    return SitePlan(
        sites=tuple(
            BuildSite(site_id, positions[site_id], tuple(stages.get(site_id, ())))
            for site_id in site_ids
            if groups[site_id] or stages.get(site_id)
        ),
        deposits=deposits,
        transfers=transfers,
        shortfall=tuple(sorted(shortfall.items())),
    )


def shards_for(clock_speed: float) -> int:
    """Power shards an extractor at `clock_speed` takes: one per 50 points past 100%."""
    return max(0, math.ceil((clock_speed - 1.0) / _SHARD_STEP - _EPSILON))


def _candidates(
    needs: Mapping[str, float],
    nodes: Sequence[ResourceNode],
    placements: Sequence[PlacementRecord],
    extractor_for: ExtractorFor,
    distance_fn: DistanceFn,
    claim_radius: float,
    left_out: Collection[str] = (),
) -> dict[str, list[_Candidate]]:
    """The free nodes of every needed resource some extractor can work, by item id — but those
    `left_out` — and of those only an extractor the player doesn't have can, only as many as it
    takes for what the others can't give."""
    wanted = [node for node in nodes if node.item_id in needs and node.node_id not in left_out]
    claimed = claimed_node_ids(
        wanted, placements, distance_fn=distance_fn, claim_radius=claim_radius
    )
    found: dict[str, list[_Candidate]] = defaultdict(list)
    locked: dict[str, list[_Candidate]] = defaultdict(list)
    for node in wanted:
        extractor = extractor_for(node) if node.node_id not in claimed else None
        candidate = _candidate(node, extractor)
        if candidate is not None:
            (locked if candidate.extractor.unlocked is False else found)[node.item_id].append(
                candidate
            )
    for item_id, others in locked.items():
        if sum(c.capacity for c in found[item_id]) < needs[item_id] - _EPSILON:
            found[item_id].extend(others)
    return found


def _candidate(node: ResourceNode, extractor: Extractor | None) -> _Candidate | None:
    """`node` worked by `extractor` — `None` when it gives nothing that way."""
    if extractor is None or extractor.rate_per_minute <= 0:
        return None
    base = extractor.rate_per_minute * _PURITY_WEIGHT[node.purity]
    capacity = min(base * extractor.max_clock_speed, extractor.output_capacity)
    return _Candidate(node, extractor, base, capacity) if capacity > _EPSILON else None


def _revised(
    needs: Mapping[str, float],
    kept: SitePlan,
    nodes: Sequence[ResourceNode],
    placements: Sequence[PlacementRecord],
    extractor_for: ExtractorFor,
    candidates: Mapping[str, list[_Candidate]],
    reference: Coordinates,
    radius: float,
    distance_fn: DistanceFn,
) -> tuple[dict[str, list[_Deposits]], dict[str, Coordinates], dict[str, float]]:
    """`kept` revised for `needs` — see the module docstring: the deposits of each site, by site
    id, kept sites first; where each site is; and what no free node is left for."""
    by_id = {node.node_id: node for node in nodes}
    built = {p.resource_node_id for p in placements if p.resource_node_id is not None}
    groups: dict[str, list[_Deposits]] = {site.site_id: [] for site in kept.sites}
    positions = {site.site_id: site.position for site in kept.sites}
    rest: dict[str, float] = {}
    for item, need in needs.items():
        own: list[tuple[_Candidate, str]] = []
        for deposit in kept.deposits:
            node = by_id.get(deposit.resource_node_id)
            found = _candidate(node, extractor_for(node)) if node is not None else None
            if deposit.item_id == item and found is not None and deposit.site_id in groups:
                own.append((found, deposit.site_id))
        site_of = {candidate.node.node_id: site for candidate, site in own}
        standing = [c for c, _ in own if c.node.node_id in built]
        others = [c for c, _ in own if c.node.node_id not in built]
        capacity = sum(c.capacity for c, _ in own)
        if capacity < need - _EPSILON:
            chosen, gives = [c for c, _ in own], capacity
            rest[item] = need - capacity
        else:
            enough = sum(c.capacity for c in standing)
            if enough >= need - _EPSILON:
                chosen = list(_cheapest_cover(standing, need, reference, distance_fn) or standing)
            else:
                more = _cheapest_cover(others, need - enough, reference, distance_fn) or others
                chosen = standing + list(more)
            gives = need
        whole = sum(c.capacity for c in chosen)
        for site_id in groups:
            here = tuple(c for c in chosen if site_of[c.node.node_id] == site_id)
            if here and whole > 0:
                groups[site_id].append(_deposits(item, here, gives * _capacity_of(here) / whole))

    seeds = [found for site_deposits in groups.values() for found in site_deposits]
    coverable = {item: rate for item, rate in rest.items() if candidates.get(item)}
    shortfall = {item: rate for item, rate in rest.items() if item not in coverable}
    added, left = _split(coverable, candidates, reference, radius, distance_fn, seeds)
    shortfall.update(left)
    unplaced = []
    for found in added:
        near = min(
            positions,
            key=lambda site_id: distance_fn(found.middle, positions[site_id]),
            default=None,
        )
        if near is not None and distance_fn(found.middle, positions[near]) <= radius:
            groups[near].append(found)
        else:
            unplaced.append(found)
    numbers = [int(site_id.rsplit("_", 1)[-1]) for site_id in groups if site_id[-1].isdigit()]
    for number, group in enumerate(
        _gathered(unplaced, radius, distance_fn), max(numbers, default=0) + 1
    ):
        site_id = f"new_site_{number}"
        groups[site_id] = group
        positions[site_id] = _middle_of(group)
    return groups, positions, shortfall


def _capacity_of(nodes: Sequence[_Candidate]) -> float:
    return sum(node.capacity for node in nodes)


def _one_site(
    needs: Mapping[str, float],
    candidates: Mapping[str, list[_Candidate]],
    reference: Coordinates,
    radius: float,
    distance_fn: DistanceFn,
) -> tuple[_Deposits, ...] | None:
    """Every need covered from around one middle, the cheapest there is — `None` if none does."""
    if not needs:
        return None
    grids = {item: _Grid(candidates[item], radius) for item in needs}
    options: list[tuple[float, float, str, tuple[_Deposits, ...]]] = []
    for item in sorted(needs):
        for anchor in candidates[item]:
            middle = anchor.node.position
            chosen = []
            for wanted, need in sorted(needs.items()):
                near = [
                    candidate
                    for candidate in grids[wanted].around(middle)
                    if distance_fn(candidate.node.position, middle) <= radius
                ]
                cover = _cheapest_cover(near, need, middle, distance_fn)
                if cover is None:
                    break
                chosen.append(_deposits(wanted, cover, need))
            else:
                cost = _cost(chosen, reference, distance_fn)
                options.append((_spare(chosen, needs), cost, anchor.node.node_id, tuple(chosen)))
    return _least_spare(options, len(needs))[-1] if options else None


def _split(
    needs: Mapping[str, float],
    candidates: Mapping[str, list[_Candidate]],
    reference: Coordinates,
    radius: float,
    distance_fn: DistanceFn,
    seeds: Sequence[_Deposits] = (),
) -> tuple[list[_Deposits], dict[str, float]]:
    """Deposits for every need on its own, gathering where they can — near `seeds`, deposits
    chosen already, too; and what no free node is left for."""
    chosen: list[_Deposits] = list(seeds)
    shortfall: dict[str, float] = {}
    used: set[str] = set()
    for item in sorted(needs, key=lambda item: (-needs[item], item)):
        left = needs[item]
        while left > _EPSILON:
            free = [c for c in candidates[item] if c.node.node_id not in used]
            if not free:
                shortfall[item] = left
                break
            covering: list[tuple[float, float, str, _Deposits]] = []
            biggest: tuple[float, float, str, _Deposits] | None = None
            for anchor in free:
                middle = anchor.node.position
                near = [c for c in free if distance_fn(c.node.position, middle) <= radius]
                capacity = sum(c.capacity for c in near)
                covers = capacity >= left - _EPSILON
                cover = _cheapest_cover(near, left, middle, distance_fn) if covers else near
                found = _deposits(item, tuple(cover), min(left, capacity))
                cost = _cost([found], reference, distance_fn)
                if chosen:
                    nearest = min(distance_fn(found.middle, other.middle) for other in chosen)
                    cost += _GATHER_WEIGHT * nearest
                if covers:
                    covering.append((_spare([found], needs), cost, anchor.node.node_id, found))
                elif biggest is None or (-capacity, cost) < (biggest[0], biggest[1]):
                    biggest = (-capacity, cost, anchor.node.node_id, found)
            picked = _least_spare(covering)[-1] if covering else biggest[-1]
            chosen.append(picked)
            used.update(c.node.node_id for c in picked.nodes)
            left -= picked.rate
    return chosen[len(seeds) :], shortfall


def _gathered(
    chosen: Sequence[_Deposits], radius: float, distance_fn: DistanceFn
) -> list[list[_Deposits]]:
    """`chosen` grouped into sites: deposits closer than `radius` to each other — through any
    chain of them — share one."""
    parent = list(range(len(chosen)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for index, deposits in enumerate(chosen):
        for other in range(index):
            if distance_fn(deposits.middle, chosen[other].middle) <= radius:
                parent[root(index)] = root(other)
    groups: dict[int, list[_Deposits]] = defaultdict(list)
    for index, deposits in enumerate(chosen):
        groups[root(index)].append(deposits)
    return list(groups.values())


def _cheapest_cover(
    candidates: Sequence[_Candidate],
    need: float,
    around: Coordinates,
    distance_fn: DistanceFn,
) -> tuple[_Candidate, ...] | None:
    """The nodes among `candidates` covering `need` with the least capacity to spare, then the
    fewest, then the nearest `around` — `None` when even all of them don't. Nodes that can give
    the same are interchangeable but for where they are, so it's how many of each that's searched,
    the nearest of them taken."""
    by_capacity: dict[float, list[tuple[float, str, _Candidate]]] = defaultdict(list)
    for candidate in candidates:
        far = distance_fn(candidate.node.position, around)
        by_capacity[round(candidate.capacity, 6)].append((far, candidate.node.node_id, candidate))
    capacities = sorted(by_capacity)
    for members in by_capacity.values():
        members.sort(key=lambda member: (member[0], member[1]))
    beyond = [0.0] * (len(capacities) + 1)  # what the capacities from each index on can give
    for index in range(len(capacities) - 1, -1, -1):
        capacity = capacities[index]
        beyond[index] = beyond[index + 1] + capacity * len(by_capacity[capacity])
    if beyond[0] < need - _EPSILON:
        return None

    best: tuple[tuple[float, int, float], tuple[int, ...]] | None = None

    def search(index: int, counts: tuple[int, ...], total: float, count: int, far: float) -> None:
        nonlocal best
        if total >= need - _EPSILON:
            key = (round(total, 6), count, far)
            if best is None or key < best[0]:
                best = (key, counts + (0,) * (len(capacities) - len(counts)))
            return
        if index == len(capacities) or total + beyond[index] < need - _EPSILON:
            return
        if best is not None and total >= best[0][0]:
            return  # more nodes only spare more
        capacity = capacities[index]
        members = by_capacity[capacity]
        most = min(len(members), math.ceil((need - total) / capacity - _EPSILON))
        further = far
        for taken in range(most + 1):
            if taken:
                further += members[taken - 1][0]
            search(index + 1, (*counts, taken), total + taken * capacity, count + taken, further)

    search(0, (), 0.0, 0, 0.0)
    assert best is not None
    return tuple(
        member[2]
        for capacity, taken in zip(capacities, best[1], strict=True)
        for member in by_capacity[capacity][:taken]
    )


def _deposits(item_id: str, nodes: tuple[_Candidate, ...], rate: float) -> _Deposits:
    capacity = sum(node.capacity for node in nodes)
    share = rate / capacity if capacity > 0 else 0.0
    weighted = [(node.node.position, node.capacity * share) for node in nodes]
    return _Deposits(item_id, nodes, rate, _weighted_middle(weighted))


def _least_spare(options: Sequence[tuple], needs: int = 1) -> tuple:
    """Of `options` — (capacity spared, cost, tie-break, ...) — the cheapest of those sparing the
    least capacity, give or take `_SPARE_TOLERANCE` of each of `needs` needs."""
    least = min(option[0] for option in options)
    allowed = least + _SPARE_TOLERANCE * needs + _EPSILON
    return min((o for o in options if o[0] <= allowed), key=lambda option: (option[1], option[2]))


def _spare(chosen: Sequence[_Deposits], needs: Mapping[str, float]) -> float:
    """The capacity a choice of deposits leaves unused, in its resources' whole needs: 0.5 is half
    a need."""
    return sum((_capacity(found) - found.rate) / needs[found.item_id] for found in chosen)


def _cost(chosen: Sequence[_Deposits], reference: Coordinates, distance_fn: DistanceFn) -> float:
    """What a choice of deposits costs, in centimetres of hauling — see the module docstring."""
    worked = [
        (candidate.node.position, candidate.capacity * found.rate / _capacity(found))
        for found in chosen
        for candidate in found.nodes
    ]
    middle = _weighted_middle(worked)
    total = sum(rate for _, rate in worked) or 1.0
    haul = sum(rate * distance_fn(position, middle) for position, rate in worked) / total
    return haul + _BASE_WEIGHT * distance_fn(middle, reference) + _NODE_COST * len(worked)


def _capacity(found: _Deposits) -> float:
    return sum(candidate.capacity for candidate in found.nodes)


def _worked(found: _Deposits, site_id: str) -> Iterator[NodeAssignment]:
    """Each of the deposits' nodes at the same share of what it can give, `found.rate` between
    them."""
    share = min(found.rate / _capacity(found), 1.0)
    for candidate in sorted(found.nodes, key=lambda c: (-c.capacity, c.node.node_id)):
        rate = candidate.capacity * share
        clock = rate / candidate.base_rate
        yield NodeAssignment(
            resource_node_id=candidate.node.node_id,
            item_id=found.item_id,
            purity=candidate.node.purity,
            position=candidate.node.position,
            extractor_id=candidate.extractor.building_id,
            clock_speed=clock,
            power_shards=shards_for(clock),
            rate_per_minute=rate,
            site_id=site_id,
        )


def _middle_of(group: Sequence[_Deposits]) -> Coordinates:
    return _weighted_middle([(found.middle, found.rate) for found in group])


def _weighted_middle(points: Sequence[tuple[Coordinates, float]]) -> Coordinates:
    total = sum(weight for _, weight in points)
    if total <= 0:
        total, points = len(points), [(position, 1.0) for position, _ in points]
    return Coordinates(
        x=sum(p.x * w for p, w in points) / total,
        y=sum(p.y * w for p, w in points) / total,
        z=sum(p.z * w for p, w in points) / total,
    )


def _shared_out(
    graph: ProductionGraph,
    site_ids: Sequence[str],
    supply: Mapping[str, Mapping[str, float]],
    raw_item_ids: frozenset[str],
    previous: Mapping[str, str] | None = None,
) -> tuple[dict[str, list[SiteStage]], tuple[SiteTransfer, ...]]:
    """Which site builds how much of each of `graph`'s stages, and what has to go between them —
    see the module docstring."""
    if not site_ids:
        return {}, ()
    flows_into: dict[str, list[MaterialFlow]] = defaultdict(list)
    for flow in graph.flows:
        if flow.target_node_id is not None:
            flows_into[flow.target_node_id].append(flow)
    totals = {item: sum(supply[site].get(item, 0.0) for site in site_ids) for item in raw_item_ids}

    def mined(item_id: str) -> dict[str, float]:
        """Where `item_id` is mined: each site's share of it."""
        return {
            site: supply[site][item_id] / totals[item_id]
            for site in site_ids
            if supply[site].get(item_id, 0.0) > _EPSILON
        }

    if len(site_ids) == 1:
        shares = {node.node_id: {site_ids[0]: 1.0} for node in graph.nodes}
    else:
        shares = _placed(graph, site_ids, supply, totals, flows_into, mined, previous or {})

    stages: dict[str, list[SiteStage]] = defaultdict(list)
    for node in graph.nodes:
        for site, share in shares[node.node_id].items():
            stages[site].append(
                SiteStage(
                    recipe_id=node.recipe_id,
                    building_id=node.building_id,
                    machine_count=float(math.ceil(node.machine_count * share - _EPSILON)),
                    share=share,
                )
            )

    moved: dict[tuple[str, str, str], float] = defaultdict(float)
    used: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for flow in graph.flows:
        if flow.target_node_id is None or flow.target_node_id not in shares:
            continue  # what the plan makes leaves it where it's made
        targets = shares[flow.target_node_id]
        if flow.source_node_id is not None:
            sources = shares.get(flow.source_node_id, {})
            _match(sources, targets, flow.amount_per_minute, flow.item_id, moved)
        elif totals.get(flow.item_id, 0.0) > 0:  # not water, say: from outside the deposits
            for site, share in targets.items():
                used[flow.item_id][site] += flow.amount_per_minute * share
    # A resource goes from the sites mining more of it than they use to those using more than
    # they mine: the whole of it at once, so none goes there and back.
    for item_id, by_site in used.items():
        whole = sum(by_site.values())
        if whole > 0:
            _match(
                mined(item_id),
                {site: rate / whole for site, rate in by_site.items()},
                whole,
                item_id,
                moved,
            )
    transfers = tuple(
        SiteTransfer(item_id, source, target, rate)
        for (item_id, source, target), rate in sorted(moved.items())
        if rate > 0.01
    )
    return dict(stages), transfers


def _placed(
    graph: ProductionGraph,
    site_ids: Sequence[str],
    supply: Mapping[str, Mapping[str, float]],
    totals: Mapping[str, float],
    flows_into: Mapping[str, Sequence[MaterialFlow]],
    mined: Callable[[str], dict[str, float]],
    previous: Mapping[str, str],
) -> dict[str, dict[str, float]]:
    """Each stage's share at each site, stages placed from the raw resources down — see the module
    docstring: a stage mining-fed where its resources are left, a single resource's stage split
    among its sites when no one of them has enough, and every other stage where most of what it
    takes in comes from."""
    left = {site: dict(supply[site]) for site in site_ids}
    main = max(site_ids, key=lambda site: (sum(supply[site].values()), -site_ids.index(site)))
    node_ids = {node.node_id for node in graph.nodes}
    depth: dict[str, int] = {}

    def depth_of(node_id: str, seen: frozenset[str] = frozenset()) -> int:
        if node_id not in depth:
            sources = [
                flow.source_node_id
                for flow in flows_into.get(node_id, ())
                if flow.source_node_id in node_ids and flow.source_node_id not in seen
            ]
            depth[node_id] = max(
                (depth_of(source, seen | {node_id}) + 1 for source in sources if source), default=0
            )
        return depth[node_id]

    def mined_in(node_id: str) -> dict[str, float]:
        """What of the plan's deposits `node_id` takes in, a minute."""
        taken: dict[str, float] = defaultdict(float)
        for flow in flows_into.get(node_id, ()):
            if flow.source_node_id is None and totals.get(flow.item_id, 0.0) > 0:
                taken[flow.item_id] += flow.amount_per_minute
        return taken

    order = sorted(
        enumerate(graph.nodes),
        key=lambda entry: (
            depth_of(entry[1].node_id),
            -len(mined_in(entry[1].node_id)),
            -sum(mined_in(entry[1].node_id).values()),
            entry[0],
        ),
    )
    shares: dict[str, dict[str, float]] = {}
    for _, node in order:
        taken = mined_in(node.node_id)
        if taken:
            lacking = {
                site: sum(
                    max(0.0, rate - left[site].get(item, 0.0)) for item, rate in taken.items()
                )
                for site in site_ids
            }
            before = previous.get(node.recipe_id)
            home = min(
                site_ids,
                key=lambda site: (
                    lacking[site],
                    site != before,
                    site != main,
                    site_ids.index(site),
                ),
            )
            if lacking[home] > _EPSILON and len(taken) == 1:
                ((item, _),) = taken.items()
                available = {site: left[site].get(item, 0.0) for site in site_ids}
                whole = sum(available.values())
                share = (
                    {site: a / whole for site, a in available.items() if a > _EPSILON}
                    if whole > _EPSILON
                    else mined(item)
                )
            else:
                share = {home: 1.0}
            for site, part in share.items():
                for item, rate in taken.items():
                    left[site][item] = max(0.0, left[site].get(item, 0.0) - rate * part)
        else:
            weight: dict[str, float] = defaultdict(float)
            for flow in flows_into.get(node.node_id, ()):
                for site, part in shares.get(flow.source_node_id or "", {}).items():
                    weight[site] += flow.amount_per_minute * part
            before = previous.get(node.recipe_id)
            home = (
                before
                if weight.get(before or "", 0.0) > 0  # it still gets some of what it takes in there
                else max(
                    site_ids,
                    key=lambda site: (weight.get(site, 0.0), site == main, -site_ids.index(site)),
                )
            )
            share = {home: 1.0}
        shares[node.node_id] = share
    return shares


def _match(
    sources: Mapping[str, float],
    targets: Mapping[str, float],
    amount: float,
    item_id: str,
    moved: dict[tuple[str, str, str], float],
) -> None:
    """What of a flow of `amount` made at `sources` (site -> share) and used at `targets` has to
    move between sites: what each site makes it uses itself first, and the rest goes where it's
    short, in proportion."""
    kept = {site: min(share, targets.get(site, 0.0)) for site, share in sources.items()}
    surplus = {site: share - kept[site] for site, share in sources.items()}
    short = {site: share - kept.get(site, 0.0) for site, share in targets.items()}
    surplus = {site: left for site, left in surplus.items() if left > _EPSILON}
    short = {site: want for site, want in short.items() if want > _EPSILON}
    wanting = sum(short.values())
    for source, left in surplus.items():
        for target, want in short.items():
            moved[(item_id, source, target)] += amount * left * want / wanting


class _Grid:
    """Candidates bucketed into square x/y cells of side `radius`: everything within `radius` of a
    point lies in that point's cell or one of the eight around it."""

    def __init__(self, candidates: Sequence[_Candidate], radius: float):
        self._size = max(radius, 1.0)
        self._cells: dict[tuple[int, int], list[_Candidate]] = defaultdict(list)
        for candidate in candidates:
            self._cells[self._cell(candidate.node.position)].append(candidate)

    def around(self, position: Coordinates) -> Iterator[_Candidate]:
        column, row = self._cell(position)
        for d_column in (-1, 0, 1):
            for d_row in (-1, 0, 1):
                yield from self._cells.get((column + d_column, row + d_row), ())

    def _cell(self, position: Coordinates) -> tuple[int, int]:
        return math.floor(position.x / self._size), math.floor(position.y / self._size)
