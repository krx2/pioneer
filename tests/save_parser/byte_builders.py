"""Hand-built byte fixtures shared by save_parser's own tests — not a test module itself (no
`test_` prefix, so pytest won't collect it), just the write-side counterpart of
`binary_reader.ByteReader` used to construct known-good `.sav`-shaped buffers by hand."""

from __future__ import annotations

import struct
import zlib

PACKAGE_FILE_TAG = 0x9E2A83C1


def fstring(value: str) -> bytes:
    """Mirrors `ByteReader.read_fstring`'s ASCII branch: empty string -> length 0, no data."""
    if value == "":
        return struct.pack("<i", 0)
    encoded = value.encode("ascii") + b"\x00"
    return struct.pack("<i", len(encoded)) + encoded


def header_bytes(
    *,
    save_header_version: int = 14,
    save_version: int = 60,
    build_version: int = 495413,
    save_name: str = "test_save",
    map_name: str = "Persistent_Level",
    map_options: str = "",
    session_name: str = "test session",
    play_duration_seconds: int = 999,
    save_datetime_ticks: int = 638207885677380000,
    editor_object_version: int = 10240,
    trailing_metadata: bytes = b"\x00" * 12,
) -> bytes:
    """A hand-built header matching `header.parse_header`'s field-by-field trust boundary,
    followed by arbitrary `trailing_metadata` standing in for the block `parse_header` doesn't
    trust (see header.py's module docstring) — proving the parser doesn't need it to be correct.
    """
    out = struct.pack("<iii", save_header_version, save_version, build_version)
    if save_header_version >= 14:
        out += fstring(save_name)
    out += fstring(map_name)
    out += fstring(map_options)
    if save_header_version >= 4:
        out += fstring(session_name)
    if save_header_version >= 3:
        out += struct.pack("<i", play_duration_seconds)
    if save_header_version >= 4:
        out += struct.pack("<q", save_datetime_ticks)
    if save_header_version >= 7:
        out += struct.pack("<i", editor_object_version)
    out += trailing_metadata
    return out


def chunk_bytes(uncompressed: bytes) -> bytes:
    """One chunk, framed exactly as `chunks._read_one_chunk` expects to read it."""
    compressed = zlib.compress(uncompressed)
    out = struct.pack("<I", PACKAGE_FILE_TAG)
    out += struct.pack("<i", 0x22222222)  # archive header magic
    out += struct.pack("<q", 131072)  # max chunk size
    out += bytes([3])  # compressor num (zlib)
    out += struct.pack("<q", len(compressed))  # compressed size summary
    out += struct.pack("<q", len(uncompressed))  # uncompressed size summary
    out += struct.pack("<q", len(compressed))  # compressed size
    out += struct.pack("<q", len(uncompressed))  # uncompressed size
    out += compressed
    return out


def body_bytes(payload: bytes) -> bytes:
    """A decompressed level-data body: the int64 `TotalSize` prefix + `payload`, matching what
    `loader._validate_total_size` checks."""
    return struct.pack("<q", len(payload)) + payload


def actor_header_bytes(
    *,
    class_name: str,
    level_name: str = "Persistent_Level",
    path_name: str = "Persistent_Level:PersistentLevel.SomeActor_1",
    object_flags: int = 8,
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> bytes:
    """One `FActorSaveHeader` TOC entry, framed exactly as `object_table._read_one_object_header`
    expects for `is_actor=True`."""
    out = struct.pack("<i", 1)  # type: actor
    out += fstring(class_name) + fstring(level_name) + fstring(path_name)
    out += struct.pack("<I", object_flags)
    out += struct.pack("<i", 1)  # NeedTransform
    out += struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)  # identity rotation quaternion
    out += struct.pack("<3f", *position)
    out += struct.pack("<3f", 1.0, 1.0, 1.0)  # scale
    out += struct.pack("<i", 0)  # WasPlacedInLevel
    return out


def component_header_bytes(
    *,
    class_name: str,
    level_name: str = "Persistent_Level",
    path_name: str = "Persistent_Level:PersistentLevel.SomeActor_1.SomeComponent",
    object_flags: int = 8,
    outer_path_name: str = "Persistent_Level:PersistentLevel.SomeActor_1",
) -> bytes:
    """One `FObjectSaveHeader` TOC entry (`is_actor=False`) — components carry an extra
    `OuterPathName` instead of a transform."""
    out = struct.pack("<i", 0)  # type: component
    out += fstring(class_name) + fstring(level_name) + fstring(path_name)
    out += struct.pack("<I", object_flags)
    out += fstring(outer_path_name)
    return out


def object_property_tag_bytes(
    *,
    name: str,
    referenced_path: str,
    size: int = 0,
    array_index: int = 0,
    padding_before_value: bytes = b"\x00\x00\x00\x00\x00",
) -> bytes:
    """One `ObjectProperty`-typed tag as `properties.read_property_tag` +
    `read_object_reference_value` expect to resync onto: `[Name][Type="ObjectProperty"]
    [ArrayIndex][Size]`, then arbitrary `padding_before_value` (standing in for the tag's
    `HasPropertyGuid` byte and whatever else -- see properties.py's module docstring for why the
    real value is found by resync, not a fixed offset) before the referenced object's own path
    `FString`.
    """
    out = fstring(name) + fstring("ObjectProperty")
    out += struct.pack("<ii", array_index, size)
    out += padding_before_value
    out += fstring(referenced_path)
    return out


def float_property_tag_bytes(
    *, name: str, value: float, property_guid: bytes | None = None
) -> bytes:
    """One `FloatProperty` tag as `properties.read_float_property` reads it:
    `[Name][Type="FloatProperty"][ArrayIndex=0][Size=4]`, the `HasPropertyGuid` byte (followed by
    the 16-byte GUID when one is given), then the float itself."""
    out = fstring(name) + fstring("FloatProperty") + struct.pack("<ii", 0, 4)
    out += b"\x00" if property_guid is None else b"\x01" + property_guid
    return out + struct.pack("<f", value)


def bool_property_tag_bytes(*, name: str, value_bytes: bytes = b"\x00\x10") -> bytes:
    """One `BoolProperty` tag as `properties.read_bool_property` reads it: `[Name]
    [Type="BoolProperty"][0][Size=0]`, then the two bytes carrying the value — `00 10` (the
    `BoolTrue` flag) as every real save seen writes a true one."""
    out = fstring(name) + fstring("BoolProperty") + struct.pack("<ii", 0, 0)
    return out + value_bytes


def object_reference_array_tag_bytes(*, name: str, paths: list[str]) -> bytes:
    """One `ArrayProperty` of class references as `properties.read_object_reference_array` reads
    it: `[Name][Type="ArrayProperty"][1][FString "ObjectProperty"][0][Size][flags]`, then the
    element count and each element as an empty level name and its path."""
    value = struct.pack("<i", len(paths)) + b"".join(fstring("") + fstring(p) for p in paths)
    out = fstring(name) + fstring("ArrayProperty") + struct.pack("<i", 1)
    out += fstring("ObjectProperty") + struct.pack("<ii", 0, len(value)) + b"\x00"
    return out + value


def level_object_reference_tag_bytes(
    *, name: str, path: str, level: str = "Persistent_Level"
) -> bytes:
    """One `ObjectProperty` tag pointing at an object placed in the level, as
    `properties.read_level_object_reference` reads it: the tag with `Size` covering the value, the
    `HasPropertyGuid` byte, then the level name and the object's path name."""
    value = fstring(level) + fstring(path)
    out = fstring(name) + fstring("ObjectProperty") + struct.pack("<ii", 0, len(value))
    return out + b"\x00" + value


def property_list_terminator_bytes() -> bytes:
    """The `"None"` tag that ends a property list — just its name `FString`, per
    `properties.read_property_tag`."""
    return fstring("None")


def object_table_bytes(headers: list[bytes], *, trailing_block: bytes = b"") -> bytes:
    """The `[length: int64][numObjects: int32][headers...][trailing_block]` framing `object_table`
    resyncs onto and reads. `trailing_block` stands in for the real format's persistent-level flag,
    level name and collected-object list, which `entities.find_entity_spans` skips wholesale via
    the declared `length` — so `length` covers it here, exactly as in a real save."""
    payload = struct.pack("<i", len(headers)) + b"".join(headers) + trailing_block
    return struct.pack("<q", len(payload)) + payload


def entity_bytes(payload: bytes, *, entity_save_version: int = 60) -> bytes:
    """One entity, framed as `entities._read_one_entity_span` expects:
    `[entitySaveVersion: uint32][shouldMigrate: uint32][length: int32][payload]`, plus the
    "no data package version follows" int32 that trails entities from save version 53 on."""
    out = struct.pack("<II", entity_save_version, 0)
    out += struct.pack("<i", len(payload))
    out += payload
    if entity_save_version >= 53:
        out += struct.pack("<i", 0)  # haveDataPackageVersion
    return out


def entity_section_bytes(entities: list[bytes]) -> bytes:
    """The `[length: int64][countEntities: int32][entities...]` section that follows the objects
    section — `length` counts everything after itself, as in a real save."""
    payload = struct.pack("<i", len(entities)) + b"".join(entities)
    return struct.pack("<q", len(payload)) + payload
