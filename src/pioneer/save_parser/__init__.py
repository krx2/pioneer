"""Save file (.sav) parser module (implementation.md Stage 5).

Reads a Satisfactory save file into `ProductionGraph` + `PlacementRecord` shapes. Read-only —
never writes to the save file. Depends only on `pioneer.contracts`; tested against fixture save
files, resolving recipe IDs against a fixture recipe list rather than a live Knowledge Base call.

Work in progress: header parsing, chunk decompression, the object table (TOC), and
`PlacementRecord` mapping are done and verified against real save data.
`load_body_from_file`/`load_body_from_bytes` get you the fully decompressed level-data body;
`find_and_read_object_table` walks that body's object list into `RawObjectHeader` tuples (class
name, path, world position); `to_placement_records` filters and maps those into `PlacementRecord`.
`ProductionGraph` construction (recipe extraction from object property data, needed for
`ProductionNode.recipe_id`) isn't built yet — see placements.py's module docstring for why.
"""

from pioneer.save_parser.header import SaveHeader
from pioneer.save_parser.loader import load_body_from_bytes, load_body_from_file
from pioneer.save_parser.object_table import RawObjectHeader, find_and_read_object_table
from pioneer.save_parser.placements import to_placement_records

__all__ = [
    "RawObjectHeader",
    "SaveHeader",
    "find_and_read_object_table",
    "load_body_from_bytes",
    "load_body_from_file",
    "to_placement_records",
]
