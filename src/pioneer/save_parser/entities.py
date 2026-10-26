"""Frames the entity data section: the per-object property blobs that follow the object table, in
the same order, so `spans[i]` belongs to `table.headers[i]`.

Framing only: each entity is preceded by `[entitySaveVersion: uint32]
[shouldMigrateObjectRefsToPersistentFlag: uint32][length: int32]`, `length` counting every byte
after itself, so the walk needs no per-class decoding of what's inside (cf. SC-InteractiveMap's
`src/SaveParser/Read.js: readEntity`). `properties.py` then searches one blob's byte range.
"""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.save_parser.binary_reader import ByteReader
from pioneer.save_parser.object_table import ObjectTable

_DATA_PACKAGE_VERSION_SAVE_VERSION = 53
"""From this entity save version on, each entity is followed by an optional data-package version
block (engine/licensee/custom versions). Only its presence flag and size matter here."""


@dataclass(frozen=True)
class EntitySpan:
    """Byte range of one entity's serialized data within the decompressed body, TOC-ordered."""

    start: int
    end: int


def find_entity_spans(body: bytes, table: ObjectTable) -> tuple[EntitySpan, ...]:
    """One span per object in `table.headers`, in the same order. Raises `ValueError` if the
    section's own entity count or declared length disagrees with what was walked."""
    # The objects section's trailing block (persistent-level flag, level name, collected-object
    # references) is skipped via its declared length: none of it feeds this project's contracts.
    reader = ByteReader(body, table.section_end_offset)

    entities_length = reader.read_int64()
    entities_start = reader.offset
    count = reader.read_int32()
    if count != len(table.headers):
        raise ValueError(
            f"entity section declares {count} entities but the object table holds "
            f"{len(table.headers)} objects -- they are supposed to be the same list"
        )

    spans = tuple(_read_one_entity_span(reader) for _ in range(count))

    consumed = reader.offset - entities_start
    if consumed != entities_length:
        raise ValueError(
            f"walked {consumed} bytes of entity data, but the section declares {entities_length}"
        )
    return spans


def _read_one_entity_span(reader: ByteReader) -> EntitySpan:
    entity_save_version = reader.read_uint32()
    reader.read_uint32()  # shouldMigrateObjectRefsToPersistentFlag -- not surfaced
    length = reader.read_int32()

    start = reader.offset
    reader.offset = start + length

    if entity_save_version >= _DATA_PACKAGE_VERSION_SAVE_VERSION and reader.read_int32() != 0:
        _skip_data_package_version(reader)

    return EntitySpan(start=start, end=start + length)


def _skip_data_package_version(reader: ByteReader) -> None:
    reader.read_int32()  # save object version
    reader.read_int32()  # UE4 package file version
    reader.read_int32()  # UE5 package file version
    reader.read_int32()  # licensee version
    reader.read_bytes(6)  # engine version major/minor/patch, uint16 each
    reader.read_uint32()  # engine changelist
    reader.read_fstring()  # engine branch
    for _ in range(reader.read_int32()):
        reader.read_bytes(16)  # custom version GUID
        reader.read_int32()  # custom version
