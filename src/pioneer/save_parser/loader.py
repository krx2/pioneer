"""Top-level entry point tying header parsing and chunk decompression together.

`load_body_from_file` / `load_body_from_bytes` return the fully decompressed level-data body,
ready for the object/TOC parsing that turns it into `ProductionGraph` + `PlacementRecord`. That
parsing isn't built yet — this is as far as Stage 5 has gotten so far; see the module docstrings
in `header.py` and `chunks.py` for what's already verified against real save data.
"""

from __future__ import annotations

import struct
from pathlib import Path

from pioneer.save_parser.chunks import decompress_all
from pioneer.save_parser.header import ParsedHeader, SaveHeader, parse_header


def load_body_from_file(path: Path | str) -> tuple[SaveHeader, bytes]:
    with open(path, "rb") as f:
        data = f.read()
    return load_body_from_bytes(data)


def load_body_from_bytes(data: bytes) -> tuple[SaveHeader, bytes]:
    parsed: ParsedHeader = parse_header(data)
    body = decompress_all(data, parsed.body_offset)
    _validate_total_size(body)
    return parsed.header, body


def _validate_total_size(body: bytes) -> None:
    """The decompressed body's own first field is an int64 declaring the length of everything
    that follows it — a cheap, strong integrity check that decompression consumed the chunk
    stream correctly."""
    declared = struct.unpack_from("<q", body, 0)[0]
    actual = len(body) - 8
    if declared != actual:
        raise ValueError(f"decompressed body length mismatch: header says {declared}, got {actual}")
