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
from collections.abc import Iterator, Sequence
from dataclasses import replace

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
)

_OVERCLOCK_POWER_EXPONENT = 1.321929
"""How power draw scales with clock speed — the game's `mPowerConsumptionExponent` for production
buildings and extractors: at 250% a machine draws 2.5 ** 1.32 ≈ 3.4x its rated power, not 2.5x.
Generator output, by contrast, scales linearly with clock speed."""

_RATE_TOLERANCE = 1e-9
"""Machine counts this close to zero are float noise, not machines."""

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
    makes it the reference a flow-routing check can be verified against.
    """
    net: dict[str, float] = {}
    for node in graph.nodes:
        recipe = _recipe_by_id(recipes, node.recipe_id)
        for item in recipe.outputs:
            rate = item.amount_per_minute * node.machine_count
            net[item.item_id] = net.get(item.item_id, 0.0) + rate
        for item in recipe.inputs:
            rate = item.amount_per_minute * node.machine_count
            net[item.item_id] = net.get(item.item_id, 0.0) - rate
    return net


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


def added_machines(graph: ProductionGraph, existing: ProductionGraph | None) -> ProductionGraph:
    """What building `graph` adds on top of `existing`: every node `existing` already has (same
    `node_id`, and `graph` marks it `is_existing`) keeps only its machines beyond the existing
    count, and a node left with none is dropped. Flows are kept as they are — in an expansion they
    already describe only the additions. `existing=None` returns `graph` unchanged."""
    if existing is None:
        return graph
    already = {node.node_id: node.machine_count for node in existing.nodes}
    nodes = []
    for node in graph.nodes:
        count = node.machine_count
        if node.is_existing:
            count -= already.get(node.node_id, 0.0)
        if count > _RATE_TOLERANCE:
            nodes.append(replace(node, machine_count=count))
    return ProductionGraph(nodes=tuple(nodes), flows=graph.flows)


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
            output.item_id: output.amount_per_minute
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
    `_OVERCLOCK_POWER_EXPONENT`). Placements `buildings` has no entry for are skipped rather than
    raising, unlike in `power_balance`: most placed buildings — belts, foundations, storage, poles —
    draw nothing and appear in no building list."""
    return sum(
        building.power_consumption_mw * placement.clock_speed**_OVERCLOCK_POWER_EXPONENT
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
    by_id = {building.building_id: building for building in buildings}
    for placement in placements:
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
