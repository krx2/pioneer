"""Tests for the property-tag reader and `mCurrentRecipe` extraction, against hand-built byte
buffers (`byte_builders.object_property_tag_bytes`) — no real save file involved. See
test_real_saves_properties.py for the bonus real-file check."""

from tests.save_parser.byte_builders import (
    bool_property_tag_bytes,
    float_property_tag_bytes,
    fstring,
    level_object_reference_tag_bytes,
    object_property_tag_bytes,
    object_reference_array_tag_bytes,
    property_list_terminator_bytes,
)

from pioneer.save_parser.binary_reader import ByteReader
from pioneer.save_parser.properties import (
    find_recipe_ids,
    find_recipe_paths,
    read_bool_property,
    read_float_property,
    read_level_object_reference,
    read_object_reference_array,
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


def test_read_bool_property_reads_the_bool_true_flag() -> None:
    body = b"\xaa" + bool_property_tag_bytes(name="mIsProductionPaused")

    assert read_bool_property(body, "mIsProductionPaused") is True


def test_read_bool_property_reads_the_older_value_byte_and_an_explicit_false() -> None:
    old_style = bool_property_tag_bytes(name="mIsProductionPaused", value_bytes=b"\x01\x00")
    false = bool_property_tag_bytes(name="mIsProductionPaused", value_bytes=b"\x00\x00")

    assert read_bool_property(old_style, "mIsProductionPaused") is True
    assert read_bool_property(false, "mIsProductionPaused") is False


def test_read_bool_property_absent_or_of_another_type_is_none() -> None:
    body = float_property_tag_bytes(name="mIsProductionPaused", value=1.0)

    assert read_bool_property(body, "mIsProductionPaused") is None
    assert read_bool_property(b"", "mIsProductionPaused") is None


_SCHEMATICS = [
    "/Game/FactoryGame/Schematics/Schematic_StartingRecipes.Schematic_StartingRecipes_C",
    "/Game/FactoryGame/Schematics/Progression/Schematic_3-4.Schematic_3-4_C",
]


def test_read_object_reference_array_returns_every_path() -> None:
    body = b"\x01\x02" + object_reference_array_tag_bytes(
        name="mPurchasedSchematics", paths=_SCHEMATICS
    )

    assert read_object_reference_array(body, "mPurchasedSchematics") == tuple(_SCHEMATICS)


def test_read_object_reference_array_of_nothing_is_empty() -> None:
    body = object_reference_array_tag_bytes(name="mPurchasedSchematics", paths=[])

    assert read_object_reference_array(body, "mPurchasedSchematics") == ()


def test_read_object_reference_array_refuses_a_size_the_elements_do_not_fill() -> None:
    body = bytearray(
        object_reference_array_tag_bytes(name="mPurchasedSchematics", paths=_SCHEMATICS)
    )
    size_offset = body.index(b"ObjectProperty") + len("ObjectProperty") + 1 + 4
    body[size_offset] += 1

    assert read_object_reference_array(bytes(body), "mPurchasedSchematics") is None
    assert read_object_reference_array(b"", "mPurchasedSchematics") is None
