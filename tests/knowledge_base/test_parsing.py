"""Tests for the UE-struct-string parsers, against hand-written sample strings — no real game
data needed."""

from pioneer.knowledge_base.parsing import (
    class_name_from_path,
    parse_item_amounts,
    parse_quoted_class_list,
)


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


def test_parse_item_amounts_single() -> None:
    raw = (
        "((ItemClass=\"/Script/Engine.BlueprintGeneratedClass'/Game/FactoryGame/Resource/Parts/"
        "IronIngot/Desc_IronIngot.Desc_IronIngot_C'\",Amount=3))"
    )
    assert parse_item_amounts(raw) == (("Desc_IronIngot_C", 3.0),)


def test_parse_item_amounts_multiple() -> None:
    raw = (
        '((ItemClass="/Game/A/Desc_Foo.Desc_Foo_C\'",Amount=2),'
        '(ItemClass="/Game/B/Desc_Bar.Desc_Bar_C\'",Amount=15.5))'
    )
    assert parse_item_amounts(raw) == (
        ("Desc_Foo_C", 2.0),
        ("Desc_Bar_C", 15.5),
    )


def test_parse_item_amounts_empty() -> None:
    assert parse_item_amounts("") == ()


def test_parse_quoted_class_list() -> None:
    raw = (
        '("/Game/FactoryGame/Buildable/Factory/ConstructorMk1/Build_ConstructorMk1.'
        'Build_ConstructorMk1_C","/Script/FactoryGame.FGBuildableAutomatedWorkBench")'
    )
    assert parse_quoted_class_list(raw) == (
        "Build_ConstructorMk1_C",
        "FGBuildableAutomatedWorkBench",
    )


def test_parse_quoted_class_list_empty() -> None:
    assert parse_quoted_class_list("") == ()
