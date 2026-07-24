"""The assembled knowledge base and its pure lookup functions.

`KnowledgeBase` is this module's own aggregate container (not a Stage 1 contract — other modules
never construct one themselves, they only ever receive `Recipe`/`Building`/`Technology` tuples
produced by the lookup functions below).
"""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.contracts import Building, Recipe, Technology


@dataclass(frozen=True)
class KnowledgeBase:
    recipes: tuple[Recipe, ...]
    buildings: tuple[Building, ...]
    technologies: tuple[Technology, ...]


def recipe_by_id(kb: KnowledgeBase, recipe_id: str) -> Recipe | None:
    return next((r for r in kb.recipes if r.recipe_id == recipe_id), None)


def recipes_for_output(kb: KnowledgeBase, item_id: str) -> tuple[Recipe, ...]:
    """Every recipe that produces `item_id` — the alternate-recipe choice set for that item."""
    return tuple(r for r in kb.recipes if any(o.item_id == item_id for o in r.outputs))


def building_for(kb: KnowledgeBase, building_id: str) -> Building | None:
    return next((b for b in kb.buildings if b.building_id == building_id), None)


def technology_by_id(kb: KnowledgeBase, technology_id: str) -> Technology | None:
    return next((t for t in kb.technologies if t.technology_id == technology_id), None)


def technology_for_recipe(kb: KnowledgeBase, recipe_id: str) -> Technology | None:
    """The `Technology` that unlocks `recipe_id`, if any."""
    recipe = recipe_by_id(kb, recipe_id)
    if recipe is None or recipe.unlocked_by is None:
        return None
    return technology_by_id(kb, recipe.unlocked_by)


def prerequisites_for_technology(kb: KnowledgeBase, technology_id: str) -> tuple[str, ...]:
    """`Technology.technology_id`s that must be unlocked before `technology_id`.

    Genuinely empty for milestone/MAM technologies — see loader.py's module docstring for why.
    """
    technology = technology_by_id(kb, technology_id)
    return technology.prerequisites if technology is not None else ()
