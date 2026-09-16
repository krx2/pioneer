"""The assembled knowledge base and its pure lookup functions.

`KnowledgeBase` is this module's own aggregate container (not a Stage 1 contract — other modules
never construct one themselves, they only ever receive `Recipe`/`Building`/`Technology`/`Item`
tuples produced by the lookup functions below).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pioneer.contracts import Building, Item, Recipe, Technology


@dataclass(frozen=True)
class KnowledgeBase:
    recipes: tuple[Recipe, ...]
    buildings: tuple[Building, ...]
    technologies: tuple[Technology, ...]
    items: tuple[Item, ...] = ()


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


def item_by_id(kb: KnowledgeBase, item_id: str) -> Item | None:
    return next((i for i in kb.items if i.item_id == item_id), None)


def raw_resource_ids(kb: KnowledgeBase) -> frozenset[str]:
    """Every raw (extracted, not crafted) resource — what the Production Planner stops at."""
    return frozenset(i.item_id for i in kb.items if i.is_raw_resource)


def find_items(items: Iterable[Item], query: str, *, limit: int = 5) -> tuple[Item, ...]:
    """Items matching `query`, best first: exact id, exact name, name starting with the query,
    every query word matching some word of the name (in any order, and forgiving plurals, so
    "reinforced plates" finds Reinforced Iron Plate — see `_word_matches`), then id containing the
    query. Case-insensitive; ties go to the shorter name. Takes the items directly rather than a
    `KnowledgeBase`, so callers holding just the tuple — like the Orchestrator's context — can
    search too."""
    needle = query.strip().casefold()
    if not needle:
        return ()
    needle_words = needle.split()

    ranked: list[tuple[int, int, int, Item]] = []
    for index, item in enumerate(items):
        item_id = item.item_id.casefold()
        name = item.name.casefold()
        name_words = name.split()
        if needle == item_id:
            rank = 0
        elif needle == name:
            rank = 1
        elif name.startswith(needle):
            rank = 2
        elif all(any(_word_matches(n, word) for word in name_words) for n in needle_words):
            rank = 3
        elif needle in item_id:
            rank = 4
        else:
            continue
        ranked.append((rank, len(item.name), index, item))

    ranked.sort(key=lambda entry: entry[:3])
    return tuple(entry[3] for entry in ranked[:limit])


def _word_matches(query_word: str, name_word: str) -> bool:
    """`query_word` starts `name_word` ("reinf" -> "reinforced"), or is `name_word` with something
    tacked on — a plural, mostly ("plates" -> "plate")."""
    return name_word.startswith(query_word) or (
        len(name_word) >= 3 and query_word.startswith(name_word)
    )
