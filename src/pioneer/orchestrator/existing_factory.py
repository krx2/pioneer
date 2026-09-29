"""The player's existing factory as the Verifier sees it: its net per-item balance, its
power draw and capacity, and graphs of its sites and of the chains behind its items -- what
expansion, diagnosis and the factory tools all start from."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

from pioneer.contracts import (
    Coordinates,
    FactorySite,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
    Recipe,
)
from pioneer.orchestrator.base import _CM_PER_M, OrchestratorContext
from pioneer.orchestrator.items import _uncraftable_item_ids, raw_resource_ids
from pioneer.verifier import (
    balance,
    distance,
    extraction_rates,
    generator_byproducts,
    generator_fuel_demand,
    generator_supplemental_demand,
    placed_generation_capacity_mw,
    placed_power_consumption_mw,
    power_balance,
)


def _site_graph(site: FactorySite) -> ProductionGraph:
    """One factory's buildings as a graph, by the save parser's rules: a node per recipe, its
    machines its buildings' clock speeds summed, its boost theirs averaged by clock speed."""
    machines: dict[str, float] = defaultdict(float)
    boosted: dict[str, float] = defaultdict(float)
    building_of: dict[str, str] = {}
    for placement in site.placements:
        if placement.recipe_id is None or placement.is_paused:
            continue
        machines[placement.recipe_id] += placement.clock_speed
        boosted[placement.recipe_id] += placement.clock_speed * placement.production_boost
        building_of.setdefault(placement.recipe_id, placement.building_id)
    return ProductionGraph(
        nodes=tuple(
            ProductionNode(
                node_id=f"save_{recipe_id}",
                recipe_id=recipe_id,
                building_id=building_of[recipe_id],
                machine_count=count,
                is_existing=True,
                existing_machine_count=count,
                production_boost=boosted[recipe_id] / count if count > 0 else 1.0,
            )
            for recipe_id, count in machines.items()
        ),
        flows=(),
    )


def _chain_graph(
    graph: ProductionGraph, item_id: str, recipes: tuple[Recipe, ...]
) -> ProductionGraph:
    """The part of `graph` that makes `item_id`: the stages making it, the stages making their
    inputs, and so on down to what comes from outside the factory."""
    recipe_of = {recipe.recipe_id: recipe for recipe in recipes}
    makers: dict[str, list[ProductionNode]] = defaultdict(list)
    for node in graph.nodes:
        for product in recipe_of[node.recipe_id].outputs if node.recipe_id in recipe_of else ():
            makers[product.item_id].append(node)
    kept: dict[str, ProductionNode] = {}
    wanted, seen = [item_id], set()
    while wanted:
        current = wanted.pop()
        if current in seen:
            continue
        seen.add(current)
        for node in makers.get(current, ()):
            if node.node_id not in kept:
                kept[node.node_id] = node
                wanted.extend(i.item_id for i in recipe_of[node.recipe_id].inputs)
    return ProductionGraph(nodes=tuple(kept.values()), flows=())


def _node_links(
    graph: ProductionGraph, members: Iterable[PlacementRecord], context: OrchestratorContext
) -> set[tuple[str, str]] | None:
    """Which of `graph`'s nodes the save's belts and pipes join: a link from one of `members` to
    another joins the nodes of the recipes they run. `None` without link data -- the drawing then
    shares its flows out by the recipes alone."""
    if not context.existing_links:
        return None
    node_ids = {node.node_id for node in graph.nodes}
    node_of = {
        placement.object_id: f"save_{placement.recipe_id}"
        for placement in members
        if placement.object_id and placement.recipe_id and not placement.is_paused
    }
    return {
        (node_of[link.source_id], node_of[link.target_id])
        for link in context.existing_links
        if node_of.get(link.source_id) in node_ids and node_of.get(link.target_id) in node_ids
    }


def _existing_item_balance(context: OrchestratorContext) -> dict[str, float]:
    """The player's factory's net per-item rate: what its recipes make, its extractors pull out of
    the ground and its generators leave behind, minus what its recipes and its generators consume
    -- a factory burning its own Fuel isn't overproducing it, and the water its coal generators
    drink isn't spare."""
    assert context.existing_graph is not None
    return _item_balance(context.existing_graph, context.existing_placements, context)


def _item_balance(
    graph: ProductionGraph, placements: Sequence[PlacementRecord], context: OrchestratorContext
) -> dict[str, float]:
    """`_existing_item_balance` of `graph` and the `placements` it stands on -- the whole factory,
    or one of its sites."""
    net = balance(graph, context.recipes)
    _add_rates(net, extraction_rates(placements, context.buildings, context.resource_nodes))
    _add_rates(net, generator_byproducts(placements, context.buildings, context.items))
    _add_rates(net, _generator_consumption(context, placements), sign=-1.0)
    return net


def _generator_consumption(
    context: OrchestratorContext, placements: Sequence[PlacementRecord] | None = None
) -> dict[str, float]:
    """What the placed generators consume per minute: their fuel and any supplemental water."""
    placements = context.existing_placements if placements is None else placements
    buildings = context.buildings
    consumed = generator_fuel_demand(placements, buildings, context.items)
    _add_rates(consumed, generator_supplemental_demand(placements, buildings))
    return consumed


def _add_rates(totals: dict[str, float], rates: Mapping[str, float], sign: float = 1.0) -> None:
    for item_id, rate in rates.items():
        totals[item_id] = totals.get(item_id, 0.0) + sign * rate


def _consuming_nodes(
    context: OrchestratorContext, *, shared: Collection[str]
) -> dict[str, set[str]]:
    """Per item, the existing graph's nodes whose recipe consumes it -- leaving out the `shared`
    items something outside the graph (a generator) consumes too, which no node alone is to blame
    for running short."""
    assert context.existing_graph is not None
    recipes = {recipe.recipe_id: recipe for recipe in context.recipes}
    consumers: dict[str, set[str]] = defaultdict(set)
    for node in context.existing_graph.nodes:
        recipe = recipes.get(node.recipe_id)
        for ingredient in recipe.inputs if recipe is not None else ():
            if ingredient.item_id not in shared:
                consumers[ingredient.item_id].add(node.node_id)
    return consumers


def _existing_power(context: OrchestratorContext) -> tuple[float, float | None]:
    """(draw, capacity) in MW. Taken from every placed building when the save's placements are
    loaded -- extractors, pumps and generators included -- otherwise just the recipe graph's own
    draw, against whatever capacity the context was given (`None`: unknown, so never a blackout).
    """
    if context.existing_placements and context.buildings:
        draw = placed_power_consumption_mw(context.existing_placements, context.buildings)
        capacity = (
            context.available_power_mw
            if context.available_power_mw is not None
            else placed_generation_capacity_mw(context.existing_placements, context.buildings)
        )
        return draw, capacity

    assert context.existing_graph is not None
    draw = power_balance(context.existing_graph, context.buildings) if context.buildings else 0.0
    return draw, context.available_power_mw


def _inputs_from_outside(context: OrchestratorContext, item_ids: Iterable[str]) -> frozenset[str]:
    """Items diagnosis doesn't judge as short: hand-gathered ones always, and raw resources unless
    resource node data is loaded -- without it, what the extractors mine is unknown and every ore
    would look short."""
    raw = raw_resource_ids(context)
    gathered = _uncraftable_item_ids(context, item_ids) - raw
    return gathered if context.resource_nodes else gathered | raw


def reference_point(args: dict[str, Any], context: OrchestratorContext) -> Coordinates:
    """The point the model asked to measure from; else the middle of the player's buildings --
    their base, near enough -- else the map origin."""
    if any(key in args for key in ("reference_x", "reference_y", "reference_z")):
        return Coordinates(
            x=float(args.get("reference_x", 0.0)),
            y=float(args.get("reference_y", 0.0)),
            z=float(args.get("reference_z", 0.0)),
        )
    placements = context.existing_placements
    if not placements:
        return Coordinates(x=0.0, y=0.0, z=0.0)
    return Coordinates(
        x=sum(p.position.x for p in placements) / len(placements),
        y=sum(p.position.y for p in placements) / len(placements),
        z=sum(p.position.z for p in placements) / len(placements),
    )


def _main_site(recipe_id: str, context: OrchestratorContext) -> FactorySite | None:
    """The factory site running the most of `recipe_id`, by clock speed."""

    def running(site: FactorySite) -> float:
        return sum(p.clock_speed for p in site.placements if p.recipe_id == recipe_id)

    best = max(context.factory_sites, key=running, default=None)
    return best if best is not None and running(best) > 0 else None


def _site_summary(site: FactorySite, context: OrchestratorContext) -> dict[str, Any]:
    reference = reference_point({}, context)
    counts: dict[str, float] = {}
    for placement in site.placements:
        if placement.recipe_id is not None:
            counts[placement.recipe_id] = counts.get(placement.recipe_id, 0.0) + 1
    return {
        "x": site.position.x,
        "y": site.position.y,
        "z": site.position.z,
        "distance_from_base_m": distance(site.position, reference) / _CM_PER_M,
        "buildings": len(site.placements),
        "main_recipes": sorted(counts, key=lambda r: -counts[r])[:3],
    }


def _with_spare_power(result: dict[str, Any], context: OrchestratorContext) -> dict[str, Any]:
    """`result`, plus what the player's grid has to spare, when the save tells."""
    spare = spare_power_mw(context)
    return result if spare is None else {**result, "grid_spare_power_mw": spare}


def spare_power_mw(context: OrchestratorContext) -> float | None:
    """What the player's grid can still supply: its capacity minus its draw, from the save. `None`
    with no save loaded, or no way to tell the capacity."""
    if context.existing_graph is None:
        return None
    try:
        draw, capacity = _existing_power(context)
    except ValueError:
        return None
    return None if capacity is None else capacity - draw
