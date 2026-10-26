"""Extracts specific named properties from a save body's object-property data: a manufacturer's
recipe (`mCurrentRecipe`), a generator's fuel (`mCurrentFuelClass`), clock speed
(`mCurrentPotential`), what an extractor works (`mExtractableResource`) and a few more.

A property is found by searching for its name inside one object's byte range (its entity span),
rather than walking the whole property list, which would mean decoding every type-specific tag
layout. The tag shape follows SC-InteractiveMap's `src/SaveParser/Read.js`:
`[Name FString][Type FString][int32][Size int32]`, the first int32 being the number of type
parameters (0 for a plain type).

- An `ObjectProperty` referencing a class has `Size` 0, so `read_object_reference_value` resyncs
  by searching a bounded window for the `/Game/` or `/Script/` class-path `FString`.
- A level object reference and a `FloatProperty`: a `HasPropertyGuid` byte (16-byte GUID when
  set), then the value; `Size` is exact.
- A `BoolProperty` has `Size` 0 and two bytes after it, `0x10` being the newer format's `BoolTrue`.
- An `ArrayProperty` of objects: `[1][FString "ObjectProperty"][0][Size][flags byte][count]`,
  then each element as a level name and a path name.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass

from pioneer.common import class_name_from_path
from pioneer.save_parser.binary_reader import ByteReader

_CLASS_PATH_PREFIXES = (b"/Game/", b"/Script/")
_BOOL_TRUE_FLAG = 0x10
"""`EPropertyTagFlags::BoolTrue` — a `BoolProperty`'s value, in UE's newer property-tag format."""
_OBJECT_REFERENCE_SEARCH_WINDOW = 256
"""How far past an `ObjectProperty` tag's header to search for its class-path `FString`: the path
starts within a handful of bytes in every save seen."""


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
    after the tag -- not yet at the value, which comes after further type-specific header fields
    (at minimum a `HasPropertyGuid` bool). Callers consume the rest by `type_name`/`size`."""
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
    `/Game/FactoryGame/Recipes/Recipe_IronPlate_C.Recipe_IronPlate_C`), searched for since the
    tag's `size` is 0. Returns `None` (leaving `reader`
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


def find_recipe_paths(
    body: bytes,
    *,
    start: int = 0,
    end: int | None = None,
    property_name: str = "mCurrentRecipe",
) -> tuple[str, ...]:
    """Every `property_name` occurrence's referenced class path, in save-file order, searching the
    `[start, end)` byte range (the whole body by default; one `entities.EntitySpan` to get just one
    object's properties). Skips an occurrence whose value can't be resolved within the search
    window: bytes merely looking like the name."""
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
        class_name_from_path(path)
        for path in find_recipe_paths(body, start=start, end=end, property_name=property_name)
    )


def read_float_property(
    body: bytes, property_name: str, *, start: int = 0, end: int | None = None
) -> float | None:
    """The value of the first `FloatProperty` named `property_name` in the `[start, end)` byte
    range, or `None` if there is none — which, for a property like `mCurrentPotential`, means the
    default: the game only saves a property when it differs from its default.
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
    range, or `None` if there is none — the default, like a float's. True when either the old
    format's value byte or the new format's `BoolTrue` flag is set.
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
    `None` if there's none. The elements must fill the tag's `Size` exactly, which tells the
    layout was read right."""

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

    After the `HasPropertyGuid` byte come two `FString`s, the level name and the path name, and
    `Size` must be exactly their length, so a class reference (`Size` 0) is never taken for one.
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
