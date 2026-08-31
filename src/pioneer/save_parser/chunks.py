"""Decompresses the chunk stream that follows the save header into one contiguous buffer.

Each chunk is independently zlib-compressed with a small fixed framing header. See
`header.py`'s module docstring for format references. Verified against a real save's full chunk
stream in tests/save_parser/test_real_saves.py: every chunk (294 of them, for the committed
`stal_mielec.sav` fixture) decompresses cleanly, and the pieces concatenate to exactly the length
the decompressed body's own embedded `TotalSize` field declares (see `loader.py`).
"""

from __future__ import annotations

import zlib

from pioneer.save_parser.binary_reader import ByteReader

_PACKAGE_FILE_TAG = 0x9E2A83C1


def decompress_all(data: bytes, start_offset: int) -> bytes:
    """Decompresses every chunk from `start_offset` to the end of `data`, concatenated in order."""
    reader = ByteReader(data, start_offset)
    pieces: list[bytes] = []
    while reader.remaining() > 0:
        pieces.append(_read_one_chunk(reader))
    return b"".join(pieces)


def _read_one_chunk(reader: ByteReader) -> bytes:
    tag = reader.read_uint32()
    if tag != _PACKAGE_FILE_TAG:
        raise ValueError(
            f"expected chunk tag {_PACKAGE_FILE_TAG:#x}, got {tag:#x} at offset {reader.offset - 4}"
        )
    reader.read_int32()  # archive header magic (0x00000000 or 0x22222222) — not needed
    reader.read_int64()  # max chunk size (always 131072 in practice) — not needed
    reader.read_byte()  # compressor num — always zlib (3) in every save this module has seen
    reader.read_int64()  # compressed size summary — redundant with per-chunk compressed_size below
    reader.read_int64()  # uncompressed size summary — redundant with uncompressed_size below
    compressed_size = reader.read_int64()
    uncompressed_size = reader.read_int64()

    compressed_data = reader.read_bytes(compressed_size)
    decompressed = zlib.decompress(compressed_data)
    if len(decompressed) != uncompressed_size:
        raise ValueError(
            f"chunk decompressed to {len(decompressed)} bytes, expected {uncompressed_size}"
        )
    return decompressed
