"""Tests for the pure lookup functions, against a small hand-built `KnowledgeBase` — no loader
or file I/O involved."""

from pioneer.contracts import Building, Item, ItemAmount, Recipe, Technology
from pioneer.knowledge_base.queries import (
    KnowledgeBase,
    building_for,
    find_items,
    item_by_id,
    prerequisites_for_technology,
    raw_resource_ids,
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
    is_alternate=True,
)
_ITEMS = (
    Item(item_id="iron_ore", name="Iron Ore", is_raw_resource=True),
    Item(item_id="iron_ingot", name="Iron Ingot"),
    Item(item_id="reinforced_plate", name="Reinforced Iron Plate"),
    Item(item_id="iron_plate", name="Iron Plate"),
    Item(item_id="water", name="Water", is_fluid=True, is_raw_resource=True),
)

KB = KnowledgeBase(
    recipes=(_RECIPE_INGOT, _RECIPE_INGOT_ALT),
    buildings=(_SMELTER,),
    technologies=(_TECH, _TECH_ALT),
    items=_ITEMS,
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


def test_item_by_id_found_and_missing() -> None:
    assert item_by_id(KB, "iron_plate") == Item(item_id="iron_plate", name="Iron Plate")
    assert item_by_id(KB, "does_not_exist") is None


def test_raw_resource_ids() -> None:
    assert raw_resource_ids(KB) == frozenset({"iron_ore", "water"})


def test_find_items_exact_name_beats_a_longer_match() -> None:
    assert [i.item_id for i in find_items(_ITEMS, "iron plate")] == [
        "iron_plate",
        "reinforced_plate",
    ]


def test_find_items_matches_every_word_in_any_order() -> None:
    assert find_items(_ITEMS, "reinforced plate")[0].item_id == "reinforced_plate"
    assert find_items(_ITEMS, "plate reinf")[0].item_id == "reinforced_plate"


def test_find_items_forgives_plurals() -> None:
    assert find_items(_ITEMS, "reinforced plates")[0].item_id == "reinforced_plate"
    assert find_items(_ITEMS, "iron ingots")[0].item_id == "iron_ingot"


def test_find_items_by_id_is_case_insensitive() -> None:
    assert find_items(_ITEMS, "IRON_INGOT")[0].item_id == "iron_ingot"


def test_find_items_no_match_and_blank_query() -> None:
    assert find_items(_ITEMS, "uranium") == ()
    assert find_items(_ITEMS, "   ") == ()


def test_find_items_respects_limit() -> None:
    assert len(find_items(_ITEMS, "iron", limit=2)) == 2
