"""Tests for the pure lookup functions, against a small hand-built `KnowledgeBase` — no loader
or file I/O involved."""

from pioneer.contracts import Building, Item, ItemAmount, Recipe, Technology
from pioneer.knowledge_base.queries import (
    KnowledgeBase,
    building_for,
    easiest_unlock,
    find_items,
    item_by_id,
    prerequisites_for_technology,
    raw_resource_ids,
    recipe_by_id,
    recipe_is_unlocked,
    recipes_for_output,
    resolve_item,
    technology_for_recipe,
    unlock_order,
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


_NAMED = (
    *_ITEMS,
    Item(item_id="screw", name="Screws"),
    Item(item_id="battery", name="Batteries"),
    Item(item_id="glass", name="Glass"),
)


def _resolved(query: str) -> str | None:
    item = resolve_item(_NAMED, query)
    return item.item_id if item is not None else None


def test_resolve_item_takes_an_exact_id_or_name() -> None:
    assert _resolved("IRON_PLATE") == "iron_plate"
    assert _resolved("iron plate") == "iron_plate"


def test_resolve_item_ignores_plurals_either_way() -> None:
    assert _resolved("Screw") == "screw"
    assert _resolved("iron plates") == "iron_plate"
    assert _resolved("battery") == "battery"
    assert _resolved("glass") == "glass"


def test_resolve_item_takes_the_only_best_match() -> None:
    assert _resolved("reinforced plates") == "reinforced_plate"
    assert _resolved("wat") == "water"


def test_resolve_item_refuses_what_several_items_match_equally() -> None:
    assert _resolved("iron") is None  # Iron Ore, Iron Ingot, Iron Plate
    assert _resolved("plate") is None  # Iron Plate, Reinforced Iron Plate


def test_resolve_item_refuses_an_id_fragment_or_nothing() -> None:
    assert _resolved("_ingot") is None
    assert _resolved("uranium") is None
    assert _resolved("  ") is None


_MILESTONE_1 = Technology(technology_id="m1", name="Base Building", tier=1, kind="milestone")
_MILESTONE_2 = Technology(
    technology_id="m2", name="Logistics", tier=2, kind="milestone", prerequisites=("m1",)
)
_MAM_1 = Technology(technology_id="mam1", name="Quartz", tier=1, kind="mam")
_DRIVE = Technology(
    technology_id="drive",
    name="Alternate: Pure Ingot",
    tier=0,
    kind="alternate",
    prerequisites=("m2",),
)
_TECHNOLOGIES = (_DRIVE, _MAM_1, _MILESTONE_2, _MILESTONE_1)


def test_a_recipe_is_unlocked_by_any_of_its_technologies() -> None:
    both = Recipe(
        recipe_id="silica",
        name="Silica",
        building_ids=(),
        inputs=(),
        outputs=(),
        unlockable_by=("mam1", "m2"),
    )
    assert recipe_is_unlocked(both, {"m2"}) is True
    assert recipe_is_unlocked(both, {"m1"}) is False
    assert recipe_is_unlocked(both, None) is None
    assert recipe_is_unlocked(_RECIPE_INGOT, set()) is True  # names no technology


def test_the_easiest_unlock_is_the_lowest_tier_on_the_most_direct_path() -> None:
    assert easiest_unlock(("m2", "mam1"), _TECHNOLOGIES) == _MAM_1
    assert easiest_unlock(("m1", "mam1"), _TECHNOLOGIES) == _MILESTONE_1
    assert easiest_unlock(("unknown",), _TECHNOLOGIES) is None


def test_unlock_order_puts_prerequisites_first() -> None:
    order = unlock_order(["drive", "mam1"], _TECHNOLOGIES)

    assert [t.technology_id for t in order] == ["m1", "mam1", "m2", "drive"]


def test_unlock_order_skips_what_is_unlocked_and_what_is_unknown() -> None:
    order = unlock_order(["drive", "nope"], _TECHNOLOGIES, unlocked={"m1"})

    assert [t.technology_id for t in order] == ["m2", "drive"]
    assert unlock_order(["m1"], _TECHNOLOGIES, unlocked={"m1"}) == ()
