"""Extracts specific named properties (currently: `mCurrentRecipe`, the recipe a manufacturing
building is currently running) from a decompressed save body's object-property data.

Inspired by SC-InteractiveMap's (github.com/AnthorNet/SC-InteractiveMap) generic UE property-tag
reader and its `mCurrentRecipe` lookup in `Building/Production.js` -- reimplemented independently
here against this project's own real save fixtures (different language, and a much narrower scope:
this only decodes the one property shape this project actually needs, not a full property-bag
reader). See that project's `src/SaveParser/Read.js` for the general `[Name][Type][Size]
[ArrayIndex]` tag shape this mirrors.

**What's solved, verified against both real fixture saves at tests/save_parser/fixtures/:** every
`mCurrentRecipe` property tag anywhere in the decompressed body can be found and decoded into the
recipe's class path. All 500 occurrences in `stal_mielec.sav` and all 906 in
`wielka_polska_niesmiertelna.sav` resolve to a real `recipe_id` in the Knowledge Base loaded from
`docs/en-US.json` (see test_real_saves_properties.py) -- zero unresolved.

**Field order, confirmed against real data:** a tag's two `int32`s are `ArrayIndex` then `Size` (in
that order) -- verified by matching a `UInt32Property`'s declared `Size` (4) against the real,
independently-known offset of the *next* tag. `PropertyTag.size` is `0` on every `ObjectProperty`
occurrence in both real fixtures, though (game-specific: the engine doesn't bother filling it in
for object references), so it can't bound an `ObjectProperty` read even once you're reading the
right field. `read_object_reference_value` falls back to the same resync technique `header.py` and
`object_table.py` already use elsewhere in this package: search forward a bounded window for the
referenced object's own `/Game/` or `/Script/` class-path `FString`, rather than parsing the tag's
further undocumented header fields (there's at least a `HasPropertyGuid: bool`, sometimes followed
by a 16-byte GUID, before the value -- confirmed present but not modeled here since resync doesn't
need it).

**Attribution** -- which building a given `mCurrentRecipe` belongs to -- comes from the
`start`/`end` bounds a caller passes in: `entities.find_entity_spans` frames each object's property
blob, so searching one span finds only that object's own properties. Both real fixture saves
attribute every occurrence (500 and 906) to exactly one building, every owner a manufacturer class.

**Why not a full property-list walker.** Reaching a property by resync inside a known byte range
avoids decoding every property type along the way, which is a much bigger job than it looks:
`Bool`/`Byte`/`Enum`/`Struct`/`Array` tags each carry extra type-specific header fields before
their value, and containers (`Map`/`Set`) carry a recursive type-name-node tree -- a real
`mSaveData` MapProperty seen while developing this nests `StructProperty` -> `IntVector` ->
`/Script/CoreUObject`. That complexity is why SC-InteractiveMap needs a file per building type
rather than one generic reader; framing plus a bounded search needs none of it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from pioneer.save_parser.binary_reader import ByteReader

_CLASS_PATH_PREFIXES = (b"/Game/", b"/Script/")
_OBJECT_REFERENCE_SEARCH_WINDOW = 256
"""How far past an `ObjectProperty` tag's header to search for its class-path `FString` -- see
module docstring. Generous relative to every occurrence seen in both real fixture saves (the path
itself always starts within a handful of bytes of the tag header)."""


@dataclass(frozen=True)
class PropertyTag:
    """One `FPropertyTag`-shaped header, without its value -- see `read_property_tag`."""

    name: str
    type_name: str
    size: int
    array_index: int


def read_property_tag(reader: ByteReader) -> PropertyTag | None:
    """Reads one property tag header: `[Name FString]["None" -> the property list has ended,
    returns None][Type FString][ArrayIndex int32][Size int32]`. Leaves `reader` positioned right
    after the tag -- not yet at the value, for every type this module has confirmed carries further
    type-specific header fields first (at minimum a `HasPropertyGuid` bool; see module docstring).
    Callers decide how to consume the rest based on `type_name`/`size`."""
    name = reader.read_fstring()
    if name in ("", "None"):
        return None
    type_name = reader.read_fstring()
    array_index = reader.read_int32()
    size = reader.read_int32()
    return PropertyTag(name=name, type_name=type_name, size=size, array_index=array_index)


def read_object_reference_value(
    reader: ByteReader, *, search_window: int = _OBJECT_REFERENCE_SEARCH_WINDOW
) -> str | None:
    """Reads an `ObjectProperty` value: a reference to another object, serialized somewhere ahead
    as a class/object path `FString` (e.g.
    `/Game/FactoryGame/Recipes/Recipe_IronPlate_C.Recipe_IronPlate_C`). See module docstring for
    why this searches instead of trusting the tag's `size`. Returns `None` (leaving `reader`
    untouched) if no plausible path turns up within `search_window` bytes."""
    start = reader.offset
    window = reader.data[start : start + search_window]
    for prefix in _CLASS_PATH_PREFIXES:
        marker = window.find(prefix)
        if marker == -1:
            continue
        length_prefix_offset = start + marker - 4
        if length_prefix_offset < start:
            continue
        probe = ByteReader(reader.data, length_prefix_offset)
        try:
            path = probe.read_fstring()
        except (UnicodeDecodeError, IndexError):
            continue
        if path.startswith(("/Game/", "/Script/")):
            reader.offset = probe.offset
            return path
    return None


def _class_name_from_path(path: str) -> str:
    """The bare class name from a UE object path, e.g.
    `/Game/FactoryGame/Recipes/Recipe_IronPlate_C.Recipe_IronPlate_C` -> `Recipe_IronPlate_C`.
    Mirrors `knowledge_base.parsing.class_name_from_path` -- duplicated rather than imported, the
    same "depends only on `pioneer.contracts`" independence every module in this package keeps
    (see production_planner's own precedent for duplicating small pure logic over cross-importing).
    """
    return path.rsplit(".", 1)[-1].rstrip("'")


def find_recipe_paths(
    body: bytes,
    *,
    start: int = 0,
    end: int | None = None,
    property_name: str = "mCurrentRecipe",
) -> tuple[str, ...]:
    """Every `property_name` occurrence's referenced class path, in save-file order, searching the
    `[start, end)` byte range (the whole body by default; one `entities.EntitySpan` to get just one
    object's properties). Skips (rather than raising on) an occurrence whose value can't be
    resolved within the search window, since a handful of unrelated bytes coincidentally matching
    the property name is possible in a save this large, if vanishingly unlikely."""
    paths: list[str] = []
    name_needle = _fstring_needle(property_name)
    search_end = len(body) if end is None else end
    offset = start
    while True:
        offset = body.find(name_needle, offset, search_end)
        if offset == -1:
            break
        reader = ByteReader(body, offset)
        tag = read_property_tag(reader)
        offset += 1  # always advance past this occurrence, whether or not it resolves
        if tag is None:
            continue
        value = read_object_reference_value(reader)
        if value is not None:
            paths.append(value)
    return tuple(paths)


def find_recipe_ids(
    body: bytes,
    *,
    start: int = 0,
    end: int | None = None,
    property_name: str = "mCurrentRecipe",
) -> tuple[str, ...]:
    """Every `property_name` occurrence's recipe id (e.g. `Recipe_IronPlate_C`) in the `[start,
    end)` byte range, in save-file order."""
    return tuple(
        _class_name_from_path(path)
        for path in find_recipe_paths(body, start=start, end=end, property_name=property_name)
    )


def read_float_property(
    body: bytes, property_name: str, *, start: int = 0, end: int | None = None
) -> float | None:
    """The value of the first `FloatProperty` named `property_name` in the `[start, end)` byte
    range, or `None` if there is none — which, for a property like `mCurrentPotential`, means the
    default: the game only saves a property when it differs from its default.

    Layout, confirmed against both real fixture saves (every `mCurrentPotential` in them): the tag
    (see `read_property_tag`) with `Size` 4, then a one-byte `HasPropertyGuid` flag — 0 in every
    occurrence seen; a 16-byte GUID would follow if it were set — then the 4-byte float itself.
    """
    needle = _fstring_needle(property_name)
    search_end = len(body) if end is None else end
    offset = body.find(needle, start, search_end)
    while offset != -1:
        reader = ByteReader(body, offset)
        try:
            tag = read_property_tag(reader)
            if tag is not None and tag.type_name == "FloatProperty" and tag.size == 4:
                if reader.read_byte():
                    reader.read_bytes(16)  # the property GUID
                return reader.read_float()
        except (UnicodeDecodeError, IndexError, struct.error):
            pass  # bytes that merely look like the name -- keep searching
        offset = body.find(needle, offset + 1, search_end)
    return None


def _fstring_needle(value: str) -> bytes:
    """The exact byte pattern `read_fstring` would have written for `value`: a 4-byte little-endian
    length prefix (chars + null terminator) followed by the ASCII bytes and terminator -- what
    `bytes.find` searches for to locate a property tag by name."""
    encoded = value.encode("ascii") + b"\x00"
    return len(encoded).to_bytes(4, "little") + encoded
