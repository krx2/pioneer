"""Locates and parses the save's object table (TOC): a flat list of every actor/component header
in the world — class name, level-relative path, and (for actors) world transform.

Getting here means walking past `FSaveObjectVersionData` and `FWorldPartitionValidationData`,
neither of which is documented anywhere (community docs predate the game's UE5/World Partition
rewrite; the closest reference implementation found, andraz/satisfactory-save-editor's
`skipPartitions`, was reverse-engineered by hand against this repo's own fixture saves and gets
*close* but not byte-exact — e.g. `HLOD*`-named partitions carry an extra field its per-partition
shape doesn't account for, and per-cell entries within an HLOD partition don't reliably decode as
plain `FString`, unlike every other partition type). None of that data matters to this project's
contracts anyway, so rather than nail its exact layout, this module skips straight past it with
the same resync philosophy as `header.py`: search forward for a structural signature that can
only plausibly be the object table itself, instead of parsing everything leading up to it.

**The anchor:** every object header starts `[type: int32][ClassName: FString]`, and every object
table starts `[length: int64][numObjects: int32]` immediately before its first header. `ClassName`
is a UE class path, always starting with `/Game/` or `/Script/`. So: find a `/Game/` or `/Script/`
occurrence, walk backward through the fixed-width fields that must precede it if it's really a
table's first `ClassName`, and check they're self-consistent (small `type` flag, a plausible
object count, a plausible table byte length). Verified against both real saves committed at
tests/save_parser/fixtures/: exactly one such position exists in each, walking `numObjects`
headers from it decodes cleanly with zero errors, and the recognizable results (starting
buildings, vehicle classes, ...) match what's actually in each save — see
test_real_saves_object_table.py.

**The declared `length` covers more than the headers.** It doesn't land on the offset reached after
reading `numObjects` headers (off by 29 bytes on one fixture, 177 on the other) because a trailing
block follows them inside the same section: a persistent-level flag, the level name, and a list of
"collected" object references. 29 bytes is exactly `int32` flag + `FString "Persistent_Level"` +
`int32` zero collectables; the other fixture's 177 is the same plus its non-empty collectable list.
`numObjects` drives header parsing, and `section_end_offset` (from that declared `length`) is what
`entities.py` uses to skip the whole trailing block in one step — verified to land exactly on the
entity section's own length prefix in both real saves.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from pioneer.contracts import Coordinates
from pioneer.save_parser.binary_reader import ByteReader

# Production defaults, empirically tuned against both real saves committed at
# tests/save_parser/fixtures/: real save bodies are littered with small, self-consistent-looking
# (length, count, type) triples that aren't the object table at all (ordinary array-of-object-
# reference properties elsewhere in the data) -- a real level's object table is enormous by
# comparison (tens of thousands of objects, megabytes of header data), so requiring a minimum
# object count and table length is what actually distinguishes it from those false leads. Tests
# pass smaller thresholds to keep hand-built fixtures small; see test_object_table.py.
_DEFAULT_MIN_OBJECT_COUNT = 1_000
_MAX_PLAUSIBLE_OBJECT_COUNT = 5_000_000
_DEFAULT_MIN_TABLE_LENGTH = 500_000
_CLASS_PATH_PREFIXES = (b"/Game/", b"/Script/")


@dataclass(frozen=True)
class RawObjectHeader:
    """One TOC entry. Not a Stage 1 contract — this is the raw shape straight off the wire;
    mapping it into `PlacementRecord`/`ProductionNode` (with class filtering, recipe lookup, ...)
    is a later step in this module."""

    is_actor: bool
    class_name: str
    level_name: str
    path_name: str
    object_flags: int
    position: Coordinates | None
    """World position — present when `is_actor`, `None` for components (which have no transform;
    they carry an `OuterPathName` instead, consumed here but not surfaced)."""


@dataclass(frozen=True)
class ObjectTable:
    headers: tuple[RawObjectHeader, ...]
    headers_end_offset: int
    """Byte offset just past the last header — the start of the section's trailing block (see
    module docstring)."""
    section_end_offset: int
    """Byte offset just past the whole objects section, from its own declared `length`. Where the
    entity data section begins — see `entities.find_entity_spans`."""


def find_and_read_object_table(
    body: bytes,
    search_from: int = 0,
    *,
    min_object_count: int = _DEFAULT_MIN_OBJECT_COUNT,
    min_table_length: int = _DEFAULT_MIN_TABLE_LENGTH,
) -> tuple[RawObjectHeader, ...]:
    """Just the headers — see `find_object_table` when the section's byte offsets are needed too."""
    return find_object_table(
        body,
        search_from,
        min_object_count=min_object_count,
        min_table_length=min_table_length,
    ).headers


def find_object_table(
    body: bytes,
    search_from: int = 0,
    *,
    min_object_count: int = _DEFAULT_MIN_OBJECT_COUNT,
    min_table_length: int = _DEFAULT_MIN_TABLE_LENGTH,
) -> ObjectTable:
    """Locates the object table in `body` (searching from `search_from` onward) and parses every
    entry in it. Raises `ValueError` if no plausible table is found.

    `min_object_count`/`min_table_length` exist to tell the real table apart from smaller,
    coincidentally self-consistent-looking data elsewhere in the body — see module docstring.
    Lower them for small hand-built fixtures; the production defaults are what's validated against
    real saves.
    """
    table_start = _find_object_table_start(body, search_from, min_object_count, min_table_length)
    return _read_object_table(body, table_start)


def _find_object_table_start(
    body: bytes, search_from: int, min_object_count: int, min_table_length: int
) -> int:
    for prefix in _CLASS_PATH_PREFIXES:
        offset = search_from
        while True:
            offset = body.find(prefix, offset, len(body))
            if offset == -1:
                break
            candidate = _validate_candidate(body, offset, min_object_count, min_table_length)
            if candidate is not None:
                return candidate
            offset += 1
    raise ValueError(
        "could not locate the object table: no position looked like a table's first "
        "ClassName preceded by a self-consistent (length, numObjects, type) triple"
    )


def _validate_candidate(
    body: bytes, class_name_string_offset: int, min_object_count: int, min_table_length: int
) -> int | None:
    """`class_name_string_offset` is where a `/Game/`/`/Script/` match starts, i.e. right after a
    `ClassName` FString's 4-byte length prefix. Walk backward through the fields that must precede
    a table's *first* object header — `type`, `numObjects`, `length` — and check self-consistency.
    """
    length_prefix_offset = class_name_string_offset - 4
    type_offset = length_prefix_offset - 4
    num_objects_offset = type_offset - 4
    length_offset = num_objects_offset - 8
    if length_offset < 0:
        return None

    (type_flag,) = struct.unpack_from("<i", body, type_offset)
    if type_flag not in (0, 1):
        return None

    (num_objects,) = struct.unpack_from("<i", body, num_objects_offset)
    if not (min_object_count <= num_objects <= _MAX_PLAUSIBLE_OBJECT_COUNT):
        return None

    (length,) = struct.unpack_from("<q", body, length_offset)
    if not (min_table_length <= length <= len(body)):
        return None

    return length_offset


def _read_object_table(body: bytes, table_start: int) -> ObjectTable:
    reader = ByteReader(body, table_start)
    section_length = reader.read_int64()
    section_start = reader.offset  # the length counts everything *after* itself
    num_objects = reader.read_int32()
    headers = tuple(_read_one_object_header(reader) for _ in range(num_objects))
    return ObjectTable(
        headers=headers,
        headers_end_offset=reader.offset,
        section_end_offset=section_start + section_length,
    )


def _read_one_object_header(reader: ByteReader) -> RawObjectHeader:
    is_actor = reader.read_int32() == 1
    class_name = reader.read_fstring()
    level_name = reader.read_fstring()
    path_name = reader.read_fstring()
    object_flags = reader.read_uint32()

    position = None
    if is_actor:
        reader.read_int32()  # NeedTransform -- not surfaced
        reader.read_floats(4)  # rotation quaternion -- not needed for PlacementRecord
        x, y, z = reader.read_floats(3)
        reader.read_floats(3)  # scale -- not needed for PlacementRecord
        reader.read_int32()  # WasPlacedInLevel -- not surfaced
        position = Coordinates(x=x, y=y, z=z)
    else:
        reader.read_fstring()  # OuterPathName -- components only, not surfaced

    return RawObjectHeader(
        is_actor=is_actor,
        class_name=class_name,
        level_name=level_name,
        path_name=path_name,
        object_flags=object_flags,
        position=position,
    )
