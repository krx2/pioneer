"""Tests for mapping the raw object table into PlacementRecords, against hand-built
`RawObjectHeader` tuples — no byte parsing involved here, see test_object_table.py for that."""

import pytest
from tests.save_parser.byte_builders import (
    bool_property_tag_bytes,
    float_property_tag_bytes,
    level_object_reference_tag_bytes,
    object_property_tag_bytes,
    property_list_terminator_bytes,
)

from pioneer.contracts import Coordinates, PlacementRecord
from pioneer.save_parser.entities import EntitySpan
from pioneer.save_parser.object_table import RawObjectHeader
from pioneer.save_parser.placements import to_placement_records, to_placement_records_with_recipes

_SMELTER = RawObjectHeader(
    is_actor=True,
    class_name="/Game/FactoryGame/Buildable/Factory/SmelterMk1/Build_SmelterMk1.Build_SmelterMk1_C",
    level_name="Persistent_Level",
    path_name="Persistent_Level:PersistentLevel.Build_SmelterMk1_C_1",
    object_flags=8,
    position=Coordinates(x=1.0, y=2.0, z=3.0),
)
_GENERATOR = RawObjectHeader(
    is_actor=True,
    class_name=(
        "/Game/FactoryGame/Buildable/Factory/GeneratorCoal/"
        "Build_GeneratorCoal.Build_GeneratorCoal_C"
    ),
    level_name="Persistent_Level",
    path_name="Persistent_Level:PersistentLevel.Build_GeneratorCoal_C_1",
    object_flags=8,
    position=Coordinates(x=4.0, y=5.0, z=6.0),
)
_MINER = RawObjectHeader(
    is_actor=True,
    class_name="/Game/FactoryGame/Buildable/Factory/MinerMK1/Build_MinerMk1.Build_MinerMk1_C",
    level_name="Persistent_Level",
    path_name="Persistent_Level:PersistentLevel.Build_MinerMk1_C_1",
    object_flags=8,
    position=Coordinates(x=7.0, y=8.0, z=9.0),
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
            building_id="Build_SmelterMk1_C",
            position=Coordinates(x=1.0, y=2.0, z=3.0),
            object_id=_SMELTER.path_name,
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


def _body_with_spans(*payloads: bytes) -> tuple[bytes, tuple[EntitySpan, ...]]:
    body = b""
    spans = []
    for payload in payloads:
        spans.append(EntitySpan(start=len(body), end=len(body) + len(payload)))
        body += payload
    return body, tuple(spans)


_RECIPE_TAG = (
    object_property_tag_bytes(
        name="mCurrentRecipe",
        referenced_path="/Game/FactoryGame/Recipes/Recipe_IngotIron_C.Recipe_IngotIron_C",
    )
    + property_list_terminator_bytes()
)


def test_recipe_is_read_from_the_buildings_own_span() -> None:
    body, spans = _body_with_spans(_RECIPE_TAG)

    (record,) = to_placement_records_with_recipes((_SMELTER,), body, spans)

    assert record.building_id == "Build_SmelterMk1_C"
    assert record.recipe_id == "Recipe_IngotIron_C"


def test_building_without_a_recipe_property_keeps_none() -> None:
    body, spans = _body_with_spans(property_list_terminator_bytes())

    (record,) = to_placement_records_with_recipes((_SMELTER,), body, spans)

    assert record.recipe_id is None


def test_a_neighbours_recipe_does_not_leak_across_spans() -> None:
    body, spans = _body_with_spans(property_list_terminator_bytes(), _RECIPE_TAG)

    first, second = to_placement_records_with_recipes((_SMELTER, _SMELTER), body, spans)

    assert first.recipe_id is None
    assert second.recipe_id == "Recipe_IngotIron_C"


def test_spans_are_paired_with_headers_including_skipped_non_buildings() -> None:
    """Spans pair with *every* object, not just buildings — so the recipe must follow the header
    it belongs to even when a non-building sits between them."""
    body, spans = _body_with_spans(property_list_terminator_bytes(), b"", _RECIPE_TAG)

    headers = (_PLAYER, _INVENTORY_COMPONENT, _SMELTER)
    records = to_placement_records_with_recipes(headers, body, spans)

    assert len(records) == 1
    assert records[0].recipe_id == "Recipe_IngotIron_C"


def test_span_count_must_match_header_count() -> None:
    body, spans = _body_with_spans(_RECIPE_TAG)

    with pytest.raises(ValueError, match="must pair up"):
        to_placement_records_with_recipes((_SMELTER, _SMELTER), body, spans)


def test_clock_speed_and_fuel_are_read_from_the_buildings_own_span() -> None:
    generator_properties = (
        float_property_tag_bytes(name="mCurrentPotential", value=2.5)
        + object_property_tag_bytes(
            name="mCurrentFuelClass",
            referenced_path="/Game/FactoryGame/Resource/RawResources/Coal/Desc_Coal.Desc_Coal_C",
        )
        + property_list_terminator_bytes()
    )
    body, spans = _body_with_spans(_RECIPE_TAG, generator_properties)

    smelter, generator = to_placement_records_with_recipes((_SMELTER, _GENERATOR), body, spans)

    assert (generator.clock_speed, generator.fuel_item_id) == (2.5, "Desc_Coal_C")
    assert generator.recipe_id is None
    assert (smelter.clock_speed, smelter.fuel_item_id) == (1.0, None)  # nothing saved: defaults


def test_standby_and_somersloop_boost_are_read_from_the_buildings_own_span() -> None:
    boosted_and_paused = (
        bool_property_tag_bytes(name="mIsProductionPaused")
        + float_property_tag_bytes(name="mCurrentProductionBoost", value=1.5)
        + _RECIPE_TAG
    )
    body, spans = _body_with_spans(boosted_and_paused, _RECIPE_TAG)

    first, second = to_placement_records_with_recipes((_SMELTER, _SMELTER), body, spans)

    assert (first.is_paused, first.production_boost) == (True, 1.5)
    assert (second.is_paused, second.production_boost) == (False, 1.0)  # nothing saved: defaults


def test_what_an_extractor_extracts_from_is_read_from_its_span() -> None:
    miner_properties = (
        level_object_reference_tag_bytes(
            name="mExtractableResource", path="Persistent_Level:PersistentLevel.BP_ResourceNode103"
        )
        + property_list_terminator_bytes()
    )
    body, spans = _body_with_spans(_RECIPE_TAG, miner_properties)

    smelter, miner = to_placement_records_with_recipes((_SMELTER, _MINER), body, spans)

    assert miner.resource_node_id == "Persistent_Level:PersistentLevel.BP_ResourceNode103"
    assert smelter.resource_node_id is None
