"""Production Planner module (implementation.md Stage 7).

Given a target output rate, builds a `ProductionGraph` from raw resources via graph search over
recipe data. Depends only on `pioneer.contracts`; tested against its own fixture recipe set.
"""

from pioneer.production_planner.planner import (
    MissingRecipe,
    find_missing_recipes,
    plan_production,
    plan_targets,
    recipes_for_output,
)

__all__ = [
    "MissingRecipe",
    "find_missing_recipes",
    "plan_production",
    "plan_targets",
    "recipes_for_output",
]
