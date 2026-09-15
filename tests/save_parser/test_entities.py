"""Tests for entity framing, against hand-built byte buffers — no real save file involved. See
test_real_saves_entities.py for the bonus real-file check."""

import struct

import pytest
from tests.save_parser.byte_builders import (
    actor_header_bytes,
    entity_bytes,
    entity_section_bytes,
    fstring,
    object_property_tag_bytes,
    object_table_bytes,
    property_list_terminator_bytes,
)

from pioneer.save_parser.entities import find_entity_spans
from pioneer.save_parser.object_table import find_object_table

_TRAILING_BLOCK = struct.pack("<i", 1) + fstring("Persistent_Level") + struct.pack("<i", 0)
"""Persistent-level flag + level name + zero collectables — the block that sits between the last
object header and the entity section in a real save."""


def _save_bytes(entity_payloads: list[bytes], *, class_names: list[str] | None = None) -> bytes:
    names = class_names or [f"/Game/Foo/Bar{i}.Bar{i}_C" for i in range(len(entity_payloads))]
    table = object_table_bytes(
        [actor_header_bytes(class_name=name) for name in names],
        trailing_block=_TRAILING_BLOCK,
    )
    entities = entity_section_bytes([entity_bytes(p) for p in entity_payloads])
    return table + entities


def test_frames_every_entity_in_toc_order() -> None:
    body = _save_bytes([b"\x01" * 10, b"\x02" * 25, b"\x03" * 4])

    table = find_object_table(body, min_object_count=1, min_table_length=1)
    spans = find_entity_spans(body, table)

    assert len(spans) == 3
    assert [s.end - s.start for s in spans] == [10, 25, 4]
    assert body[spans[0].start : spans[0].end] == b"\x01" * 10
    assert body[spans[1].start : spans[1].end] == b"\x02" * 25
    assert body[spans[2].start : spans[2].end] == b"\x03" * 4


def test_spans_are_contiguous_and_stay_inside_the_section() -> None:
    body = _save_bytes([b"\xaa" * 8, b"\xbb" * 8])

    table = find_object_table(body, min_object_count=1, min_table_length=1)
    spans = find_entity_spans(body, table)

    assert spans[0].end < spans[1].start  # the next entity's own header sits in between
    assert spans[-1].end <= len(body)


def test_span_isolates_one_entitys_properties() -> None:
    """The whole point of framing: a search bounded to one span can't see a neighbour's data."""
    recipe_tag = object_property_tag_bytes(
        name="mCurrentRecipe",
        referenced_path="/Game/FactoryGame/Recipes/Recipe_IronPlate_C.Recipe_IronPlate_C",
    )
    body = _save_bytes([recipe_tag + property_list_terminator_bytes(), b"\x00" * 12])

    table = find_object_table(body, min_object_count=1, min_table_length=1)
    spans = find_entity_spans(body, table)

    from pioneer.save_parser.properties import find_recipe_ids

    assert find_recipe_ids(body, start=spans[0].start, end=spans[0].end) == ("Recipe_IronPlate_C",)
    assert find_recipe_ids(body, start=spans[1].start, end=spans[1].end) == ()


def test_entity_count_disagreeing_with_the_object_table_is_rejected() -> None:
    table = object_table_bytes(
        [actor_header_bytes(class_name="/Game/Foo/Bar.Bar_C")], trailing_block=_TRAILING_BLOCK
    )
    entities = entity_section_bytes([entity_bytes(b"\x01" * 4), entity_bytes(b"\x02" * 4)])
    body = table + entities

    parsed = find_object_table(body, min_object_count=1, min_table_length=1)

    with pytest.raises(ValueError, match="same list"):
        find_entity_spans(body, parsed)


def test_declared_section_length_disagreeing_with_the_walk_is_rejected() -> None:
    table = object_table_bytes(
        [actor_header_bytes(class_name="/Game/Foo/Bar.Bar_C")], trailing_block=_TRAILING_BLOCK
    )
    entities = bytearray(entity_section_bytes([entity_bytes(b"\x01" * 4)]))
    entities[0:8] = struct.pack("<q", 999)  # corrupt the section's own declared length
    body = table + bytes(entities)

    parsed = find_object_table(body, min_object_count=1, min_table_length=1)

    with pytest.raises(ValueError, match="declares 999"):
        find_entity_spans(body, parsed)
