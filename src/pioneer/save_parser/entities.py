"""Frames the *entity data* section: the per-object property blobs that follow the object table.

A save's level data is two parallel sections. `object_table.py` reads the first — the TOC, one
header per object (class name, path, transform). This module reads the framing of the second: for
every object, in the *same order*, a length-prefixed blob holding that object's serialized
properties. `spans[i]` is therefore the data belonging to `table.headers[i]`, which is what makes
attributing a property (e.g. `mCurrentRecipe`) to a specific building possible at all.

**Framing only, deliberately.** Each entity is preceded by
`[entitySaveVersion: uint32][shouldMigrateObjectRefsToPersistentFlag: uint32][length: int32]`, and
`length` counts every byte after itself. So walking the whole section needs no understanding of
what's *inside* a blob — no property-type dispatch, no per-class special cases. That matters,
because the contents genuinely do need per-class handling (containers like `MapProperty` nest
recursive type-name trees; conveyors, vehicles and power networks append class-specific data after
their property list), which is exactly why the reference implementation this was derived from
(SC-InteractiveMap, github.com/AnthorNet/SC-InteractiveMap, `src/SaveParser/Read.js: readEntity`)
carries a file per building type. Framing sidesteps all of it: `properties.py` then searches inside
one blob's bounded byte range for the one property this project needs.

Verified against both real fixture saves: every entity frames cleanly (29211 and 55012 of them),
the walk lands byte-exactly on the entity section's own declared end, and every `entitySaveVersion`
reads back as the same value the save header declares.
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
    section's own entity count or declared length disagrees with what was walked — both are strong
    integrity checks, in the same spirit as `loader._validate_total_size`."""
    # The objects section's trailing block (persistent-level flag, level name, collected-object
    # references) is skipped wholesale via its declared length rather than parsed field-by-field --
    # none of it feeds this project's contracts, and the declared end lands exactly on the entity
    # section in both real fixture saves. See object_table.py's module docstring.
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
