"""Extracts specific named properties from a decompressed save body's object-property data: the
recipe a manufacturer is running (`mCurrentRecipe`), a generator's fuel (`mCurrentFuelClass`), a
building's clock speed (`mCurrentPotential`), and what an extractor extracts from
(`mExtractableResource`).

Inspired by SC-InteractiveMap's (github.com/AnthorNet/SC-InteractiveMap) generic UE property-tag
reader and its `mCurrentRecipe` lookup in `Building/Production.js` -- reimplemented independently
here against this project's own real save fixtures (different language, and a much narrower scope:
this only decodes the few property shapes this project actually needs, not a full property-bag
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
occurrence that references a *class* (`mCurrentRecipe`, `mCurrentFuelClass`) in both real fixtures,
though, so it can't bound those reads. `read_object_reference_value` falls back to the same resync
technique `header.py` and `object_table.py` already use elsewhere in this package: search forward
a bounded window for the referenced object's own `/Game/` or `/Script/` class-path `FString`,
rather than parsing the tag's further undocumented header fields.

References to an object placed *in the level* (`mExtractableResource`) are better behaved, and
`FloatProperty` values too: after the tag comes a one-byte `HasPropertyGuid` flag (a 16-byte GUID
follows when it's set), then the value, and `Size` is exact — see `read_level_object_reference`
and `read_float_property`.

`BoolProperty` tags (`mIsProductionPaused`) have `Size` 0 and are followed by two bytes, seen as
`00 10` on every occurrence in the real fixtures: `0x10` is the `BoolTrue` flag of UE's newer
property-tag format, and the first byte sits where the older format kept `BoolVal` — see
`read_bool_property`.

The two `int32`s after a tag's type are, strictly, the number of parameters the type name carries
(0 for a plain type) and `Size`, as UE's newer tag format writes them — which reads the same as
`ArrayIndex`/`Size` for every plain type. An `ArrayProperty`'s one parameter is its element type,
serialized in between: `[1][FString "ObjectProperty"][0][Size][flags byte][count]` and then the
elements, each an object reference as a level name and a path name — how the schematic manager
saves `mPurchasedSchematics`. See `read_object_reference_array`.

**Attribution** -- which building a given property belongs to -- comes from the `start`/`end`
bounds a caller passes in: `entities.find_entity_spans` frames each object's property blob, so
searching one span finds only that object's own properties. Both real fixture saves attribute every
`mCurrentRecipe` occurrence (500 and 906) to exactly one building, every owner a manufacturer
class.

**Why not a full property-list walker.** Reaching a property by name inside a known byte range
avoids decoding every property type along the way, which is a much bigger job than it looks:
`Bool`/`Byte`/`Enum`/`Struct`/`Array` tags each carry extra type-specific header fields before
their value, and containers (`Map`/`Set`) carry a recursive type-name-node tree -- a real
`mSaveData` MapProperty seen while developing this nests `StructProperty` -> `IntVector` ->
`/Script/CoreUObject`. That complexity is why SC-InteractiveMap needs a file per building type
rather than one generic reader; framing plus a bounded search needs none of it.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass

from pioneer.save_parser.binary_reader import ByteReader

_CLASS_PATH_PREFIXES = (b"/Game/", b"/Script/")
_BOOL_TRUE_FLAG = 0x10
"""`EPropertyTagFlags::BoolTrue` — a `BoolProperty`'s value, in UE's newer property-tag format."""
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

    Confirmed against every `mCurrentPotential` in both real fixture saves: `Size` 4, then the
    `HasPropertyGuid` byte (0 in every occurrence seen), then the 4-byte float.
    """

    def value(tag: PropertyTag, reader: ByteReader) -> float | None:
        if tag.type_name != "FloatProperty" or tag.size != 4:
            return None
        _skip_property_guid(reader)
        return reader.read_float()

    return _first_value(body, property_name, start, end, value)


def read_bool_property(
    body: bytes, property_name: str, *, start: int = 0, end: int | None = None
) -> bool | None:
    """The value of the first `BoolProperty` named `property_name` in the `[start, end)` byte
    range, or `None` if there is none — the default, like a float's. The two bytes after the tag
    (see module docstring) read as true when either the old format's value byte or the new
    format's `BoolTrue` flag is set: both saved occurrences seen, of a flag whose default is false
    and so is only saved when true, read `00 10`.
    """

    def value(tag: PropertyTag, reader: ByteReader) -> bool | None:
        if tag.type_name != "BoolProperty" or tag.size != 0:
            return None
        legacy_value, flags = reader.read_byte(), reader.read_byte()
        return bool(legacy_value) or bool(flags & _BOOL_TRUE_FLAG)

    return _first_value(body, property_name, start, end, value)


def read_object_reference_array(
    body: bytes, property_name: str, *, start: int = 0, end: int | None = None
) -> tuple[str, ...] | None:
    """The path names in the first `ArrayProperty` of `ObjectProperty` named `property_name` in
    `[start, end)` — e.g. every schematic the player has unlocked,
    `/Game/FactoryGame/Schematics/Schematic_StartingRecipes.Schematic_StartingRecipes_C` — or
    `None` if there's none. The elements must fill the tag's `Size` exactly, which is what tells
    the layout (see module docstring) was read right."""

    def value(tag: PropertyTag, reader: ByteReader) -> tuple[str, ...] | None:
        if tag.type_name != "ArrayProperty" or tag.array_index != 1:
            return None
        reader.offset -= 4  # that "size" was the element type's length prefix
        if reader.read_fstring() != "ObjectProperty" or reader.read_int32() != 0:
            return None
        size = reader.read_int32()
        if reader.read_byte():
            return None  # a property GUID or extension this reader doesn't handle
        value_start = reader.offset
        paths = []
        for _ in range(reader.read_int32()):
            reader.read_fstring()  # the level name -- empty for a class
            paths.append(reader.read_fstring())
        return tuple(paths) if reader.offset - value_start == size else None

    return _first_value(body, property_name, start, end, value)


def read_level_object_reference(
    body: bytes, property_name: str, *, start: int = 0, end: int | None = None
) -> str | None:
    """The path name of the level object the first `ObjectProperty` named `property_name` in
    `[start, end)` points at — e.g. an extractor's `mExtractableResource`,
    `Persistent_Level:PersistentLevel.BP_ResourceNode103` — or `None` if there's none.

    Confirmed against every extractor in both real fixture saves (139 of them): after the
    `HasPropertyGuid` byte come two `FString`s, the level name and the object's path name, and the
    tag's `Size` is exactly their combined length — which this checks, so a class reference (whose
    `Size` is 0) is never mistaken for one.
    """

    def value(tag: PropertyTag, reader: ByteReader) -> str | None:
        if tag.type_name != "ObjectProperty":
            return None
        _skip_property_guid(reader)
        value_start = reader.offset
        reader.read_fstring()  # the level name
        path = reader.read_fstring()
        return path if path and reader.offset - value_start == tag.size else None

    return _first_value(body, property_name, start, end, value)


def _first_value[T](
    body: bytes,
    property_name: str,
    start: int,
    end: int | None,
    read_value: Callable[[PropertyTag, ByteReader], T | None],
) -> T | None:
    """The first non-`None` `read_value` over the tags named `property_name` in `[start, end)`,
    `reader` positioned just past each tag. An occurrence that fails to decode is bytes that merely
    look like the name — the search moves on."""
    needle = _fstring_needle(property_name)
    search_end = len(body) if end is None else end
    offset = body.find(needle, start, search_end)
    while offset != -1:
        reader = ByteReader(body, offset)
        try:
            tag = read_property_tag(reader)
            value = read_value(tag, reader) if tag is not None else None
        except (UnicodeDecodeError, IndexError, struct.error):
            value = None
        if value is not None:
            return value
        offset = body.find(needle, offset + 1, search_end)
    return None


def _skip_property_guid(reader: ByteReader) -> None:
    if reader.read_byte():
        reader.read_bytes(16)


def _fstring_needle(value: str) -> bytes:
    """The exact byte pattern `read_fstring` would have written for `value`: a 4-byte little-endian
    length prefix (chars + null terminator) followed by the ASCII bytes and terminator -- what
    `bytes.find` searches for to locate a property tag by name."""
    encoded = value.encode("ascii") + b"\x00"
    return len(encoded).to_bytes(4, "little") + encoded
