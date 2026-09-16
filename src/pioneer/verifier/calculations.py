"""The deterministic arithmetic core: machine counts, throughput balance, power balance, distance.

Every function here is pure and takes Stage 1 contract data straight from its caller. Unlike
Knowledge Base or Resource DB, this module owns no aggregate of its own — there's no `Verifier`
container, just functions — since it has no data to load, only calculations to run over whatever
`Recipe`/`Building` list the caller (eventually the real Knowledge Base) hands it.

Power has two views. `power_balance` is a production graph's own draw — what a *plan* needs.
`placed_power_consumption_mw`, `placed_generation_capacity_mw` and `generator_fuel_demand` work
over a save's placements instead — every building actually standing, including the extractors,
pumps and generators a recipe graph never contains — which is what checking an existing factory
for blackouts, or for fuel its generators burn, has to look at.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence

from pioneer.contracts import (
    Building,
    Coordinates,
    Item,
    PlacementRecord,
    ProductionGraph,
    Recipe,
)

_OVERCLOCK_POWER_EXPONENT = 1.321929
"""How power draw scales with clock speed — the game's `mPowerConsumptionExponent` for production
buildings and extractors: at 250% a machine draws 2.5 ** 1.32 ≈ 3.4x its rated power, not 2.5x.
Generator output, by contrast, scales linearly with clock speed."""


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
    energy = {item.item_id: item.energy_value_mj for item in items if item.energy_value_mj > 0}
    demand: dict[str, float] = {}
    for placement, building in _placed_buildings(placements, buildings):
        fuel = placement.fuel_item_id
        if building.power_consumption_mw >= 0 or fuel is None or fuel not in energy:
            continue
        output_mw = -building.power_consumption_mw * placement.clock_speed
        demand[fuel] = demand.get(fuel, 0.0) + output_mw / energy[fuel] * 60.0
    return demand


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
