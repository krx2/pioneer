"""Tests for the object table resync + parser, against hand-built byte buffers
(`byte_builders.object_table_bytes`) — no real save file involved. See
test_real_saves_object_table.py for the bonus real-file check."""

import pytest
from tests.save_parser.byte_builders import (
    actor_header_bytes,
    component_header_bytes,
    object_table_bytes,
)

from pioneer.contracts import Coordinates
from pioneer.save_parser.object_table import find_and_read_object_table


def test_finds_table_preceded_by_arbitrary_junk() -> None:
    junk = b"\xff" * 5000  # stands in for the unparsed FSaveObjectVersionData / WPV preamble
    table = object_table_bytes([actor_header_bytes(class_name="/Game/Foo/Bar.Bar_C")])
    data = junk + table

    result = find_and_read_object_table(data, min_object_count=1, min_table_length=1)

    assert len(result) == 1
    assert result[0].class_name == "/Game/Foo/Bar.Bar_C"


def test_parses_actor_header_fields() -> None:
    table = object_table_bytes(
        [
            actor_header_bytes(
                class_name="/Game/FactoryGame/Buildable/Factory/SmelterMk1/Build_SmelterMk1.Build_SmelterMk1_C",
                level_name="Persistent_Level",
                path_name="Persistent_Level:PersistentLevel.Build_SmelterMk1_C_1",
                object_flags=8,
                position=(100.0, -200.0, 50.5),
            )
        ]
    )

    (obj,) = find_and_read_object_table(table, min_object_count=1, min_table_length=1)

    assert obj.is_actor is True
    assert obj.class_name.endswith("Build_SmelterMk1_C")
    assert obj.level_name == "Persistent_Level"
    assert obj.path_name == "Persistent_Level:PersistentLevel.Build_SmelterMk1_C_1"
    assert obj.object_flags == 8
    assert obj.position == Coordinates(x=100.0, y=-200.0, z=50.5)


def test_parses_component_header_without_position() -> None:
    table = object_table_bytes(
        [component_header_bytes(class_name="/Script/FactoryGame.FGInventoryComponent")]
    )

    (obj,) = find_and_read_object_table(table, min_object_count=1, min_table_length=1)

    assert obj.is_actor is False
    assert obj.position is None


def test_parses_mixed_actors_and_components_in_order() -> None:
    headers = [
        actor_header_bytes(class_name="/Game/A.A_C", position=(1.0, 2.0, 3.0)),
        component_header_bytes(class_name="/Script/FactoryGame.SomeComponent"),
        actor_header_bytes(class_name="/Game/B.B_C", position=(4.0, 5.0, 6.0)),
    ]
    table = object_table_bytes(headers)

    result = find_and_read_object_table(table, min_object_count=1, min_table_length=1)

    assert [o.class_name for o in result] == [
        "/Game/A.A_C",
        "/Script/FactoryGame.SomeComponent",
        "/Game/B.B_C",
    ]
    assert [o.is_actor for o in result] == [True, False, True]


def test_search_from_skips_an_earlier_false_lead() -> None:
    # A /Game/ occurrence that is NOT preceded by a self-consistent (length, numObjects, type)
    # triple must not be mistaken for the table -- only the real one should match.
    decoy = b"/Game/decoy/NotATable_C\x00"
    table = object_table_bytes([actor_header_bytes(class_name="/Game/Real/Table_C")])
    data = decoy + b"\x00" * 40 + table

    result = find_and_read_object_table(data, min_object_count=1, min_table_length=1)

    assert result[0].class_name == "/Game/Real/Table_C"


def test_no_plausible_table_raises() -> None:
    with pytest.raises(ValueError, match="could not locate"):
        find_and_read_object_table(b"\x00" * 1000)
