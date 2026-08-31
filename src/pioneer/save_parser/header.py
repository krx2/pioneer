"""Parses the fixed-layout save header that precedes the compressed chunk stream.

Field layout reverse-engineered by the community — see
https://docs.ficsit.app/satisfactory-modding/latest/Development/Satisfactory/Savegame.html and
https://github.com/moritz-h/satisfactory-3d-map/blob/master/docs/SATISFACTORY_SAVE.md. Verified
byte-for-byte against a real save committed at tests/save_parser/fixtures/stal_mielec.sav (see
tests/save_parser/test_real_saves.py): every field through `editor_object_version` decodes to a
sane value (a `save_name` matching the actual filename, a `play_duration_seconds` in a believable
range, ...), and the chunk stream's magic number turns up exactly where expected afterward.

Known gap: the trailing metadata block isn't parsed field-by-field. Its true layout — confirmed
straight from Coffee Stain's own `FSaveHeader` struct declaration, shipped with the game at
`CommunityResources/Headers.zip` (`Source/FactoryGame/Public/FGSaveManagerInterface.h`) — is:

    FString ModMetadata; bool IsModdedSave; FMD5Hash SaveDataHash (bIsValid + [16 bytes]);
    bool IsEditedSave (commented "set at load", i.e. never actually serialized to disk);
    FString SaveIdentifier; bool IsPartitionedWorld; bool IsCreativeModeEnabled;

That header only declares the struct, not the `operator<<` that serializes it (compiled into the
game, not shipped), so the exact bool width and field order are still inferred rather than
certain — though this layout does cleanly explain every byte in the real fixture saves (e.g.
`IsModdedSave` serialized as a 4-byte bool, `SaveDataHash.bIsValid == false` contributing 1 byte
with no trailing hash, landing exactly on a valid `SaveIdentifier` length prefix). None of these
fields are needed by this project's contracts (`ProductionGraph`, `PlacementRecord` — see
contracts/production_graph.py and contracts/placement.py) regardless, so rather than lock in that
inference, `parse_header` stops trusting the byte stream after `editor_object_version` and
*resyncs* by scanning forward for the chunk stream's own magic number instead of parsing the
trailing block field-by-field. This is robust to that block's layout being subtly wrong, or
changing between game versions, in a way a field-by-field guess is not.
"""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.save_parser.binary_reader import ByteReader

_PACKAGE_FILE_TAG_BYTES = (0x9E2A83C1).to_bytes(4, "little")


@dataclass(frozen=True)
class SaveHeader:
    """The header fields this module actually trusts and surfaces.

    Not a Stage 1 contract — nothing outside `save_parser` needs a save's header metadata; this is
    an internal detail of getting to the chunk stream.
    """

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
        reader.read_int64()  # SaveDateTime, .NET ticks — not surfaced, see module docstring
    if save_header_version >= 7:
        reader.read_int32()  # EditorObjectVersion — not surfaced, see module docstring

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
    body_offset = _find_chunk_stream_start(data, reader.offset)
    return ParsedHeader(header=header, body_offset=body_offset)


def _find_chunk_stream_start(data: bytes, search_from: int) -> int:
    """Scan forward for the chunk stream's magic number — see module docstring for why this beats
    parsing the trailing metadata block field-by-field."""
    index = data.find(_PACKAGE_FILE_TAG_BYTES, search_from)
    if index == -1:
        raise ValueError("could not locate chunk stream start (package file tag not found)")
    return index
