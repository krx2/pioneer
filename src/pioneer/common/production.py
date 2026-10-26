"""Counting over items, recipes and production graphs."""

from __future__ import annotations

import math
from collections.abc import Iterable

from pioneer.contracts import Item, ProductionGraph, Recipe


def raw_resource_ids(items: Iterable[Item]) -> frozenset[str]:
    """Every raw (extracted, not crafted) resource among `items`."""
    return frozenset(item.item_id for item in items if item.is_raw_resource)


def recipes_for_output(item_id: str, recipes: Iterable[Recipe]) -> tuple[Recipe, ...]:
    """Every recipe that produces `item_id`, in `recipes`' own order. Empty means `item_id` is a
    raw resource as far as these recipes go."""
    return tuple(recipe for recipe in recipes if recipe.produces(item_id))


def machines_needed(per_machine_rate: float, target_rate: float) -> int:
    """Whole machines making `target_rate`, each `per_machine_rate`: rounded up, none for no
    target. Raises `ValueError` for a rate that isn't positive."""
    if target_rate <= 0:
        return 0
    if per_machine_rate <= 0:
        raise ValueError(f"no positive output rate to make {target_rate:g}/min with")
    return math.ceil(target_rate / per_machine_rate)


def machines_by_recipe(graph: ProductionGraph | None) -> dict[str, float]:
    """How many machines run each recipe in `graph`, all its nodes running one taken together."""
    machines: dict[str, float] = {}
    for node in graph.nodes if graph is not None else ():
        machines[node.recipe_id] = machines.get(node.recipe_id, 0.0) + node.machine_count
    return machines
