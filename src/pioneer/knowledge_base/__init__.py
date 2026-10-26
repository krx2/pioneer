"""Knowledge base: items, recipes, buildings and technologies from the game's `Docs.json` export
(`assets/en-US.json` — the full data dump, despite its name). See `loader` for its quirks.
"""

from pioneer.knowledge_base.loader import load_from_dict, load_from_file
from pioneer.knowledge_base.queries import (
    KnowledgeBase,
    TierProgress,
    building_for,
    current_tier,
    easiest_unlock,
    find_items,
    item_by_id,
    milestone_progress,
    prerequisites_for_technology,
    recipe_by_id,
    recipe_is_unlocked,
    research_trees,
    resolve_item,
    technology_by_id,
    technology_for_recipe,
    unlock_order,
)

__all__ = [
    "KnowledgeBase",
    "TierProgress",
    "building_for",
    "current_tier",
    "easiest_unlock",
    "find_items",
    "item_by_id",
    "load_from_dict",
    "load_from_file",
    "milestone_progress",
    "prerequisites_for_technology",
    "recipe_by_id",
    "recipe_is_unlocked",
    "research_trees",
    "resolve_item",
    "technology_by_id",
    "technology_for_recipe",
    "unlock_order",
]
