"""Bonus confidence check: the loader against the real game export at `docs/en-US.json`.

Not the module's "done" bar — test_loader.py's fixture-based tests are (per
docs/implementation.md Stage 2). This is extra: proof the parsing/joining logic holds up against
the real, messy source data, not just the small hand-built stand-in. `docs/en-US.json` is
committed to the repo, so this runs by default; it skips cleanly if the file is ever missing
locally rather than failing the whole suite.
"""

from pathlib import Path

import pytest

from pioneer.knowledge_base.loader import load_from_file
from pioneer.knowledge_base.queries import (
    building_for,
    find_items,
    item_by_id,
    raw_resource_ids,
    recipes_for_output,
    technology_for_recipe,
)

_DOCS_PATH = Path(__file__).parent.parent.parent / "docs" / "en-US.json"

pytestmark = pytest.mark.skipif(not _DOCS_PATH.exists(), reason="docs/en-US.json not present")


@pytest.fixture(scope="module")
def kb():
    return load_from_file(_DOCS_PATH)


def test_loads_every_factory_recipe(kb) -> None:
    """The export holds 872 recipes, but only 291 run in a factory building — the rest are build
    gun, Equipment Workshop and Craft Bench recipes (see loader.py's module docstring)."""
    assert len(kb.recipes) == 291


def test_every_recipe_runs_in_a_known_building(kb) -> None:
    building_ids = {b.building_id for b in kb.buildings}
    assert [r.recipe_id for r in kb.recipes if not set(r.building_ids) <= building_ids] == []


def test_loads_all_relevant_buildings(kb) -> None:
    assert len(kb.buildings) == 25


def test_filters_out_irrelevant_schematics(kb) -> None:
    # 574 raw schematics minus resource-sink/customization/tutorial noise, per loader.py.
    assert len(kb.technologies) == 365


def test_iron_rod_recipe_matches_known_game_values(kb) -> None:
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_IronRod_C")
    assert recipe.inputs[0].item_id == "Desc_IronIngot_C"
    assert recipe.inputs[0].amount_per_minute == pytest.approx(15.0)
    assert recipe.outputs[0].item_id == "Desc_IronRod_C"
    assert recipe.outputs[0].amount_per_minute == pytest.approx(15.0)
    assert recipe.building_ids == ("Build_ConstructorMk1_C",)


def test_fuel_recipe_matches_known_game_values_in_cubic_metres(kb) -> None:
    """60 m³/min crude oil -> 40 m³/min fuel + 30 polymer resin, as the in-game recipe reads. The
    export stores the fluids as 60000/40000 litres; the solid resin is already unscaled."""
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_LiquidFuel_C")
    oil = next(i for i in recipe.inputs if i.item_id == "Desc_LiquidOil_C")
    fuel = next(o for o in recipe.outputs if o.item_id == "Desc_LiquidFuel_C")
    resin = next(o for o in recipe.outputs if o.item_id == "Desc_PolymerResin_C")
    assert oil.amount_per_minute == pytest.approx(60.0)
    assert fuel.amount_per_minute == pytest.approx(40.0)
    assert resin.amount_per_minute == pytest.approx(30.0)


_FLUID_ITEM_IDS = frozenset(
    {
        "Desc_AluminaSolution_C",
        "Desc_DarkEnergy_C",
        "Desc_DissolvedSilica_C",
        "Desc_HeavyOilResidue_C",
        "Desc_IonizedFuel_C",
        "Desc_LiquidBiofuel_C",
        "Desc_LiquidFuel_C",
        "Desc_LiquidOil_C",
        "Desc_LiquidTurboFuel_C",
        "Desc_NitricAcid_C",
        "Desc_NitrogenGas_C",
        "Desc_QuantumEnergy_C",
        "Desc_RocketFuel_C",
        "Desc_SulfuricAcid_C",
        "Desc_Water_C",
    }
)
"""Every `RF_LIQUID`/`RF_GAS` item in the export, listed out rather than re-derived from `mForm`,
so this check stays independent of the loader logic it's checking."""


def test_no_fluid_rate_is_left_unscaled(kb) -> None:
    """The busiest fluid recipe in the game moves 240 m³/min. Any amount the loader forgot to
    divide would land three orders of magnitude out, so a generous ceiling still catches it."""
    fluid_rates = [
        (r.recipe_id, item.item_id, item.amount_per_minute)
        for r in kb.recipes
        for item in r.inputs + r.outputs
        if item.item_id in _FLUID_ITEM_IDS
    ]

    assert len(fluid_rates) > 100  # guard: the check is meaningless if it matched nothing
    assert [entry for entry in fluid_rates if entry[2] >= 1000.0] == []


def test_fluid_items_are_flagged(kb) -> None:
    assert {item.item_id for item in kb.items if item.is_fluid} == _FLUID_ITEM_IDS


def test_raw_resources_are_the_extractable_ones(kb) -> None:
    assert raw_resource_ids(kb) == {
        "Desc_Coal_C",
        "Desc_LiquidOil_C",
        "Desc_NitrogenGas_C",
        "Desc_OreBauxite_C",
        "Desc_OreCopper_C",
        "Desc_OreGold_C",
        "Desc_OreIron_C",
        "Desc_OreUranium_C",
        "Desc_RawQuartz_C",
        "Desc_SAM_C",
        "Desc_Stone_C",
        "Desc_Sulfur_C",
        "Desc_Water_C",
    }


def test_alternate_recipes_are_flagged(kb) -> None:
    """Checked against class names rather than the display names the loader uses, so the two stay
    independent. The class-name convention has exactly two exceptions in this export, confirmed
    against the in-game recipe list: `Recipe_Alternate_Turbofuel_C` is the plain, MAM-unlocked
    "Turbofuel", and `Recipe_PureAluminumIngot_C` is "Alternate: Pure Aluminum Ingot"."""
    named = {r.recipe_id for r in kb.recipes if r.recipe_id.startswith("Recipe_Alternate_")}
    expected = (named - {"Recipe_Alternate_Turbofuel_C"}) | {"Recipe_PureAluminumIngot_C"}
    flagged = {r.recipe_id for r in kb.recipes if r.is_alternate}
    assert len(expected) == 110
    assert flagged == expected


def test_items_carry_display_names(kb) -> None:
    item = item_by_id(kb, "Desc_IronPlateReinforced_C")
    assert item is not None
    assert item.name == "Reinforced Iron Plate"


def test_fuel_energy_is_per_unit_or_per_cubic_metre(kb) -> None:
    """Coal: 300 MJ per unit. Fuel: 0.75 MJ per litre in the export, so 750 per m³ — which makes a
    250 MW Fuel Generator burn 20 m³/min, as in game."""
    coal = item_by_id(kb, "Desc_Coal_C")
    fuel = item_by_id(kb, "Desc_LiquidFuel_C")
    assert coal is not None and fuel is not None
    assert coal.energy_value_mj == pytest.approx(300.0)
    assert fuel.energy_value_mj == pytest.approx(750.0)


def test_find_items_resolves_a_partial_name(kb) -> None:
    assert find_items(kb.items, "heavy modular")[0].item_id == "Desc_ModularFrameHeavy_C"


def test_manufacturing_building_has_power_and_slots_from_recipes(kb) -> None:
    constructor = building_for(kb, "Build_ConstructorMk1_C")
    assert constructor is not None
    assert constructor.power_consumption_mw == pytest.approx(4.0)
    assert constructor.input_slots >= 1
    assert constructor.output_slots >= 1


def test_generator_power_is_negative_net(kb) -> None:
    generator = building_for(kb, "Build_GeneratorBiomass_Automated_C")
    assert generator is not None
    assert generator.power_consumption_mw == pytest.approx(-30.0)


def test_variable_power_buildings_get_their_average_draw(kb) -> None:
    converter = building_for(kb, "Build_Converter_C")
    accelerator = building_for(kb, "Build_HadronCollider_C")
    assert converter is not None and accelerator is not None
    assert converter.power_consumption_mw == pytest.approx(250.0)
    assert accelerator.power_consumption_mw == pytest.approx(875.0)


def test_geothermal_generator_produces_its_normal_geyser_average(kb) -> None:
    geothermal = building_for(kb, "Build_GeneratorGeoThermal_C")
    assert geothermal is not None
    assert geothermal.power_consumption_mw == pytest.approx(-200.0)


def test_extractors_and_pressurizer_are_loaded(kb) -> None:
    for building_id in ("Build_WaterPump_C", "Build_FrackingExtractor_C", "Build_OilPump_C"):
        assert building_for(kb, building_id) is not None
    pressurizer = building_for(kb, "Build_FrackingSmasher_C")
    assert pressurizer is not None
    assert pressurizer.power_consumption_mw == pytest.approx(150.0)


def test_alternate_recipes_exist_for_iron_ingot(kb) -> None:
    recipes = recipes_for_output(kb, "Desc_IronIngot_C")
    assert len(recipes) > 1


def test_iron_rod_is_unlocked_by_a_technology(kb) -> None:
    technology = technology_for_recipe(kb, "Recipe_IronRod_C")
    assert technology is not None
    assert technology.tier == 0


def test_some_technologies_have_real_prerequisites(kb) -> None:
    with_prerequisites = [t for t in kb.technologies if t.prerequisites]
    assert len(with_prerequisites) == 163
