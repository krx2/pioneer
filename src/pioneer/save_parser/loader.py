"""Top-level entry points: raw decompression, and the whole save-to-contracts pipeline.

`load_body_from_file` / `load_body_from_bytes` return the fully decompressed level-data body.
`load_save_state` goes the rest of the way — object table, entity framing, recipe attribution —
and hands back the Stage 5 contracts (`PlacementRecord`s + a `ProductionGraph`) that the rest of
the project consumes. See the module docstrings in `header.py`, `chunks.py`, `object_table.py`,
`entities.py` and `properties.py` for what each step is verified against.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from pioneer.contracts import PlacementRecord, ProductionGraph
from pioneer.save_parser.chunks import decompress_all
from pioneer.save_parser.entities import find_entity_spans
from pioneer.save_parser.header import ParsedHeader, SaveHeader, parse_header
from pioneer.save_parser.object_table import find_object_table
from pioneer.save_parser.placements import to_placement_records_with_recipes
from pioneer.save_parser.production_graph import to_production_graph


def load_body_from_file(path: Path | str) -> tuple[SaveHeader, bytes]:
    with open(path, "rb") as f:
        data = f.read()
    return load_body_from_bytes(data)


def load_body_from_bytes(data: bytes) -> tuple[SaveHeader, bytes]:
    parsed: ParsedHeader = parse_header(data)
    body = decompress_all(data, parsed.body_offset)
    _validate_total_size(body)
    return parsed.header, body


def find_latest_save(directory: Path | str) -> Path | None:
    """The most recently modified `.sav` under `directory`, searched recursively — `None` if the
    directory doesn't exist or holds no saves.

    Modification time, not filename, decides: the game rotates autosaves through numbered names
    (`<session>_autosave_0.sav`, `_1`, `_2`), so the highest number is not the newest file. In
    practice the winner is usually the latest autosave, which is exactly the freshest snapshot of
    the factory available — bearing in mind architecture.md §2's caveat that a save is a snapshot
    taken at write time, not live state.
    """
    root = Path(directory)
    if not root.is_dir():
        return None
    saves = [path for path in root.rglob("*.sav") if path.is_file()]
    if not saves:
        return None
    return max(saves, key=lambda path: path.stat().st_mtime)


@dataclass(frozen=True)
class SaveState:
    """One save file, in this project's own contract shapes — the Stage 5 deliverable."""

    header: SaveHeader
    placements: tuple[PlacementRecord, ...]
    graph: ProductionGraph
    """What the player has already built, one node per recipe in use. `flows` is empty — see
    production_graph.py for why."""


def load_save_state(path: Path | str) -> SaveState:
    header, body = load_body_from_file(path)
    table = find_object_table(body)
    spans = find_entity_spans(body, table)
    placements = to_placement_records_with_recipes(table.headers, body, spans)
    return SaveState(header=header, placements=placements, graph=to_production_graph(placements))


def _validate_total_size(body: bytes) -> None:
    """The decompressed body's own first field is an int64 declaring the length of everything
    that follows it — a cheap, strong integrity check that decompression consumed the chunk
    stream correctly."""
    declared = struct.unpack_from("<q", body, 0)[0]
    actual = len(body) - 8
    if declared != actual:
        raise ValueError(f"decompressed body length mismatch: header says {declared}, got {actual}")
