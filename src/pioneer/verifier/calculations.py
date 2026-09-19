"""The deterministic arithmetic core: machine counts, throughput balance, power balance, distance.

Every function here is pure and takes Stage 1 contract data straight from its caller. Unlike
Knowledge Base or Resource DB, this module owns no aggregate of its own — there's no `Verifier`
container, just functions — since it has no data to load, only calculations to run over whatever
`Recipe`/`Building` list the caller (eventually the real Knowledge Base) hands it.

Power has two views. `power_balance` is a production graph's own draw — what a *plan* needs.
`placed_power_consumption_mw`, `placed_generation_capacity_mw`, `generator_fuel_demand`,
`generator_supplemental_demand`, `generator_byproducts` and `extraction_rates` work over a save's
placements instead — every building actually standing, including the extractors, pumps and
generators a recipe graph never contains — which is what checking an existing factory for
blackouts, fuel burn, water or ore supply has to look at.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace

from pioneer.contracts import (
    Building,
    Coordinates,
    GeneratorFuel,
    Item,
    MaterialFlow,
    PlacementRecord,
    ProductionGraph,
    Purity,
    Recipe,
    ResourceNode,
    TransportTier,
)

_OVERCLOCK_POWER_EXPONENT = 1.321929
"""How power draw scales with clock speed — the game's `mPowerConsumptionExponent` for production
buildings and extractors: at 250% a machine draws 2.5 ** 1.32 ≈ 3.4x its rated power, not 2.5x.
Generator output, by contrast, scales linearly with clock speed."""

_RATE_TOLERANCE = 1e-9
"""Machine counts this close to zero are float noise, not machines."""

_PRODUCTION_BOOST_POWER_EXPONENT = 2.0
"""How power draw scales with Somersloop production boost — the export's
`mProductionBoostPowerConsumptionExponent`, the same for every building: double the output, four
times the power."""

_PURITY_MULTIPLIER = {Purity.IMPURE: 0.5, Purity.NORMAL: 1.0, Purity.PURE: 2.0}
"""How a node's purity scales an extractor's rate — the game's own ratios."""


def distance(a: Coordinates, b: Coordinates) -> float:
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


def machine_count(recipe: Recipe, target_rate: float) -> int:
    """Machines needed to hit `target_rate` of `recipe`'s primary (first-listed) output.

    Rounds up — you can't build a fractional machine. `target_rate <= 0` needs zero machines.
    """
    if target_rate <= 0:
        return 0
    per_machine_rate = recipe.outputs[0].amount_per_minute
    if per_machine_rate <= 0:
        raise ValueError(f"recipe {recipe.recipe_id!r} has no positive primary output rate")
    return math.ceil(target_rate / per_machine_rate)


def balance(graph: ProductionGraph, recipes: tuple[Recipe, ...]) -> dict[str, float]:
    """Net per-item rate across every node: total production minus total consumption, each scaled
    by the node's `machine_count`. Positive = surplus, negative = deficit, an item absent from the
    result is never touched by the graph.

    Computed straight from each node's own recipe, independent of how `graph.flows` routes
    material — this is what the flows *should* sum to if the graph is internally consistent, which
    makes it the reference a flow-routing check can be verified against. Outputs are multiplied by
    the node's `production_boost`; inputs aren't.
    """
    net: dict[str, float] = {}
    for node in graph.nodes:
        recipe = _recipe_by_id(recipes, node.recipe_id)
        for item in recipe.outputs:
            rate = item.amount_per_minute * node.machine_count * node.production_boost
            net[item.item_id] = net.get(item.item_id, 0.0) + rate
        for item in recipe.inputs:
            rate = item.amount_per_minute * node.machine_count
            net[item.item_id] = net.get(item.item_id, 0.0) - rate
    return net


def implied_flows(
    graph: ProductionGraph,
    recipes: tuple[Recipe, ...],
    links: Collection[tuple[str, str]] | None = None,
) -> ProductionGraph:
    """`graph` with the flows its recipes imply -- for a graph read from a save, which says what
    the machines run but not what they hand each other. Each item's producers feed its consumers
    (see `allocate_supply`): all of them, or with `links` (source node, target node) only those
    the save's belts and pipes join. What the graph makes beyond that leaves as output (target
    `None`), and what it uses beyond it comes in from outside (source `None`), so the flows into
    and out of every node add up to its recipe rates, and per item to `balance` -- the recipes'
    arithmetic, shared out along the belts where they're known, never a measurement of them. A
    node making and using the same item feeds itself first; only the rest is shown."""
    made: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    used: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for node in graph.nodes:
        recipe = _recipe_by_id(recipes, node.recipe_id)
        for item in recipe.outputs:
            rate = item.amount_per_minute * node.machine_count * node.production_boost
            made[node.node_id][item.item_id] += rate
        for item in recipe.inputs:
            used[node.node_id][item.item_id] += item.amount_per_minute * node.machine_count
    for node_id in made.keys() & used.keys():  # a node's own use never leaves it
        for item_id in made[node_id].keys() & used[node_id].keys():
            own = min(made[node_id][item_id], used[node_id][item_id])
            made[node_id][item_id] -= own
            used[node_id][item_id] -= own

    moved = allocate_supply(made, used, links)
    sent: dict[tuple[str, str], float] = defaultdict(float)
    received: dict[tuple[str, str], float] = defaultdict(float)
    for (source, target, item_id), rate in moved.items():
        sent[(source, item_id)] += rate
        received[(target, item_id)] += rate
    total_made: dict[str, float] = defaultdict(float)
    total_used: dict[str, float] = defaultdict(float)
    for totals, per_node in ((total_made, made), (total_used, used)):
        for rates in per_node.values():
            for item_id, rate in rates.items():
                totals[item_id] += rate

    def noise(item_id: str) -> float:
        return _FLOW_NOISE * max(total_made[item_id], total_used[item_id])

    flows = [
        MaterialFlow(item_id, rate, source, target)
        for (source, target, item_id), rate in moved.items()
        if rate > noise(item_id)
    ]
    flows += [
        MaterialFlow(item_id, left, node_id, None)
        for node_id, rates in made.items()
        for item_id, rate in rates.items()
        if (left := rate - sent[(node_id, item_id)]) > noise(item_id)
    ]
    flows += [
        MaterialFlow(item_id, short, None, node_id)
        for node_id, rates in used.items()
        for item_id, rate in rates.items()
        if (short := rate - received[(node_id, item_id)]) > noise(item_id)
    ]
    return replace(graph, flows=tuple(flows))


def allocate_supply(
    supply: Mapping[str, Mapping[str, float]],
    demand: Mapping[str, Mapping[str, float]],
    links: Collection[tuple[str, str]] | None = None,
    *,
    open_ends: Collection[str] = (),
) -> dict[tuple[str, str, str], float]:
    """How much of each item each supplier sends each consumer a minute, by `(supplier, consumer,
    item)`, from what each makes (`supply`) and uses (`demand`), per item.

    A consumer's need for an item is shared among the suppliers of it that reach it -- every one,
    or with `links` (supplier, consumer) only those it joins -- in proportion to what they make. A
    supplier asked for more than it makes sends each consumer the same fraction of what it does
    make; the consumer then goes short. What a supplier has left after that goes, in equal parts,
    to the `open_ends` it reaches -- a container, a sink or a station takes whatever arrives."""
    feeders: dict[str, set[str]] = defaultdict(set)
    for supplier, consumer in links or ():
        feeders[consumer].add(supplier)

    def reaching(consumer: str, makers: Iterable[str]) -> list[str]:
        return [
            supplier
            for supplier in makers
            if supplier != consumer and (links is None or supplier in feeders[consumer])
        ]

    flows: dict[tuple[str, str, str], float] = defaultdict(float)
    for item_id in dict.fromkeys(item for rates in demand.values() for item in rates):
        makers = {s: rates[item_id] for s, rates in supply.items() if rates.get(item_id, 0) > 0}
        asked: dict[str, float] = defaultdict(float)
        wanted: dict[tuple[str, str], float] = {}
        for consumer, rates in demand.items():
            need = rates.get(item_id, 0.0)
            sources = reaching(consumer, makers) if need > 0 else []
            total = sum(makers[source] for source in sources)
            for source in sources:
                wanted[(source, consumer)] = need * makers[source] / total
                asked[source] += wanted[(source, consumer)]
        for (source, consumer), rate in wanted.items():
            flows[(source, consumer, item_id)] = rate * min(1.0, makers[source] / asked[source])

    if open_ends:
        sent: dict[tuple[str, str], float] = defaultdict(float)
        for (source, _, item_id), rate in flows.items():
            sent[(source, item_id)] += rate
        ends_of: dict[str, list[str]] = defaultdict(list)
        for end in open_ends:
            for source in reaching(end, supply):
                ends_of[source].append(end)
        for source, ends in ends_of.items():
            for item_id, rate in supply[source].items():
                if (left := rate - sent[(source, item_id)]) > _RATE_TOLERANCE:
                    for end in ends:
                        flows[(source, end, item_id)] += left / len(ends)
    return dict(flows)


def belt_loads(
    routes: Mapping[tuple[str, str], Iterable[str]],
    flows: Mapping[tuple[str, str, str], float],
) -> dict[str, float]:
    """What each belt carries a minute: every flow's rate, added to each belt on its route
    (`routes` by (supplier, consumer), as `TransportLink.via` gives them)."""
    loads: dict[str, float] = defaultdict(float)
    for (source, target, _), rate in flows.items():
        for belt in routes.get((source, target), ()):
            loads[belt] += rate
    return dict(loads)


_FLOW_NOISE = 1e-5
"""A flow this small a fraction of its item's throughput is rounding, not material: a save keeps
clock speeds as 32-bit floats (a third is 0.33333334), so a factory balanced to the item still
leaves a millionth of one over -- which would otherwise be drawn as an input or output itself."""


def _positive(rates: dict[str, float]) -> dict[str, float]:
    return {key: rate for key, rate in rates.items() if rate > _RATE_TOLERANCE}


def consumption(graph: ProductionGraph, recipes: tuple[Recipe, ...]) -> dict[str, float]:
    """Gross per-item consumption across every node, scaled by its `machine_count` — the demand
    `balance` subtracts, which is what a shortfall is measured against."""
    consumed: dict[str, float] = {}
    for node in graph.nodes:
        recipe = _recipe_by_id(recipes, node.recipe_id)
        for item in recipe.inputs:
            rate = item.amount_per_minute * node.machine_count
            consumed[item.item_id] = consumed.get(item.item_id, 0.0) + rate
    return consumed


def added_machines(graph: ProductionGraph) -> ProductionGraph:
    """What building `graph` takes: every node keeps only its machines beyond
    `existing_machine_count`, and a node left with none is dropped. Flows are kept as they are — in
    an expansion they already describe only the additions. New machines carry no Somersloops."""
    nodes = tuple(
        replace(node, machine_count=added, existing_machine_count=0.0, production_boost=1.0)
        for node in graph.nodes
        if (added := node.machine_count - node.existing_machine_count) > _RATE_TOLERANCE
    )
    return ProductionGraph(nodes=nodes, flows=graph.flows)


def minimal_machine_graph(graph: ProductionGraph, recipes: tuple[Recipe, ...]) -> ProductionGraph:
    """`graph` with every node running exactly the machines its outgoing flows need — fractional,
    i.e. underclocked — the optimum a whole-machine plan is measured against (architecture.md §6's
    distance from optimum). Worked back from the graph's final outputs: a node feeding another
    only needs to feed that node's own minimal count, so rounding up at one stage isn't passed on
    as demand to the stages before it. A node no flow leaves keeps its count, since nothing says
    what it's for; so does one caught in a loop."""
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    leaving: dict[str, list[MaterialFlow]] = defaultdict(list)
    for flow in graph.flows:
        if flow.source_node_id in nodes_by_id:
            leaving[flow.source_node_id].append(flow)

    minimal: dict[str, float] = {}
    resolving: set[str] = set()

    def solve(node_id: str) -> float:
        node = nodes_by_id[node_id]
        if node_id in minimal:
            return minimal[node_id]
        if node_id in resolving or not leaving.get(node_id):
            return node.machine_count

        resolving.add(node_id)
        needed: dict[str, float] = defaultdict(float)
        for flow in leaving[node_id]:
            target = nodes_by_id.get(flow.target_node_id or "")
            share = 1.0
            if target is not None and target.machine_count > 0:
                share = solve(target.node_id) / target.machine_count
            needed[flow.item_id] += flow.amount_per_minute * share
        resolving.discard(node_id)

        rates = {
            output.item_id: output.amount_per_minute * node.production_boost
            for output in _recipe_by_id(recipes, node.recipe_id).outputs
        }
        counts = [amount / rates[item] for item, amount in needed.items() if rates.get(item, 0) > 0]
        minimal[node_id] = max(counts) if counts else node.machine_count
        return minimal[node_id]

    nodes = tuple(replace(node, machine_count=solve(node.node_id)) for node in graph.nodes)
    return ProductionGraph(nodes=nodes, flows=graph.flows)


def power_balance(graph: ProductionGraph, buildings: tuple[Building, ...]) -> float:
    """Net power draw in MW across every node, scaled by `machine_count`.

    Positive = the graph is a net power consumer (needs this much external supply); negative = net
    producer (surplus) — the same sign convention as `Building.power_consumption_mw` itself.
    """
    total = 0.0
    for node in graph.nodes:
        building = _building_by_id(buildings, node.building_id)
        total += building.power_consumption_mw * node.machine_count
    return total


def placed_power_consumption_mw(
    placements: Sequence[PlacementRecord], buildings: tuple[Building, ...]
) -> float:
    """Rated draw of every placed power consumer, at its clock speed (see
    `_OVERCLOCK_POWER_EXPONENT`) and Somersloop boost (`_PRODUCTION_BOOST_POWER_EXPONENT`).
    Placements `buildings` has no entry for are skipped rather than raising, unlike in
    `power_balance`: most placed buildings — belts, foundations, storage, poles — draw nothing and
    appear in no building list."""
    return sum(
        building.power_consumption_mw
        * placement.clock_speed**_OVERCLOCK_POWER_EXPONENT
        * placement.production_boost**_PRODUCTION_BOOST_POWER_EXPONENT
        for placement, building in _placed_buildings(placements, buildings)
        if building.power_consumption_mw > 0
    )


def placed_generation_capacity_mw(
    placements: Sequence[PlacementRecord], buildings: tuple[Building, ...]
) -> float:
    """Rated output of every placed generator at its clock speed — what the grid could supply with
    every generator fueled, not a guarantee that it is."""
    return sum(
        -building.power_consumption_mw * placement.clock_speed
        for placement, building in _placed_buildings(placements, buildings)
        if building.power_consumption_mw < 0
    )


def generator_fuel_demand(
    placements: Sequence[PlacementRecord],
    buildings: tuple[Building, ...],
    items: Sequence[Item],
) -> dict[str, float]:
    """Per fuel item, how fast the placed generators burn it, in units (m³ for fluids) per minute:
    output in MW — MJ per second — over the fuel's `Item.energy_value_mj`, times 60. A Fuel
    Generator's 250 MW on 750 MJ/m³ Fuel is 20 m³/min, as in game. Generators with no fuel recorded,
    or burning an item with no known energy value, are left out."""
    demand: dict[str, float] = {}
    for fuel, _, burned_per_minute in _fuel_burn(placements, buildings, items):
        demand[fuel] = demand.get(fuel, 0.0) + burned_per_minute
    return demand


def generator_supplemental_demand(
    placements: Sequence[PlacementRecord], buildings: tuple[Building, ...]
) -> dict[str, float]:
    """Per supplemental resource, how fast the placed generators consume it besides their fuel,
    per minute (m³ for water): output in MW times `Building.supplemental_per_minute_per_mw`, for
    whatever the fuel each one burns needs (`GeneratorFuel.supplemental_item_id`). A Coal-Powered
    Generator takes 45 m³ of water a minute at 100%. Generators with no fuel recorded are left out,
    as in `generator_fuel_demand`."""
    demand: dict[str, float] = {}
    for _, fuel, output_mw, building in _fueled_generators(placements, buildings):
        supplemental = fuel.supplemental_item_id if fuel is not None else None
        if supplemental is None or building.supplemental_per_minute_per_mw <= 0:
            continue
        rate = output_mw * building.supplemental_per_minute_per_mw
        demand[supplemental] = demand.get(supplemental, 0.0) + rate
    return demand


def generator_byproducts(
    placements: Sequence[PlacementRecord],
    buildings: tuple[Building, ...],
    items: Sequence[Item],
) -> dict[str, float]:
    """Per byproduct, how fast the placed generators leave it behind, per minute: the fuel burn
    rate (see `generator_fuel_demand`) times `GeneratorFuel.byproduct_per_fuel_unit`. A Nuclear
    Power Plant burns 0.2 Uranium Fuel Rods a minute, leaving 10 Uranium Waste."""
    produced: dict[str, float] = {}
    for _, fuel, burned_per_minute in _fuel_burn(placements, buildings, items):
        if fuel is None or fuel.byproduct_item_id is None:
            continue
        rate = burned_per_minute * fuel.byproduct_per_fuel_unit
        produced[fuel.byproduct_item_id] = produced.get(fuel.byproduct_item_id, 0.0) + rate
    return produced


def extraction_rates(
    placements: Sequence[PlacementRecord],
    buildings: tuple[Building, ...],
    resource_nodes: Sequence[ResourceNode],
) -> dict[str, float]:
    """Per resource, what the placed extractors pull out per minute (m³ for fluids): each one's
    `Building.extraction_rate_per_minute`, times its node's purity multiplier (impure 0.5, normal 1,
    pure 2) and its clock speed. The resource is the node's — or, for an extractor on no known node,
    its `fixed_resource_id` at normal rate (a Water Extractor draws from a water volume, which has
    no purity). An extractor that could extract several resources, on a node `resource_nodes`
    doesn't know, is left out: without the node there's no telling what it extracts."""
    nodes = {node.node_id: node for node in resource_nodes}
    rates: dict[str, float] = {}
    for placement, building in _placed_buildings(placements, buildings):
        if building.extraction_rate_per_minute <= 0:
            continue
        node = nodes.get(placement.resource_node_id or "")
        if node is not None:
            item_id, multiplier = node.item_id, _PURITY_MULTIPLIER[node.purity]
        elif building.fixed_resource_id is not None:
            item_id, multiplier = building.fixed_resource_id, 1.0
        else:
            continue
        rate = building.extraction_rate_per_minute * multiplier * placement.clock_speed
        rates[item_id] = rates.get(item_id, 0.0) + rate
    return rates


@dataclass(frozen=True)
class PowerPlant:
    """One way to supply a power target: how many of a generator, burning which fuel, and what that
    takes and leaves at full load."""

    generator_id: str
    fuel_item_id: str
    generators: int
    capacity_mw: float
    fuel_per_minute: float
    supplemental_item_id: str | None
    supplemental_per_minute: float
    byproduct_item_id: str | None
    byproduct_per_minute: float


def power_plants(
    target_mw: float, buildings: Sequence[Building], items: Sequence[Item]
) -> tuple[PowerPlant, ...]:
    """Every generator-and-fuel pair the data can supply `target_mw` with — one per fuel with a
    known energy value — the least capacity past the target first, then the fewest generators.
    Figures are at full load: generators burn only what the grid draws, so these are the most the
    plant needs."""
    energy = {item.item_id: item.energy_value_mj for item in items if item.energy_value_mj > 0}
    plants = []
    for building in buildings:
        output_mw = -building.power_consumption_mw
        if output_mw <= 0:
            continue
        count = math.ceil(target_mw / output_mw) if target_mw > 0 else 0
        capacity = count * output_mw
        for fuel in building.fuels:
            if fuel.fuel_item_id not in energy:
                continue
            burned = capacity / energy[fuel.fuel_item_id] * 60.0
            has_supplemental = fuel.supplemental_item_id is not None
            plants.append(
                PowerPlant(
                    generator_id=building.building_id,
                    fuel_item_id=fuel.fuel_item_id,
                    generators=count,
                    capacity_mw=capacity,
                    fuel_per_minute=burned,
                    supplemental_item_id=fuel.supplemental_item_id,
                    supplemental_per_minute=(
                        capacity * building.supplemental_per_minute_per_mw
                        if has_supplemental
                        else 0.0
                    ),
                    byproduct_item_id=fuel.byproduct_item_id,
                    byproduct_per_minute=burned * fuel.byproduct_per_fuel_unit,
                )
            )
    return tuple(
        sorted(plants, key=lambda p: (p.capacity_mw, p.generators, p.generator_id, p.fuel_item_id))
    )


def extractors_needed(rate_per_minute: float, extractor: Building) -> int:
    """Whole fixed-resource extractors (Water Extractors) for `rate_per_minute`, at 100%."""
    if rate_per_minute <= 0:
        return 0
    if extractor.extraction_rate_per_minute <= 0:
        raise ValueError(f"{extractor.building_id!r} extracts nothing")
    return math.ceil(rate_per_minute / extractor.extraction_rate_per_minute)


@dataclass(frozen=True)
class TransportNeed:
    """The slowest belt or pipe tier that carries a flow, and how many lines of it — more than one
    only when even the fastest tier is too slow."""

    flow: MaterialFlow
    tier: TransportTier | None
    """`None` when the data has no tier for the flow's form (solid or fluid)."""
    lines: int


def transport_needs(
    flows: Sequence[MaterialFlow], items: Sequence[Item], tiers: Sequence[TransportTier]
) -> tuple[TransportNeed, ...]:
    """What carries each of `flows`: belts for solids, pipes for fluids (an item `items` doesn't
    know is taken for a solid)."""
    fluids = {item.item_id for item in items if item.is_fluid}
    needs = []
    for flow in flows:
        options = sorted(
            (t for t in tiers if t.carries_fluids == (flow.item_id in fluids)),
            key=lambda t: t.capacity_per_minute,
        )
        tier = next((t for t in options if t.capacity_per_minute >= flow.amount_per_minute), None)
        if tier is None and options:
            tier = options[-1]
        lines = max(1, math.ceil(flow.amount_per_minute / tier.capacity_per_minute)) if tier else 0
        needs.append(TransportNeed(flow=flow, tier=tier, lines=lines))
    return tuple(needs)


def _fueled_generators(
    placements: Sequence[PlacementRecord], buildings: tuple[Building, ...]
) -> Iterator[tuple[str, GeneratorFuel | None, float, Building]]:
    """(fuel item id, the building's entry for that fuel — `None` if its list has none, output in
    MW, building) for every placed generator with a fuel recorded."""
    for placement, building in _placed_buildings(placements, buildings):
        fuel_id = placement.fuel_item_id
        if building.power_consumption_mw >= 0 or fuel_id is None:
            continue
        fuel = next((f for f in building.fuels if f.fuel_item_id == fuel_id), None)
        yield fuel_id, fuel, -building.power_consumption_mw * placement.clock_speed, building


def _fuel_burn(
    placements: Sequence[PlacementRecord],
    buildings: tuple[Building, ...],
    items: Sequence[Item],
) -> Iterator[tuple[str, GeneratorFuel | None, float]]:
    """(fuel item id, its entry in the generator's list, units burned per minute) for every
    fueled generator burning an item with a known energy value."""
    energy = {item.item_id: item.energy_value_mj for item in items if item.energy_value_mj > 0}
    for fuel_id, fuel, output_mw, _ in _fueled_generators(placements, buildings):
        if fuel_id in energy:
            yield fuel_id, fuel, output_mw / energy[fuel_id] * 60.0


def _placed_buildings(
    placements: Sequence[PlacementRecord], buildings: tuple[Building, ...]
) -> Iterator[tuple[PlacementRecord, Building]]:
    """Every running placement with the building it is — a paused one makes, burns and draws
    nothing, so none of the placement views count it."""
    by_id = {building.building_id: building for building in buildings}
    for placement in placements:
        if placement.is_paused:
            continue
        building = by_id.get(placement.building_id)
        if building is not None:
            yield placement, building


def _recipe_by_id(recipes: tuple[Recipe, ...], recipe_id: str) -> Recipe:
    recipe = next((r for r in recipes if r.recipe_id == recipe_id), None)
    if recipe is None:
        raise ValueError(f"no recipe {recipe_id!r} in the supplied recipe list")
    return recipe


def _building_by_id(buildings: tuple[Building, ...], building_id: str) -> Building:
    building = next((b for b in buildings if b.building_id == building_id), None)
    if building is None:
        raise ValueError(f"no building {building_id!r} in the supplied building list")
    return building
