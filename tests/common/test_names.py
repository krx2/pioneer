"""Tests for the class-id and object-path helpers, against hand-written ids and paths."""

import pytest

from pioneer.common import class_name_from_path, readable_id


@pytest.mark.parametrize(
    ("class_id", "expected"),
    [
        ("Desc_IronPlate_C", "Iron Plate"),
        ("Recipe_Alternate_CoatedIronPlate_C", "Alternate Coated Iron Plate"),
        ("Build_SmelterMk1_C", "Smelter Mk1"),
        ("Desc_SpaceElevatorPart_1_C", "Space Elevator Part 1"),
        ("Desc_GunpowderMK2_C", "Gunpowder MK2"),
        ("just a name", "just a name"),
    ],
)
def test_readable_id_turns_a_class_id_into_words(class_id: str, expected: str) -> None:
    assert readable_id(class_id) == expected


def test_class_name_from_path_with_quote_wrapper() -> None:
    path = (
        "/Script/Engine.BlueprintGeneratedClass'/Game/FactoryGame/Resource/Parts/IronIngot/"
        "Desc_IronIngot.Desc_IronIngot_C'"
    )
    assert class_name_from_path(path) == "Desc_IronIngot_C"


def test_class_name_from_path_without_quote_wrapper() -> None:
    assert class_name_from_path("/Script/FactoryGame.FGBuildableAutomatedWorkBench") == (
        "FGBuildableAutomatedWorkBench"
    )
