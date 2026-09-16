"""Tests for the property-tag reader and `mCurrentRecipe` extraction, against hand-built byte
buffers (`byte_builders.object_property_tag_bytes`) — no real save file involved. See
test_real_saves_properties.py for the bonus real-file check."""

from tests.save_parser.byte_builders import (
    float_property_tag_bytes,
    fstring,
    level_object_reference_tag_bytes,
    object_property_tag_bytes,
    property_list_terminator_bytes,
)

from pioneer.save_parser.binary_reader import ByteReader
from pioneer.save_parser.properties import (
    find_recipe_ids,
    find_recipe_paths,
    read_float_property,
    read_level_object_reference,
    read_object_reference_value,
    read_property_tag,
)


def test_read_property_tag_parses_header_fields() -> None:
    # [ArrayIndex=5][Size=9] -- confirmed field order, see properties.py's module docstring.
    data = (
        fstring("mCurrentRecipe") + fstring("ObjectProperty") + b"\x05\x00\x00\x00\x09\x00\x00\x00"
    )
    reader = ByteReader(data)

    tag = read_property_tag(reader)

    assert tag is not None
    assert tag.name == "mCurrentRecipe"
    assert tag.type_name == "ObjectProperty"
    assert tag.array_index == 5
    assert tag.size == 9


def test_read_property_tag_returns_none_at_list_terminator() -> None:
    reader = ByteReader(property_list_terminator_bytes())

    assert read_property_tag(reader) is None


def test_read_object_reference_value_resyncs_past_unknown_padding() -> None:
    tag_bytes = object_property_tag_bytes(
        name="mCurrentRecipe",
        referenced_path="/Game/FactoryGame/Recipes/Recipe_IronPlate_C.Recipe_IronPlate_C",
        padding_before_value=b"\x00" * 9,  # stands in for the undocumented tag header bytes
    )
    reader = ByteReader(tag_bytes)
    read_property_tag(reader)

    value = read_object_reference_value(reader)

    assert value == "/Game/FactoryGame/Recipes/Recipe_IronPlate_C.Recipe_IronPlate_C"


def test_read_object_reference_value_returns_none_when_nothing_found_in_window() -> None:
    reader = ByteReader(b"\x00" * 300)  # no /Game/ or /Script/ marker anywhere

    assert read_object_reference_value(reader, search_window=64) is None


def test_find_recipe_paths_locates_every_occurrence_in_order() -> None:
    body = (
        b"\xff\xff\xff"  # unrelated leading bytes
        + object_property_tag_bytes(
            name="mCurrentRecipe",
            referenced_path="/Game/FactoryGame/Recipes/Recipe_IronPlate_C.Recipe_IronPlate_C",
        )
        + b"\x11\x22\x33"  # unrelated bytes between objects
        + object_property_tag_bytes(
            name="mCurrentRecipe",
            referenced_path="/Game/FactoryGame/Recipes/Recipe_Screw_C.Recipe_Screw_C",
        )
    )

    paths = find_recipe_paths(body)

    assert paths == (
        "/Game/FactoryGame/Recipes/Recipe_IronPlate_C.Recipe_IronPlate_C",
        "/Game/FactoryGame/Recipes/Recipe_Screw_C.Recipe_Screw_C",
    )


def test_find_recipe_ids_extracts_the_bare_class_name() -> None:
    body = object_property_tag_bytes(
        name="mCurrentRecipe",
        referenced_path="/Game/FactoryGame/Recipes/Recipe_IronPlate_C.Recipe_IronPlate_C",
    )

    assert find_recipe_ids(body) == ("Recipe_IronPlate_C",)


def test_find_recipe_ids_ignores_other_property_names() -> None:
    body = object_property_tag_bytes(
        name="mSomeOtherProperty",
        referenced_path="/Game/FactoryGame/Recipes/Recipe_IronPlate_C.Recipe_IronPlate_C",
    )

    assert find_recipe_ids(body) == ()


def test_find_recipe_ids_skips_an_occurrence_with_no_resolvable_value() -> None:
    body = fstring("mCurrentRecipe") + fstring("ObjectProperty") + b"\x00" * 300  # no path anywhere

    assert find_recipe_ids(body, property_name="mCurrentRecipe") == ()


def test_read_float_property_reads_the_value() -> None:
    body = b"\xaa\xbb" + float_property_tag_bytes(name="mCurrentPotential", value=2.5)

    assert read_float_property(body, "mCurrentPotential") == 2.5


def test_read_float_property_skips_a_property_guid() -> None:
    body = float_property_tag_bytes(
        name="mCurrentPotential", value=0.75, property_guid=b"\x07" * 16
    )

    assert read_float_property(body, "mCurrentPotential") == 0.75


def test_read_float_property_absent_is_none() -> None:
    body = float_property_tag_bytes(name="mPendingPotential", value=2.0)

    assert read_float_property(body, "mCurrentPotential") is None


def test_read_float_property_stays_inside_the_given_range() -> None:
    first = property_list_terminator_bytes()
    body = first + float_property_tag_bytes(name="mCurrentPotential", value=2.0)

    assert read_float_property(body, "mCurrentPotential", end=len(first)) is None
    assert read_float_property(body, "mCurrentPotential", start=len(first)) == 2.0


def test_read_float_property_ignores_a_same_named_property_of_another_type() -> None:
    body = object_property_tag_bytes(
        name="mCurrentPotential", referenced_path="/Game/Whatever.Whatever_C"
    ) + float_property_tag_bytes(name="mCurrentPotential", value=1.5)

    assert read_float_property(body, "mCurrentPotential") == 1.5


_NODE_PATH = "Persistent_Level:PersistentLevel.BP_ResourceNode103"


def test_read_level_object_reference_returns_the_objects_path() -> None:
    body = b"\x01\x02" + level_object_reference_tag_bytes(
        name="mExtractableResource", path=_NODE_PATH
    )

    assert read_level_object_reference(body, "mExtractableResource") == _NODE_PATH


def test_read_level_object_reference_ignores_a_class_reference() -> None:
    """A class reference's `Size` is 0, so its bytes can't pass for a level object's."""
    body = object_property_tag_bytes(
        name="mExtractableResource", referenced_path="/Game/Whatever.Whatever_C"
    )

    assert read_level_object_reference(body, "mExtractableResource") is None


def test_read_level_object_reference_absent_is_none() -> None:
    body = property_list_terminator_bytes()

    assert read_level_object_reference(body, "mExtractableResource") is None
