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


def object_table_bytes(headers: list[bytes]) -> bytes:
    """The `[length: int64][numObjects: int32][headers...]` framing `object_table` resyncs onto
    and reads. `length` is written correctly here (unlike some real saves — see
    object_table.py's module docstring) since this fixture is for testing the happy path."""
    payload = struct.pack("<i", len(headers)) + b"".join(headers)
    return struct.pack("<q", len(payload)) + payload
