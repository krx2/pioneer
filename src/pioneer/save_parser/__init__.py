"""Save file (.sav) parser module (implementation.md Stage 5).

Reads a Satisfactory save file into `ProductionGraph` + `PlacementRecord` shapes. Read-only —
never writes to the save file. Depends only on `pioneer.contracts`; tested against fixture save
files, resolving recipe IDs against a fixture recipe list rather than a live Knowledge Base call.

`load_save_state` is the one-call entry point: save file in, `PlacementRecord`s (each with the
recipe its building is running) and a `ProductionGraph` of the existing factory out. The steps
underneath are usable on their own — `load_body_from_file` decompresses the level-data body,
`find_object_table` reads the TOC into `RawObjectHeader`s, `find_entity_spans` frames each object's
property blob, `find_recipe_ids` pulls `mCurrentRecipe` out of one of those spans, and
`to_placement_records*` / `to_production_graph` map the result into Stage 1 contracts.

Every step is verified against the two real saves committed under tests/save_parser/fixtures/:
entity framing lands byte-exactly on the section's declared end, and all 500 / 906 recipes in them
resolve to real Knowledge Base recipe ids, each attributed to exactly one manufacturing building.
The graph's `flows` are empty (see production_graph.py) — routing isn't recoverable from what this
parser reads.
"""

from pioneer.save_parser.entities import EntitySpan, find_entity_spans
from pioneer.save_parser.header import SaveHeader
from pioneer.save_parser.loader import (
    SaveState,
    find_latest_save,
    load_body_from_bytes,
    load_body_from_file,
    load_save_state,
)
from pioneer.save_parser.object_table import (
    ObjectTable,
    RawObjectHeader,
    find_and_read_object_table,
    find_object_table,
)
from pioneer.save_parser.placements import (
    to_placement_records,
    to_placement_records_with_recipes,
)
from pioneer.save_parser.production_graph import to_production_graph
from pioneer.save_parser.properties import (
    PropertyTag,
    find_recipe_ids,
    find_recipe_paths,
    read_object_reference_value,
    read_property_tag,
)

__all__ = [
    "EntitySpan",
    "ObjectTable",
    "PropertyTag",
    "RawObjectHeader",
    "SaveHeader",
    "SaveState",
    "find_and_read_object_table",
    "find_entity_spans",
    "find_latest_save",
    "find_object_table",
    "find_recipe_ids",
    "find_recipe_paths",
    "load_body_from_bytes",
    "load_body_from_file",
    "load_save_state",
    "read_object_reference_value",
    "read_property_tag",
    "to_placement_records",
    "to_placement_records_with_recipes",
    "to_production_graph",
]
