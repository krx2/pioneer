"""Knowledge base module (implementation.md Stage 2).

Static, queryable data set of recipes, buildings, and technologies. Depends only on
`pioneer.contracts`; tested against its own `fixtures/`, never against the real game data files.

The real data source is a Satisfactory `Docs.json`-style export, committed at `docs/en-US.json`
(named `en-US.json` by the game's own export tooling — it's the full game data dump, not just
localized strings). See `loader.py`'s module docstring for the parsing details and known
limitations of that source.
"""

from pioneer.knowledge_base.loader import load_from_dict, load_from_file
from pioneer.knowledge_base.queries import (
    KnowledgeBase,
    building_for,
    prerequisites_for_technology,
    recipe_by_id,
    recipes_for_output,
    technology_by_id,
    technology_for_recipe,
)

__all__ = [
    "KnowledgeBase",
    "building_for",
    "load_from_dict",
    "load_from_file",
    "prerequisites_for_technology",
    "recipe_by_id",
    "recipes_for_output",
    "technology_by_id",
    "technology_for_recipe",
]
