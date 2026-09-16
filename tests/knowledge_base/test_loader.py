"""Tests for the Docs.json loader, against `fixtures/mini_docs.json` — never the real game
export. See that fixture for the small iron-chain + one alternate + one filtered-out schematic
it encodes. See test_real_docs.py for the equivalent checks against the real game export."""

import json
from pathlib import Path

import pytest

from pioneer.contracts import Item
from pioneer.knowledge_base.loader import load_from_dict

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mini_docs.json"


@pytest.fixture
def kb():
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        raw_docs = json.load(f)
    return load_from_dict(raw_docs)


def test_recipe_rates_are_computed_from_duration(kb) -> None:
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_IronRod_C")
    assert recipe.inputs[0].item_id == "Desc_IronIngot_C"
    assert recipe.inputs[0].amount_per_minute == pytest.approx(15.0)
    assert recipe.outputs[0].item_id == "Desc_IronRod_C"
    assert recipe.outputs[0].amount_per_minute == pytest.approx(15.0)


def test_recipe_with_uneven_ratio(kb) -> None:
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_IronPlate_C")
    assert recipe.inputs[0].amount_per_minute == pytest.approx(30.0)
    assert recipe.outputs[0].amount_per_minute == pytest.approx(20.0)


def test_liquid_ingredient_is_scaled_down_to_cubic_metres(kb) -> None:
    """The export stores fluids in litres — 2000 per 6s craft is 20 m³/min, not 20000."""
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_ResidualPlastic_C")
    water = next(i for i in recipe.inputs if i.item_id == "Desc_Water_C")
    assert water.amount_per_minute == pytest.approx(20.0)


def test_solid_alongside_a_liquid_in_the_same_recipe_is_untouched(kb) -> None:
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_ResidualPlastic_C")
    resin = next(i for i in recipe.inputs if i.item_id == "Desc_PolymerResin_C")
    plastic = next(o for o in recipe.outputs if o.item_id == "Desc_Plastic_C")
    assert resin.amount_per_minute == pytest.approx(60.0)
    assert plastic.amount_per_minute == pytest.approx(20.0)


def test_liquid_product_is_scaled_down_too(kb) -> None:
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_LiquidFuel_C")
    oil = next(i for i in recipe.inputs if i.item_id == "Desc_LiquidOil_C")
    fuel = next(o for o in recipe.outputs if o.item_id == "Desc_LiquidFuel_C")
    resin = next(o for o in recipe.outputs if o.item_id == "Desc_PolymerResin_C")
    assert oil.amount_per_minute == pytest.approx(60.0)
    assert fuel.amount_per_minute == pytest.approx(40.0)
    assert resin.amount_per_minute == pytest.approx(30.0)


def test_gases_are_scaled_like_liquids(kb) -> None:
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_NitricAcid_C")
    nitrogen = next(i for i in recipe.inputs if i.item_id == "Desc_NitrogenGas_C")
    assert nitrogen.amount_per_minute == pytest.approx(120.0)


def test_item_with_no_descriptor_in_the_export_is_treated_as_solid(kb) -> None:
    """The iron chain's items carry no `mForm` entry in this fixture — an unknown item must be
    left alone, not scaled on the off-chance it's a fluid."""
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_IngotIron_C")
    assert recipe.inputs[0].item_id == "Desc_OreIron_C"
    assert recipe.inputs[0].amount_per_minute == pytest.approx(30.0)


def test_slots_come_from_the_widest_recipe_in_a_building(kb) -> None:
    refinery = next(b for b in kb.buildings if b.building_id == "Build_OilRefinery_C")
    # Residual Plastic has two inputs, Fuel has two outputs.
    assert (refinery.input_slots, refinery.output_slots) == (2, 2)


def test_recipe_building_ids(kb) -> None:
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_IronRod_C")
    assert recipe.building_ids == ("Build_ConstructorMk1_C",)


def test_manual_crafting_stations_are_dropped_from_building_ids(kb) -> None:
    """The fixture's Iron Plate also lists the Craft Bench, the way the real export does."""
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_IronPlate_C")
    assert recipe.building_ids == ("Build_ConstructorMk1_C",)


def test_build_gun_only_recipe_is_dropped(kb) -> None:
    assert "Recipe_ConstructorMk1_C" not in {r.recipe_id for r in kb.recipes}


def test_manufacturing_building_slots_derived_from_recipes(kb) -> None:
    constructor = next(b for b in kb.buildings if b.building_id == "Build_ConstructorMk1_C")
    assert (constructor.input_slots, constructor.output_slots) == (1, 1)
    assert constructor.power_consumption_mw == pytest.approx(4.0)


def test_extractor_falls_back_to_default_slots(kb) -> None:
    miner = next(b for b in kb.buildings if b.building_id == "Build_MinerMk1_C")
    assert (miner.input_slots, miner.output_slots) == (0, 1)


def test_water_extractor_is_loaded_as_an_extractor(kb) -> None:
    extractor = next(b for b in kb.buildings if b.building_id == "Build_WaterPump_C")
    assert extractor.power_consumption_mw == pytest.approx(20.0)
    assert (extractor.input_slots, extractor.output_slots) == (0, 1)


def test_generator_power_is_negative_net(kb) -> None:
    generator = next(b for b in kb.buildings if b.building_id == "Build_GeneratorBiomass_C")
    assert generator.power_consumption_mw == pytest.approx(-30.0)
    assert (generator.input_slots, generator.output_slots) == (1, 0)


def test_variable_power_manufacturer_draws_its_midpoint(kb) -> None:
    converter = next(b for b in kb.buildings if b.building_id == "Build_Converter_C")
    assert converter.power_consumption_mw == pytest.approx(250.0)


def test_geothermal_generator_uses_its_variable_production(kb) -> None:
    geothermal = next(b for b in kb.buildings if b.building_id == "Build_GeneratorGeoThermal_C")
    assert geothermal.power_consumption_mw == pytest.approx(-200.0)


def test_resource_sink_schematic_is_filtered_out(kb) -> None:
    ids = {t.technology_id for t in kb.technologies}
    assert "Schematic_ResourceSinkBonus_C" not in ids


def test_recipe_unlocked_by_is_cross_referenced(kb) -> None:
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_IronRod_C")
    assert recipe.unlocked_by == "Schematic_StartingRecipes_C"


def test_recipe_the_game_presents_as_an_alternate_is_flagged(kb) -> None:
    alternate = next(r for r in kb.recipes if r.recipe_id == "Recipe_Alternate_PureIronIngot_C")
    standard = next(r for r in kb.recipes if r.recipe_id == "Recipe_IngotIron_C")
    assert alternate.is_alternate is True
    assert alternate.unlocked_by == "Schematic_Alternate_PureIronIngot_C"
    assert standard.is_alternate is False


def test_alternate_technology_prerequisites_are_parsed(kb) -> None:
    alt = next(
        t for t in kb.technologies if t.technology_id == "Schematic_Alternate_PureIronIngot_C"
    )
    assert alt.prerequisites == ("Schematic_StartingRecipes_C",)


def test_milestone_style_technology_has_no_prerequisites(kb) -> None:
    starting = next(t for t in kb.technologies if t.technology_id == "Schematic_StartingRecipes_C")
    assert starting.prerequisites == ()


def test_items_are_loaded_with_names_forms_and_energy(kb) -> None:
    """Fuel's `mEnergyValue` is 0.75 MJ per litre in the export: 750 MJ per m³."""
    items = {item.item_id: item for item in kb.items}
    assert items["Desc_LiquidFuel_C"] == Item(
        item_id="Desc_LiquidFuel_C", name="Fuel", is_fluid=True, energy_value_mj=750.0
    )
    assert items["Desc_PolymerResin_C"].is_fluid is False
    assert items["Desc_PolymerResin_C"].energy_value_mj == 0.0


def test_resource_descriptors_are_the_raw_resources(kb) -> None:
    raw = {item.item_id for item in kb.items if item.is_raw_resource}
    assert raw == {"Desc_Water_C", "Desc_LiquidOil_C", "Desc_NitrogenGas_C"}


def test_building_descriptors_are_not_items(kb) -> None:
    assert "Desc_ConstructorMk1_C" not in {item.item_id for item in kb.items}
