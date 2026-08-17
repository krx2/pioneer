"""The deterministic arithmetic core: machine counts, throughput balance, power balance, distance.

Every function here is pure and takes Stage 1 contract data straight from its caller. Unlike
Knowledge Base or Resource DB, this module owns no aggregate of its own — there's no `Verifier`
container, just functions — since it has no data to load, only calculations to run over whatever
`Recipe`/`Building` list the caller (eventually the real Knowledge Base) hands it.
"""

from __future__ import annotations

import math

from pioneer.contracts import Building, Coordinates, ProductionGraph, Recipe


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
