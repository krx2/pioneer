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
from pioneer.knowledge_base.queries import building_for, recipes_for_output, technology_for_recipe

_DOCS_PATH = Path(__file__).parent.parent.parent / "docs" / "en-US.json"

pytestmark = pytest.mark.skipif(not _DOCS_PATH.exists(), reason="docs/en-US.json not present")


@pytest.fixture(scope="module")
def kb():
    return load_from_file(_DOCS_PATH)


def test_loads_all_recipes(kb) -> None:
    assert len(kb.recipes) == 872


def test_loads_all_relevant_buildings(kb) -> None:
    assert len(kb.buildings) == 20


def test_filters_out_irrelevant_schematics(kb) -> None:
    # 574 raw schematics minus resource-sink/customization/tutorial noise, per loader.py.
    assert len(kb.technologies) == 365


def test_iron_rod_recipe_matches_known_game_values(kb) -> None:
    recipe = next(r for r in kb.recipes if r.recipe_id == "Recipe_IronRod_C")
    assert recipe.inputs[0].item_id == "Desc_IronIngot_C"
    assert recipe.inputs[0].amount_per_minute == pytest.approx(15.0)
    assert recipe.outputs[0].item_id == "Desc_IronRod_C"
    assert recipe.outputs[0].amount_per_minute == pytest.approx(15.0)
    assert "Build_ConstructorMk1_C" in recipe.building_ids


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
