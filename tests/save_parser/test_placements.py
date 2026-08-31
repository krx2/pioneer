"""Tests for mapping the raw object table into PlacementRecords, against hand-built
`RawObjectHeader` tuples — no byte parsing involved here, see test_object_table.py for that."""

from pioneer.contracts import Coordinates, PlacementRecord
from pioneer.save_parser.object_table import RawObjectHeader
from pioneer.save_parser.placements import to_placement_records

_SMELTER = RawObjectHeader(
    is_actor=True,
    class_name="/Game/FactoryGame/Buildable/Factory/SmelterMk1/Build_SmelterMk1.Build_SmelterMk1_C",
    level_name="Persistent_Level",
    path_name="Persistent_Level:PersistentLevel.Build_SmelterMk1_C_1",
    object_flags=8,
    position=Coordinates(x=1.0, y=2.0, z=3.0),
)
_PLAYER = RawObjectHeader(
    is_actor=True,
    class_name="/Script/FactoryGame.FGCharacterPlayer",
    level_name="Persistent_Level",
    path_name="Persistent_Level:PersistentLevel.Player_1",
    object_flags=8,
    position=Coordinates(x=0.0, y=0.0, z=0.0),
)
_INVENTORY_COMPONENT = RawObjectHeader(
    is_actor=False,
    class_name="/Script/FactoryGame.FGInventoryComponent",
    level_name="Persistent_Level",
    path_name="Persistent_Level:PersistentLevel.Build_SmelterMk1_C_1.Inventory",
    object_flags=8,
    position=None,
)


def test_building_actor_becomes_a_placement_record() -> None:
    result = to_placement_records((_SMELTER,))
    assert result == (
        PlacementRecord(
            building_id="Build_SmelterMk1_C", position=Coordinates(x=1.0, y=2.0, z=3.0)
        ),
    )


def test_non_buildable_actor_is_excluded() -> None:
    assert to_placement_records((_PLAYER,)) == ()


def test_component_is_excluded() -> None:
    assert to_placement_records((_INVENTORY_COMPONENT,)) == ()


def test_preserves_toc_order_and_filters_mixed_list() -> None:
    result = to_placement_records((_SMELTER, _PLAYER, _INVENTORY_COMPONENT, _SMELTER))
    assert len(result) == 2
    assert all(r.building_id == "Build_SmelterMk1_C" for r in result)


def test_recipe_id_defaults_to_none() -> None:
    (record,) = to_placement_records((_SMELTER,))
    assert record.recipe_id is None
