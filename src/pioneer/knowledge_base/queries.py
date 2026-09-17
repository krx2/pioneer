"""The assembled knowledge base and its pure lookup functions.

`KnowledgeBase` is this module's own aggregate container (not a Stage 1 contract — other modules
never construct one themselves, they only ever receive `Recipe`/`Building`/`Technology`/`Item`
tuples produced by the lookup functions below).
"""

from __future__ import annotations

import heapq
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from pioneer.contracts import (
    Building,
    ClassDescription,
    Item,
    Recipe,
    Technology,
    TransportTier,
)


@dataclass(frozen=True)
class KnowledgeBase:
    recipes: tuple[Recipe, ...]
    buildings: tuple[Building, ...]
    technologies: tuple[Technology, ...]
    items: tuple[Item, ...] = ()
    descriptions: tuple[ClassDescription, ...] = ()
    transport_tiers: tuple[TransportTier, ...] = ()


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


_KIND_ORDER = {"tutorial": 0, "milestone": 1, "mam": 2, "alternate": 3, "custom": 4}
"""Which way of unlocking to suggest first: the HUB's own path, then MAM research, then hard
drives. "Custom" schematics are granted along the way rather than bought, so they come last."""


def recipe_is_unlocked(recipe: Recipe, unlocked: Collection[str] | None) -> bool | None:
    """Whether any technology unlocking `recipe` is in `unlocked` — `None` when what's unlocked is
    unknown. A recipe the data names no technology for counts as unlocked."""
    if unlocked is None:
        return None
    return not recipe.unlockable_by or any(t in unlocked for t in recipe.unlockable_by)


def easiest_unlock(
    technology_ids: Iterable[str], technologies: Iterable[Technology]
) -> Technology | None:
    """Of the technologies `technology_ids` name — alternative ways to the same unlock — the one to
    suggest: the lowest tier on the most direct path (see `_KIND_ORDER`)."""
    by_id = {t.technology_id: t for t in technologies}
    known = [by_id[t] for t in technology_ids if t in by_id]
    return min(known, key=_unlock_rank, default=None)


def unlock_order(
    wanted: Iterable[str],
    technologies: Iterable[Technology],
    unlocked: Collection[str] = (),
) -> tuple[Technology, ...]:
    """The `wanted` technologies not yet `unlocked`, together with every locked prerequisite they
    need, each after its prerequisites and otherwise by tier, then by how direct the path is.
    Ids the data doesn't know are left out."""
    by_id = {t.technology_id: t for t in technologies}
    needed: set[str] = set()
    stack = [t for t in wanted if t in by_id]
    while stack:
        technology_id = stack.pop()
        if technology_id in needed or technology_id in unlocked:
            continue
        needed.add(technology_id)
        stack.extend(p for p in by_id[technology_id].prerequisites if p in by_id)

    waiting = {t: {p for p in by_id[t].prerequisites if p in needed} for t in needed}
    ready = [(_unlock_rank(by_id[t]), t) for t, blockers in waiting.items() if not blockers]
    heapq.heapify(ready)
    order: list[Technology] = []
    while ready:
        _, technology_id = heapq.heappop(ready)
        order.append(by_id[technology_id])
        for other, blockers in waiting.items():
            if technology_id in blockers:
                blockers.discard(technology_id)
                if not blockers:
                    heapq.heappush(ready, (_unlock_rank(by_id[other]), other))
    return tuple(order)


def _unlock_rank(technology: Technology) -> tuple[int, int, str]:
    return (technology.tier, _KIND_ORDER.get(technology.kind, len(_KIND_ORDER)), technology.name)


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
    return tuple(item for _, item in _ranked_matches(items, query)[:limit])


def resolve_item(items: Iterable[Item], query: str) -> Item | None:
    """The one item `query` unambiguously names, else `None`: an exact id or name; a name that's
    the same with plurals ignored ("Screw" for Screws, "iron plates" for Iron Plate); or the only
    item at `find_items`' best match, if that's a name the query starts or names every word of —
    "reinforced plates" for Reinforced Iron Plate, but not "iron", which starts several."""
    items = tuple(items)
    ranked = _ranked_matches(items, query)
    if not ranked and not query.strip():
        return None
    if ranked and ranked[0][0] <= _EXACT_NAME:
        return ranked[0][1]

    singular = _singular_name(query)
    same_name = [item for item in items if _singular_name(item.name) == singular]
    if len(same_name) == 1:
        return same_name[0]
    if not ranked:
        return None

    best_rank, best = ranked[0]
    at_best = [item for rank, item in ranked if rank == best_rank]
    return best if best_rank <= _ALL_WORDS and len(at_best) == 1 else None


_EXACT_ID, _EXACT_NAME, _NAME_PREFIX, _ALL_WORDS, _ID_FRAGMENT = range(5)


def _ranked_matches(items: Iterable[Item], query: str) -> list[tuple[int, Item]]:
    """(rank, item) for every item matching `query`, best first — see `find_items`."""
    needle = query.strip().casefold()
    if not needle:
        return []
    needle_words = needle.split()

    ranked: list[tuple[int, int, int, Item]] = []
    for index, item in enumerate(items):
        item_id = item.item_id.casefold()
        name = item.name.casefold()
        name_words = name.split()
        if needle == item_id:
            rank = _EXACT_ID
        elif needle == name:
            rank = _EXACT_NAME
        elif name.startswith(needle):
            rank = _NAME_PREFIX
        elif all(any(_word_matches(n, word) for word in name_words) for n in needle_words):
            rank = _ALL_WORDS
        elif needle in item_id:
            rank = _ID_FRAGMENT
        else:
            continue
        ranked.append((rank, len(item.name), index, item))

    ranked.sort(key=lambda entry: entry[:3])
    return [(rank, item) for rank, _, _, item in ranked]


def _singular_name(name: str) -> str:
    return " ".join(_singular(word) for word in name.casefold().split())


def _singular(word: str) -> str:
    """A word's singular, near enough: batteries -> battery, screws -> screw. Glass, and words
    of three letters or fewer, stay as they are."""
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _word_matches(query_word: str, name_word: str) -> bool:
    """`query_word` starts `name_word` ("reinf" -> "reinforced"), or is `name_word` with something
    tacked on — a plural, mostly ("plates" -> "plate")."""
    return name_word.startswith(query_word) or (
        len(name_word) >= 3 and query_word.startswith(name_word)
    )
