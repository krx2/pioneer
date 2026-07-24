"""Tests for the pure lookup functions, against a small hand-built `KnowledgeBase` — no loader
or file I/O involved."""

from pioneer.contracts import Building, ItemAmount, Recipe, Technology
from pioneer.knowledge_base.queries import (
    KnowledgeBase,
    building_for,
    prerequisites_for_technology,
    recipe_by_id,
    recipes_for_output,
    technology_for_recipe,
)

_TECH = Technology(technology_id="tier_0", name="Tier 0", tier=0)
_TECH_ALT = Technology(
    technology_id="alt_pure_ingot", name="Alt", tier=0, prerequisites=("tier_0",)
)
_SMELTER = Building(
    building_id="smelter", name="Smelter", power_consumption_mw=4, input_slots=1, output_slots=1
)
_RECIPE_INGOT = Recipe(
    recipe_id="iron_ingot",
    name="Iron Ingot",
    building_ids=("smelter",),
    inputs=(ItemAmount(item_id="iron_ore", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="iron_ingot", amount_per_minute=30),),
    unlocked_by="tier_0",
)
_RECIPE_INGOT_ALT = Recipe(
    recipe_id="iron_ingot_pure",
    name="Pure Iron Ingot",
    building_ids=("smelter",),
    inputs=(ItemAmount(item_id="iron_ore", amount_per_minute=35),),
    outputs=(ItemAmount(item_id="iron_ingot", amount_per_minute=65),),
    unlocked_by="alt_pure_ingot",
)

KB = KnowledgeBase(
    recipes=(_RECIPE_INGOT, _RECIPE_INGOT_ALT),
    buildings=(_SMELTER,),
    technologies=(_TECH, _TECH_ALT),
)


def test_recipe_by_id_found_and_missing() -> None:
    assert recipe_by_id(KB, "iron_ingot") is _RECIPE_INGOT
    assert recipe_by_id(KB, "does_not_exist") is None


def test_recipes_for_output_returns_all_alternates() -> None:
    recipes = recipes_for_output(KB, "iron_ingot")
    assert set(recipes) == {_RECIPE_INGOT, _RECIPE_INGOT_ALT}


def test_recipes_for_output_unknown_item() -> None:
    assert recipes_for_output(KB, "nonexistent_item") == ()


def test_building_for_found_and_missing() -> None:
    assert building_for(KB, "smelter") is _SMELTER
    assert building_for(KB, "does_not_exist") is None


def test_technology_for_recipe() -> None:
    assert technology_for_recipe(KB, "iron_ingot") is _TECH
    assert technology_for_recipe(KB, "iron_ingot_pure") is _TECH_ALT


def test_technology_for_recipe_missing_recipe() -> None:
    assert technology_for_recipe(KB, "does_not_exist") is None


def test_prerequisites_for_technology() -> None:
    assert prerequisites_for_technology(KB, "alt_pure_ingot") == ("tier_0",)
    assert prerequisites_for_technology(KB, "tier_0") == ()


def test_prerequisites_for_unknown_technology() -> None:
    assert prerequisites_for_technology(KB, "does_not_exist") == ()
