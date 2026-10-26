"""Graph-search production planner: builds a `ProductionGraph` from raw resources up to target
items and rates, over a supplied `Recipe` list.

- Converging trees (Iron Ingot for both Plate and Screw) get one node per item: recipes are chosen
  first, then demand is summed in Kahn's order, each item after everything consuming it. Several
  targets (`plan_targets`) are summed the same way.
- Expansion stops at `raw_item_ids` and at items no recipe makes. The game has recipes producing
  most raw resources (the Converter), which would otherwise loop.
- `available_supply` (an existing factory's spare output) covers demand before any machine is
  planned; what's drawn from it enters as boundary inflows, split proportionally.
- Recipe preference, unless `recipe_choices` picks one: standard over alternates (alternates only
  for an item with no standard recipe, or one whose standard recipes all loop, like Compacted Coal
  for Turbofuel); primary output over byproduct; fewer crafted ingredients; list order. A candidate
  looping back to an item being resolved above it is skipped.
- With `game_recipes`, an item only a left-out recipe makes is a dead end, not a raw input;
  `find_missing_recipes` says which left-out recipes the plan would take.

"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass

from pioneer.common import machines_needed, recipes_for_output
from pioneer.contracts import MaterialFlow, ProductionGraph, ProductionNode, Recipe

_RATE_EPSILON = 1e-9


@dataclass(frozen=True)
class MissingRecipe:
    """An item a plan can't be made without, and the left-out recipes that would make it — any
    one of them, best first (the planner's own preference order)."""

    item_id: str
    any_of: tuple[str, ...]


def plan_production(
    target_item_id: str,
    target_rate: float,
    recipes: tuple[Recipe, ...],
    recipe_choices: dict[str, str] | None = None,
    *,
    raw_item_ids: Collection[str] = (),
    available_supply: Mapping[str, float] | None = None,
    game_recipes: tuple[Recipe, ...] | None = None,
) -> ProductionGraph:
    """Builds a `ProductionGraph` producing `target_rate` units/minute of `target_item_id`,
    expanding no further than `raw_item_ids` and planning machines only for demand
    `available_supply` doesn't cover. Raises `ValueError` if
    `target_item_id` is a raw resource or has no recipe in `recipes`, if `recipe_choices` names a
    recipe that doesn't actually produce the item it's keyed to, or if every recipe chain for the
    target loops back on itself — or, given `game_recipes`, needs an item only a recipe left out
    of `recipes` makes, more of it than `available_supply` has."""
    return plan_targets(
        {target_item_id: target_rate},
        recipes,
        recipe_choices,
        raw_item_ids=raw_item_ids,
        available_supply=available_supply,
        game_recipes=game_recipes,
    )


def plan_targets(
    targets: Mapping[str, float],
    recipes: tuple[Recipe, ...],
    recipe_choices: dict[str, str] | None = None,
    *,
    raw_item_ids: Collection[str] = (),
    available_supply: Mapping[str, float] | None = None,
    game_recipes: tuple[Recipe, ...] | None = None,
) -> ProductionGraph:
    """`plan_production` for several targets at once, each item at its rate: one graph making all
    of them. Raises what `plan_production` raises, for any one of the targets, and for none at
    all."""
    if not targets:
        raise ValueError("no target to plan for")
    raw = frozenset(raw_item_ids)
    supply = available_supply or {}
    dead_ends = _dead_ends(recipes, game_recipes, raw) if game_recipes is not None else frozenset()
    for target_item_id in targets:
        if target_item_id in raw:
            raise ValueError(
                f"{target_item_id!r} is a raw resource -- it's extracted, not crafted, so "
                f"there's nothing to plan"
            )
        if not recipes_for_output(target_item_id, recipes):
            raise ValueError(
                f"no recipe produces {target_item_id!r} -- nothing to plan (it's a raw "
                f"resource, or not in the supplied recipe list)"
            )

    # A dead end the existing factory has spare of can still be taken from it.
    blocked = frozenset(item for item in dead_ends if supply.get(item, 0.0) <= 0)
    chosen_recipes = _choose_recipes(
        tuple(targets), recipes, recipe_choices, raw, blocked, alternates_as_fallback=False
    )
    if chosen_recipes is None:
        chosen_recipes = _choose_recipes(
            tuple(targets), recipes, recipe_choices, raw, blocked, alternates_as_fallback=True
        )
    if chosen_recipes is None:
        named = ", ".join(repr(target_item_id) for target_item_id in targets)
        if blocked:
            raise ValueError(
                f"no chain of the given recipes makes {named}: every one loops back "
                f"on itself or needs an item only a left-out recipe makes"
            )
        raise ValueError(
            f"every recipe chain for {named} loops back on itself -- cyclic recipe "
            f"dependency, cannot compute a planning order"
        )

    order = _topological_order(*_requirements(tuple(targets), chosen_recipes))
    demand = _accumulate_demand(targets, order, chosen_recipes, supply)
    short = [
        item
        for item in order
        if item in dead_ends and demand.total[item] > supply.get(item, 0.0) + _RATE_EPSILON
    ]
    if short:
        raise ValueError(
            f"the plan needs more {short[0]!r} than there's spare of, and only a left-out recipe "
            f"makes it"
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
        *(
            flow
            for target_item_id, target_rate in targets.items()
            for flow in _split_flow(target_item_id, target_rate, None, demand)
        ),
    ]
    return ProductionGraph(nodes=nodes, flows=tuple(flows))


def find_missing_recipes(
    target_item_id: str,
    target_rate: float,
    recipes: tuple[Recipe, ...],
    game_recipes: tuple[Recipe, ...],
    recipe_choices: dict[str, str] | None = None,
    *,
    raw_item_ids: Collection[str] = (),
    available_supply: Mapping[str, float] | None = None,
) -> tuple[MissingRecipe, ...] | None:
    """Which of the recipes left out of `recipes` it would take to plan `target_item_id` — empty
    when `recipes` already do, `None` when not even `game_recipes` (every recipe) can.

    One entry per item the chain breaks at, nearest the target first: entries are needed together,
    the options within one are alternatives. The best option is assumed taken before looking for
    the next break, so a chain Cast Screw makes from ingots needs no Iron Rod entry."""
    raw = frozenset(raw_item_ids)

    def plans(target: str, with_recipes: tuple[Recipe, ...], supply: Mapping[str, float] | None):
        try:
            plan_production(
                target,
                target_rate,
                with_recipes,
                recipe_choices,
                raw_item_ids=raw,
                available_supply=supply,
                game_recipes=game_recipes,
            )
        except ValueError:
            return False
        return True

    if not plans(target_item_id, game_recipes, available_supply):
        return None
    current = tuple(recipes)
    missing: list[MissingRecipe] = []
    for _ in range(len(game_recipes) + 1):
        if plans(target_item_id, current, available_supply):
            return tuple(missing)
        item = _first_break(
            target_item_id,
            target_rate,
            current,
            game_recipes,
            recipe_choices,
            raw,
            available_supply or {},
        )
        if item is None:
            return None
        in_use = {recipe.recipe_id for recipe in current}
        makers = sorted(
            (r for r in recipes_for_output(item, game_recipes) if r.recipe_id not in in_use),
            key=lambda recipe: _preference(recipe, item, raw),
        )
        if not makers:
            return None
        closing = [recipe for recipe in makers if plans(item, (*current, recipe), None)]
        options = closing or makers
        missing.append(MissingRecipe(item, tuple(recipe.recipe_id for recipe in options)))
        current = (*current, options[0])
    return None


def _dead_ends(
    recipes: tuple[Recipe, ...], game_recipes: tuple[Recipe, ...], raw: frozenset[str]
) -> frozenset[str]:
    """Items a recipe of the game's makes and none of `recipes` does."""
    made = {output.item_id for recipe in recipes for output in recipe.outputs}
    return frozenset(
        output.item_id
        for recipe in game_recipes
        for output in recipe.outputs
        if output.item_id not in made and output.item_id not in raw
    )


def _first_break(
    target_item_id: str,
    target_rate: float,
    recipes: tuple[Recipe, ...],
    game_recipes: tuple[Recipe, ...],
    recipe_choices: dict[str, str] | None,
    raw: frozenset[str],
    supply: Mapping[str, float],
) -> str | None:
    """The item nearest the target that `recipes` can't make and the plan needs — found by
    planning as if such items could be had from outside, and looking at which ones were."""
    try:
        graph = plan_production(
            target_item_id,
            target_rate,
            recipes,
            recipe_choices,
            raw_item_ids=raw,
            available_supply=supply,
        )
    except ValueError:
        return target_item_id  # no recipe here makes the target, or every chain of them loops
    dead_ends = _dead_ends(recipes, game_recipes, raw)
    taken: dict[str, float] = {}
    for flow in graph.flows:
        if flow.source_node_id is None and flow.item_id in dead_ends:
            taken[flow.item_id] = taken.get(flow.item_id, 0.0) + flow.amount_per_minute
    for node in graph.nodes:  # target first
        for flow in graph.flows:
            item = flow.item_id
            if flow.target_node_id == node.node_id and flow.source_node_id is None:
                if item in taken and taken[item] > supply.get(item, 0.0) + _RATE_EPSILON:
                    return item
    return None


def _preference(recipe: Recipe, item_id: str, raw: frozenset[str]) -> tuple[bool, bool, int]:
    """The order an item's recipes are tried in — the sort being stable, the list's own order
    breaks ties."""
    byproduct = recipe.outputs[0].item_id != item_id
    crafted_inputs = sum(1 for i in recipe.inputs if i.item_id not in raw)
    return recipe.is_alternate, byproduct, crafted_inputs


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
    """`item_id`'s recipes in preference order — or just the one `recipe_choices` names for it.
    Alternates come after every standard recipe, and only at all
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

    def preference(recipe: Recipe) -> tuple[bool, bool, int]:
        return _preference(recipe, item_id, raw_item_ids)

    standard = sorted((r for r in candidates if not r.is_alternate), key=preference)
    alternates = sorted((r for r in candidates if r.is_alternate), key=preference)
    if not standard:
        return alternates
    return standard + alternates if alternates_as_fallback else standard


def _choose_recipes(
    target_item_ids: tuple[str, ...],
    recipes: tuple[Recipe, ...],
    recipe_choices: dict[str, str] | None,
    raw_item_ids: frozenset[str],
    dead_ends: frozenset[str],
    *,
    alternates_as_fallback: bool,
) -> dict[str, Recipe] | None:
    """One recipe per crafted item reachable from the targets, or `None` if every chain for one of
    them loops or runs into one of `dead_ends`.

    Depth-first: each item settles on its first candidate whose whole chain resolves without
    looping back; a failed chain is rolled back before the next candidate is tried."""
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
        if not candidates:  # a raw input as far as this recipe list goes -- unless the game
            return item_id not in dead_ends  # makes it, with a recipe left out of the list

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

    return chosen if all(resolve(target_item_id) for target_item_id in target_item_ids) else None


def _requirements(
    target_item_ids: tuple[str, ...], chosen_recipes: dict[str, Recipe]
) -> tuple[set[str], dict[str, set[str]]]:
    """Every item the chosen recipes reach from the targets, and the items each crafted one
    directly needs — the graph `_topological_order` sorts."""
    requires: dict[str, set[str]] = defaultdict(set)
    all_items = set(target_item_ids)
    to_visit = deque(target_item_ids)
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


def _topological_order(all_items: set[str], requires: dict[str, set[str]]) -> list[str]:
    """Kahn's algorithm: targets first, an item only after every item that consumes it."""
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
    targets: Mapping[str, float],
    order: list[str],
    chosen_recipes: dict[str, Recipe],
    available_supply: Mapping[str, float],
) -> _Demand:
    demand = _Demand()
    demand.total.update(targets)

    for item in order:
        recipe = chosen_recipes.get(item)
        if recipe is None:
            continue  # raw resource -- demand.total[item] is the total extraction rate needed
        covered = min(demand.total[item], max(available_supply.get(item, 0.0), 0.0))
        if covered > 0:
            demand.supplied[item] = covered
        rate = recipe.output_rate(item) or 0.0  # chosen among the recipes making `item`
        machines = machines_needed(rate, demand.total[item] - covered)
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
