"""BFS/graph-search production planner: given a target item + rate, builds a `ProductionGraph`
from raw resources up to the target, over a supplied `Recipe` list.

**Demand aggregation.** Real crafting trees converge — e.g. Reinforced Iron Plate needs both Iron
Plate and Screw, and both of *those* need Iron Ingot. A naive recursive expansion would either
double-count Iron Ingot demand or build two separate Iron Ingot nodes; this planner instead runs
two passes: first discovers the full set of items/recipes involved and a valid processing order
(via Kahn's algorithm — an item's total demand can only be finalized once every recipe that
consumes it has already contributed its share), then walks that order once, accumulating demand
and computing exactly one node per item.

**Alternate-recipe awareness.** `recipes_for_output` is the "expose the choice" half of this
module's job — nothing about which recipe gets used is hidden. `plan_production` takes an optional
`recipe_choices` mapping (item_id -> recipe_id) so a caller can pick among alternates explicitly;
left unspecified, the first recipe `recipes_for_output` returns for that item is used (documented
default, not a silent one).

Doesn't import the Knowledge Base, the Verifier, or any other module — `recipes` arrives as a
plain `Recipe` tuple from the caller, and machine-count arithmetic is duplicated locally in
miniature (same pattern resource_db uses for `distance`) rather than reaching into `verifier`.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Iterator

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
) -> ProductionGraph:
    """Builds a from-scratch `ProductionGraph` producing `target_rate` units/minute of
    `target_item_id`. Raises `ValueError` if `target_item_id` has no recipe in `recipes`, if
    `recipe_choices` names a recipe that doesn't actually produce the item it's keyed to, or if
    `recipes` contains a cyclic dependency (which a real recipe list shouldn't)."""
    chosen_recipes, order = _discover(target_item_id, recipes, recipe_choices)
    if target_item_id not in chosen_recipes:
        raise ValueError(
            f"no recipe produces {target_item_id!r} -- nothing to plan (it's a raw resource, "
            f"or not in the supplied recipe list)"
        )
    machine_counts, demand = _accumulate_demand(target_item_id, target_rate, order, chosen_recipes)

    nodes = tuple(
        ProductionNode(
            node_id=_node_id(item_id),
            recipe_id=chosen_recipes[item_id].recipe_id,
            building_id=chosen_recipes[item_id].building_ids[0],
            machine_count=machine_counts[item_id],
        )
        for item_id in order
        if item_id in chosen_recipes
    )

    flows = list(_internal_flows(order, chosen_recipes, machine_counts))
    flows.append(
        MaterialFlow(
            item_id=target_item_id,
            amount_per_minute=target_rate,
            source_node_id=_node_id(target_item_id),
            target_node_id=None,
        )
    )

    return ProductionGraph(nodes=nodes, flows=tuple(flows))


def _node_id(item_id: str) -> str:
    return f"node_{item_id}"


def _choose_recipe(
    item_id: str, recipes: tuple[Recipe, ...], recipe_choices: dict[str, str] | None
) -> Recipe | None:
    candidates = recipes_for_output(item_id, recipes)
    if not candidates:
        return None
    chosen_id = (recipe_choices or {}).get(item_id)
    if chosen_id is None:
        return candidates[0]
    match = next((r for r in candidates if r.recipe_id == chosen_id), None)
    if match is None:
        raise ValueError(
            f"recipe_choices specifies {chosen_id!r} for {item_id!r}, but no recipe with that "
            f"id produces {item_id!r}"
        )
    return match


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


def _discover(
    target_item_id: str, recipes: tuple[Recipe, ...], recipe_choices: dict[str, str] | None
) -> tuple[dict[str, Recipe], list[str]]:
    """BFS from the target, picking a recipe per item as it's reached, plus a Kahn's-algorithm
    topological order (target first, an item only after every item that consumes it)."""
    chosen: dict[str, Recipe] = {}
    requires: dict[str, set[str]] = defaultdict(set)  # item -> items its recipe directly needs
    all_items = {target_item_id}

    to_visit = deque([target_item_id])
    seen = {target_item_id}
    while to_visit:
        item = to_visit.popleft()
        recipe = _choose_recipe(item, recipes, recipe_choices)
        if recipe is None:
            continue
        chosen[item] = recipe
        for ingredient in recipe.inputs:
            requires[item].add(ingredient.item_id)
            all_items.add(ingredient.item_id)
            if ingredient.item_id not in seen:
                seen.add(ingredient.item_id)
                to_visit.append(ingredient.item_id)

    order = _topological_order(all_items, requires)
    return chosen, order


def _topological_order(all_items: set[str], requires: dict[str, set[str]]) -> list[str]:
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


def _accumulate_demand(
    target_item_id: str,
    target_rate: float,
    order: list[str],
    chosen_recipes: dict[str, Recipe],
) -> tuple[dict[str, int], dict[str, float]]:
    demand: dict[str, float] = defaultdict(float)
    demand[target_item_id] = target_rate
    machine_counts: dict[str, int] = {}

    for item in order:
        recipe = chosen_recipes.get(item)
        if recipe is None:
            continue  # raw resource -- demand[item] is the total extraction rate needed
        per_machine_rate = _output_rate(recipe, item)
        machines = _machine_count(per_machine_rate, demand[item])
        machine_counts[item] = machines
        for ingredient in recipe.inputs:
            demand[ingredient.item_id] += ingredient.amount_per_minute * machines

    return machine_counts, demand


def _internal_flows(
    order: list[str], chosen_recipes: dict[str, Recipe], machine_counts: dict[str, int]
) -> Iterator[MaterialFlow]:
    for item in order:
        recipe = chosen_recipes.get(item)
        if recipe is None:
            continue
        machines = machine_counts[item]
        for ingredient in recipe.inputs:
            amount = ingredient.amount_per_minute * machines
            source_node_id = (
                _node_id(ingredient.item_id) if ingredient.item_id in chosen_recipes else None
            )
            yield MaterialFlow(
                item_id=ingredient.item_id,
                amount_per_minute=amount,
                source_node_id=source_node_id,
                target_node_id=_node_id(item),
            )
