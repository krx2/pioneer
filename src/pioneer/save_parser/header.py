"""Parses the fixed-layout save header that precedes the compressed chunk stream.

Field layout reverse-engineered by the community — see
https://docs.ficsit.app/satisfactory-modding/latest/Development/Satisfactory/Savegame.html and
https://github.com/moritz-h/satisfactory-3d-map/blob/master/docs/SATISFACTORY_SAVE.md.

The trailing metadata block isn't parsed. Per the game's `FSaveHeader` declaration
(`CommunityResources/Headers.zip`, `FGSaveManagerInterface.h`) it is:

    FString ModMetadata; bool IsModdedSave; FMD5Hash SaveDataHash (bIsValid + [16 bytes]);
    bool IsEditedSave (never serialized); FString SaveIdentifier; bool IsPartitionedWorld;
    bool IsCreativeModeEnabled;

but the serializer isn't shipped, so bool widths and order are inferred. Nothing here needs those
fields, so `parse_header` stops after `editor_object_version` and resyncs by scanning forward for
the chunk stream's magic number, which survives that block changing between game versions.
"""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.save_parser.binary_reader import ByteReader

_PACKAGE_FILE_TAG_BYTES = (0x9E2A83C1).to_bytes(4, "little")


@dataclass(frozen=True)
class SaveHeader:
    """The header fields this module actually trusts and surfaces."""

    save_header_version: int
    save_version: int
    build_version: int
    save_name: str
    map_name: str
    map_options: str
    session_name: str
    play_duration_seconds: int


@dataclass(frozen=True)
class ParsedHeader:
    header: SaveHeader
    body_offset: int
    """Byte offset of the first compressed chunk — where `chunks.decompress_all` should start."""


def parse_header(data: bytes) -> ParsedHeader:
    header, fields_end = _read_fields(data)
    body_offset = _find_chunk_stream_start(data, fields_end)
    return ParsedHeader(header=header, body_offset=body_offset)


def parse_header_fields(data: bytes) -> SaveHeader:
    """Just the header's fields, without looking for the chunk stream behind them — all that
    listing saves needs, from the first few kilobytes of each file (see `catalog.py`)."""
    return _read_fields(data)[0]


def _read_fields(data: bytes) -> tuple[SaveHeader, int]:
    """The trusted fields, and the offset right after the last of them."""
    reader = ByteReader(data)
    save_header_version = reader.read_int32()
    save_version = reader.read_int32()
    build_version = reader.read_int32()

    save_name = reader.read_fstring() if save_header_version >= 14 else ""
    map_name = reader.read_fstring()
    map_options = reader.read_fstring()
    session_name = reader.read_fstring() if save_header_version >= 4 else ""
    play_duration_seconds = reader.read_int32() if save_header_version >= 3 else 0
    if save_header_version >= 4:
        reader.read_int64()  # SaveDateTime, .NET ticks — not surfaced
    if save_header_version >= 7:
        reader.read_int32()  # EditorObjectVersion — not surfaced

    header = SaveHeader(
        save_header_version=save_header_version,
        save_version=save_version,
        build_version=build_version,
        save_name=save_name,
        map_name=map_name,
        map_options=map_options,
        session_name=session_name,
        play_duration_seconds=play_duration_seconds,
    )
    return header, reader.offset


def _find_chunk_stream_start(data: bytes, search_from: int) -> int:
    """Scan forward for the chunk stream's magic number, past the unparsed metadata block."""
    index = data.find(_PACKAGE_FILE_TAG_BYTES, search_from)
    if index == -1:
        raise ValueError("could not locate chunk stream start (package file tag not found)")
    return index
