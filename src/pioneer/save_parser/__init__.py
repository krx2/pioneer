"""Save file (.sav) parser, read-only.

`load_save_state` is the one-call entry point: a save in, its `PlacementRecord`s, a
`ProductionGraph` of the existing factory and the belts' and pipes' `TransportLink`s out. The
steps underneath are usable on their own: `load_body_from_file` decompresses the body,
`find_object_table` reads the TOC, `find_entity_spans` frames each object's properties,
`find_recipe_ids` pulls out `mCurrentRecipe`, and `to_placement_records*` / `to_production_graph`
map the result into contracts. `discover_saves` lists saves by their headers alone.
"""

from pioneer.save_parser.catalog import (
    SaveFileInfo,
    SaveSource,
    discover_saves,
    read_save_header,
    save_source,
    saves_in,
)
from pioneer.save_parser.connections import Port, read_ports, trace_links
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
    "Port",
    "PropertyTag",
    "RawObjectHeader",
    "SaveFileInfo",
    "SaveHeader",
    "SaveSource",
    "SaveState",
    "discover_saves",
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
    "read_ports",
    "read_property_tag",
    "read_save_header",
    "save_source",
    "saves_in",
    "to_placement_records",
    "to_placement_records_with_recipes",
    "to_production_graph",
    "trace_links",
]
