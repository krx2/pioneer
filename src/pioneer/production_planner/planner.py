"""Graph-search production planner: given a target item + rate, builds a `ProductionGraph` from
raw resources up to the target, over a supplied `Recipe` list.

**Demand aggregation.** Real crafting trees converge — e.g. Reinforced Iron Plate needs both Iron
Plate and Screw, and both of *those* need Iron Ingot. A naive recursive expansion would either
double-count Iron Ingot demand or build two separate Iron Ingot nodes; this planner instead runs
two passes: first picks one recipe per item and a valid processing order (via Kahn's algorithm —
an item's total demand can only be finalized once every recipe that consumes it has already
contributed its share), then walks that order once, accumulating demand and computing exactly one
node per item.

**Where expansion stops.** At `raw_item_ids` (the caller's list of extracted resources — ores,
water, crude oil, ...) and at any item no recipe produces. The first matters on real data: the
game has recipes *producing* most raw resources (1.0's Converter turns one ore into another,
unpackaging yields crude oil), and following them walks ore -> ore -> ore in a circle.

**Planning on top of what's already there.** `available_supply` is per-minute supply of items on
hand outside the plan — when expanding a factory, its spare output (the Verifier's positive
balance). Each item's demand draws on that supply before any machine is planned for it, so a
covered item gets no new machines, and neither does anything upstream of it. What the plan draws
from supply shows up as boundary inflows (`source_node_id=None`), like a raw input; where supply
covers only part of an item, each consumer's flow of it is split between the two sources in the
same proportion.

**Recipe choice.** `recipes_for_output` is the "expose the choice" half of this module's job —
nothing about which recipe gets used is hidden. `plan_production` takes an optional
`recipe_choices` mapping (item_id -> recipe_id) so a caller can pick among alternates explicitly.
Left unspecified, an item's recipe is picked by a documented preference, best first:

1. standard recipes over alternates (`Recipe.is_alternate`) — the player may not have researched
   the alternates. They're used only for an item with no standard recipe at all, or, when *no*
   plan exists without them, for an item whose standard recipes all loop: 1.0's Turbofuel needs
   Compacted Coal, whose only standard sources are byproducts of Rocket Fuel and Ionized Fuel —
   both made from Turbofuel — so only "Alternate: Compacted Coal" breaks the circle;
2. recipes making the item as their primary (first) output over ones yielding it as a byproduct;
3. fewer crafted (non-raw) ingredients, i.e. the shallower chain;
4. the recipe list's own order.

A candidate whose ingredients lead back to an item already being resolved further up the chain is
skipped for the next one — so Fuel never gets planned as "unpackage Packaged Fuel, which is made
from Fuel". Only when every candidate for the target loops is the plan refused.

Doesn't import the Knowledge Base, the Verifier, or any other module — `recipes` arrives as a
plain `Recipe` tuple from the caller, and machine-count arithmetic is duplicated locally in
miniature (same pattern resource_db uses for `distance`) rather than reaching into `verifier`.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Collection, Iterator, Mapping

from pioneer.contracts import MaterialFlow, ProductionGraph, ProductionNode, Recipe


def recipes_for_output(item_id: str, recipes: tuple[Recipe, ...]) -> tuple[Recipe, ...]:
    """Every recipe that produces `item_id` — the alternate-recipe choice set for that item, in
    `recipes`' own order. Empty means `item_id` is a raw resource as far as this recipe list goes
    (extracted, not crafted — outside this module's job)."""
    return tuple(r for r in recipes if any(o.item_id == item_id for o in r.outputs))


def plan_production(
    target_item_id: str,
    target_rate: float,
    recipes: tuple[Recipe, ...],
    recipe_choices: dict[str, str] | None = None,
    *,
    raw_item_ids: Collection[str] = (),
    available_supply: Mapping[str, float] | None = None,
) -> ProductionGraph:
    """Builds a `ProductionGraph` producing `target_rate` units/minute of `target_item_id`,
    expanding no further than `raw_item_ids` and planning machines only for demand
    `available_supply` doesn't cover (see module docstring). Raises `ValueError` if
    `target_item_id` is a raw resource or has no recipe in `recipes`, if `recipe_choices` names a
    recipe that doesn't actually produce the item it's keyed to, or if every recipe chain for the
    target loops back on itself."""
    raw = frozenset(raw_item_ids)
    if target_item_id in raw:
        raise ValueError(
            f"{target_item_id!r} is a raw resource -- it's extracted, not crafted, so there's "
            f"nothing to plan"
        )
    if not recipes_for_output(target_item_id, recipes):
        raise ValueError(
            f"no recipe produces {target_item_id!r} -- nothing to plan (it's a raw resource, "
            f"or not in the supplied recipe list)"
        )

    chosen_recipes = _choose_recipes(
        target_item_id, recipes, recipe_choices, raw, alternates_as_fallback=False
    )
    if chosen_recipes is None:
        chosen_recipes = _choose_recipes(
            target_item_id, recipes, recipe_choices, raw, alternates_as_fallback=True
        )
    if chosen_recipes is None:
        raise ValueError(
            f"every recipe chain for {target_item_id!r} loops back on itself -- cyclic recipe "
            f"dependency, cannot compute a planning order"
        )

    order = _topological_order(*_requirements(target_item_id, chosen_recipes))
    demand = _accumulate_demand(
        target_item_id, target_rate, order, chosen_recipes, available_supply or {}
    )

    nodes = tuple(
        ProductionNode(
            node_id=_node_id(item_id),
            recipe_id=chosen_recipes[item_id].recipe_id,
            building_id=chosen_recipes[item_id].building_ids[0],
            machine_count=demand.machines[item_id],
        )
        for item_id in order
        if demand.machines.get(item_id, 0) > 0
    )
    flows = [
        *_internal_flows(order, chosen_recipes, demand),
        *_split_flow(target_item_id, target_rate, None, demand),
    ]
    return ProductionGraph(nodes=nodes, flows=tuple(flows))


def _node_id(item_id: str) -> str:
    return f"node_{item_id}"


def _ranked_candidates(
    item_id: str,
    recipes: tuple[Recipe, ...],
    recipe_choices: dict[str, str] | None,
    raw_item_ids: frozenset[str],
    *,
    alternates_as_fallback: bool,
) -> list[Recipe]:
    """`item_id`'s recipes in the preference order from the module docstring — or just the one
    `recipe_choices` names for it. Alternates come after every standard recipe, and only at all
    when there's no standard recipe or `alternates_as_fallback` is set."""
    candidates = recipes_for_output(item_id, recipes)
    if not candidates:
        return []

    chosen_id = (recipe_choices or {}).get(item_id)
    if chosen_id is not None:
        match = next((r for r in candidates if r.recipe_id == chosen_id), None)
        if match is None:
            raise ValueError(
                f"recipe_choices specifies {chosen_id!r} for {item_id!r}, but no recipe with that "
                f"id produces {item_id!r}"
            )
        return [match]

    def preference(recipe: Recipe) -> tuple[bool, int]:
        byproduct = recipe.outputs[0].item_id != item_id
        crafted_inputs = sum(1 for i in recipe.inputs if i.item_id not in raw_item_ids)
        return byproduct, crafted_inputs

    standard = sorted((r for r in candidates if not r.is_alternate), key=preference)
    alternates = sorted((r for r in candidates if r.is_alternate), key=preference)
    if not standard:
        return alternates
    return standard + alternates if alternates_as_fallback else standard


def _choose_recipes(
    target_item_id: str,
    recipes: tuple[Recipe, ...],
    recipe_choices: dict[str, str] | None,
    raw_item_ids: frozenset[str],
    *,
    alternates_as_fallback: bool,
) -> dict[str, Recipe] | None:
    """One recipe per crafted item reachable from the target, or `None` if every chain loops.

    Picked depth-first: each item tries its candidates best-first and settles on the first whose
    whole ingredient chain resolves without looping back to an item still being resolved above
    it. A chain that fails is rolled back before the next candidate is tried, so no half-explored
    choice leaks into the plan."""
    chosen: dict[str, Recipe] = {}
    in_progress: set[str] = set()

    def resolve(item_id: str) -> bool:
        if item_id in chosen or item_id in raw_item_ids:
            return True
        candidates = _ranked_candidates(
            item_id,
            recipes,
            recipe_choices,
            raw_item_ids,
            alternates_as_fallback=alternates_as_fallback,
        )
        if not candidates:
            return True  # nothing makes it -- a raw input as far as this recipe list goes

        in_progress.add(item_id)
        try:
            for recipe in candidates:
                if any(ingredient.item_id in in_progress for ingredient in recipe.inputs):
                    continue
                resolved_before = set(chosen)
                if all(resolve(ingredient.item_id) for ingredient in recipe.inputs):
                    chosen[item_id] = recipe
                    return True
                for abandoned in chosen.keys() - resolved_before:
                    del chosen[abandoned]
            return False
        finally:
            in_progress.discard(item_id)

    return chosen if resolve(target_item_id) else None


def _requirements(
    target_item_id: str, chosen_recipes: dict[str, Recipe]
) -> tuple[set[str], dict[str, set[str]]]:
    """Every item the chosen recipes reach from the target, and the items each crafted one
    directly needs — the graph `_topological_order` sorts."""
    requires: dict[str, set[str]] = defaultdict(set)
    all_items = {target_item_id}
    to_visit = deque([target_item_id])
    while to_visit:
        item = to_visit.popleft()
        recipe = chosen_recipes.get(item)
        if recipe is None:
            continue
        for ingredient in recipe.inputs:
            requires[item].add(ingredient.item_id)
            if ingredient.item_id not in all_items:
                all_items.add(ingredient.item_id)
                to_visit.append(ingredient.item_id)
    return all_items, requires


def _output_rate(recipe: Recipe, item_id: str) -> float:
    """The specific per-machine rate at which `recipe` produces `item_id` -- not just its first
    output, since a multi-output recipe might list the item we care about second."""
    for output in recipe.outputs:
        if output.item_id == item_id:
            return output.amount_per_minute
    raise ValueError(f"recipe {recipe.recipe_id!r} doesn't actually produce {item_id!r}")


def _machine_count(per_machine_rate: float, target_rate: float) -> int:
    if target_rate <= 0:
        return 0
    if per_machine_rate <= 0:
        raise ValueError("recipe has no positive output rate for the item being planned")
    return math.ceil(target_rate / per_machine_rate)


def _topological_order(all_items: set[str], requires: dict[str, set[str]]) -> list[str]:
    """Kahn's algorithm: target first, an item only after every item that consumes it."""
    in_degree: dict[str, int] = dict.fromkeys(all_items, 0)
    for needed_items in requires.values():
        for needed in needed_items:
            in_degree[needed] += 1

    queue = deque(item for item, degree in in_degree.items() if degree == 0)
    order: list[str] = []
    while queue:
        item = queue.popleft()
        order.append(item)
        for needed in requires.get(item, ()):
            in_degree[needed] -= 1
            if in_degree[needed] == 0:
                queue.append(needed)

    if len(order) != len(all_items):
        raise ValueError("cyclic recipe dependency detected -- cannot compute a planning order")
    return order


class _Demand:
    """Per item: total demand, how much of it outside supply covered, and the machines planned for
    the rest (crafted items only)."""

    def __init__(self) -> None:
        self.total: dict[str, float] = defaultdict(float)
        self.supplied: dict[str, float] = {}
        self.machines: dict[str, int] = {}

    def supplied_fraction(self, item_id: str) -> float:
        total = self.total.get(item_id, 0.0)
        return self.supplied.get(item_id, 0.0) / total if total > 0 else 0.0


def _accumulate_demand(
    target_item_id: str,
    target_rate: float,
    order: list[str],
    chosen_recipes: dict[str, Recipe],
    available_supply: Mapping[str, float],
) -> _Demand:
    demand = _Demand()
    demand.total[target_item_id] = target_rate

    for item in order:
        recipe = chosen_recipes.get(item)
        if recipe is None:
            continue  # raw resource -- demand.total[item] is the total extraction rate needed
        covered = min(demand.total[item], max(available_supply.get(item, 0.0), 0.0))
        if covered > 0:
            demand.supplied[item] = covered
        machines = _machine_count(_output_rate(recipe, item), demand.total[item] - covered)
        demand.machines[item] = machines
        for ingredient in recipe.inputs:
            demand.total[ingredient.item_id] += ingredient.amount_per_minute * machines

    return demand


def _internal_flows(
    order: list[str], chosen_recipes: dict[str, Recipe], demand: _Demand
) -> Iterator[MaterialFlow]:
    for item in order:
        machines = demand.machines.get(item, 0)
        if machines == 0:
            continue  # raw, or wholly covered by supply: there's no node here consuming anything
        for ingredient in chosen_recipes[item].inputs:
            yield from _split_flow(
                ingredient.item_id,
                ingredient.amount_per_minute * machines,
                _node_id(item),
                demand,
            )


def _split_flow(
    item_id: str, amount: float, target_node_id: str | None, demand: _Demand
) -> Iterator[MaterialFlow]:
    """`amount` of `item_id` into `target_node_id` (`None`: out of the graph as its output), drawn
    from the planned node making it and from outside supply, in the proportion supply covers the
    item's total demand. Raw and wholly-supplied items have no node, so it all enters from outside.
    """
    if demand.machines.get(item_id, 0) > 0:
        from_outside = amount * demand.supplied_fraction(item_id)
        from_node = amount - from_outside
        if from_node > 0:
            yield MaterialFlow(
                item_id=item_id,
                amount_per_minute=from_node,
                source_node_id=_node_id(item_id),
                target_node_id=target_node_id,
            )
    else:
        from_outside = amount
    if from_outside > 0:
        yield MaterialFlow(
            item_id=item_id,
            amount_per_minute=from_outside,
            source_node_id=None,
            target_node_id=target_node_id,
        )
