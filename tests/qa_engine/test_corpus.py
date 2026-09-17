"""Tests for building the Q&A corpus from Knowledge Base contracts — hand-built data, no loader."""

from dataclasses import replace

from pioneer.contracts import Building, ClassDescription, Item, ItemAmount, Recipe, Technology
from pioneer.qa_engine.corpus import build_corpus
from pioneer.qa_engine.retrieval import Passage, retrieve

_ITEMS = (
    Item(item_id="Desc_IronIngot_C", name="Iron Ingot"),
    Item(item_id="Desc_IronPlate_C", name="Iron Plate"),
)
_BUILDINGS = (
    Building(
        building_id="Build_ConstructorMk1_C",
        name="Constructor",
        power_consumption_mw=4,
        input_slots=1,
        output_slots=1,
    ),
)
_TECHNOLOGIES = (Technology(technology_id="Schematic_Onboarding_C", name="Onboarding", tier=0),)
_PLATE = Recipe(
    recipe_id="Recipe_IronPlate_C",
    name="Iron Plate",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronPlate_C", amount_per_minute=20),),
    unlocked_by="Schematic_Onboarding_C",
)
_DESCRIPTIONS = (
    ClassDescription(
        class_id="Desc_IronPlate_C",
        name="Iron Plate",
        text="Used for crafting. One of the most basic parts.",
        category="FGItemDescriptor",
    ),
)


def _corpus(descriptions=(), recipes=()):
    return build_corpus(descriptions, recipes, _ITEMS, _BUILDINGS, _TECHNOLOGIES)


def test_every_description_becomes_a_passage_citing_its_name() -> None:
    assert _corpus(descriptions=_DESCRIPTIONS) == (
        Passage(
            passage_id="Desc_IronPlate_C",
            text="Iron Plate. Used for crafting. One of the most basic parts.",
            source="Iron Plate",
        ),
    )


def test_a_recipe_passage_spells_out_building_rates_and_unlock_by_name() -> None:
    (passage,) = _corpus(recipes=(_PLATE,))

    assert passage.passage_id == "recipe:Recipe_IronPlate_C"
    assert passage.text == (
        "Iron Plate is a recipe made in Constructor. Per machine per minute it uses "
        "30 Iron Ingot and makes 20 Iron Plate. It is unlocked by Onboarding."
    )
    assert passage.source == "Recipe: Iron Plate"


def test_alternates_and_unnamed_ids_are_spelled_out_as_they_are() -> None:
    alternate = replace(
        _PLATE,
        recipe_id="Recipe_Alternate_Mystery_C",
        name="Alternate: Mystery",
        inputs=(ItemAmount(item_id="Desc_Mystery_C", amount_per_minute=7.5),),
        unlocked_by=None,
        is_alternate=True,
    )

    (passage,) = _corpus(recipes=(alternate,))

    assert "is an alternate recipe" in passage.text
    assert "7.5 Desc_Mystery_C" in passage.text
    assert "unlocked" not in passage.text


def test_the_corpus_answers_a_how_is_it_made_question_with_the_recipe() -> None:
    corpus = _corpus(descriptions=_DESCRIPTIONS, recipes=(_PLATE,))

    top = retrieve("how is iron plate made in a constructor", corpus)[0]

    assert top.passage.passage_id == "recipe:Recipe_IronPlate_C"


def test_identically_described_variants_become_one_passage() -> None:
    twin = replace(_DESCRIPTIONS[0], class_id="Desc_IronPlate_Twin_C")

    corpus = _corpus(descriptions=_DESCRIPTIONS + (twin,))

    assert [passage.passage_id for passage in corpus] == ["Desc_IronPlate_C"]
