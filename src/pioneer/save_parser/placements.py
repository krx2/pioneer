"""Maps the raw object table into `PlacementRecord`s: every placed building, with its class and
world position.

A "building" is any actor whose class lives under the game's own `/Game/FactoryGame/Buildable/`
asset path — the same convention the game itself uses to distinguish buildable structures from
everything else in the world (foliage, resource nodes, creatures, pickups, vehicles, ...). This
mirrors how knowledge_base's loader filters recipes/buildings by `NativeClass`, just via asset
path instead, since the object table carries no equivalent field.

`building_id` is the short class name (e.g. `Build_SmelterMk1_C`, taken from
`/Game/.../Build_SmelterMk1.Build_SmelterMk1_C`) — the same convention knowledge_base's loader
uses for `Building.building_id`, so a `PlacementRecord` here and a `Building` from the Knowledge
Base are keyed identically.

`recipe_id` is always `None` here. Determining which recipe a manufacturer is currently running
means reading its property data — see docs/implementation.md Stage 5 notes for why that's deferred
(the property blob has no per-object length markers reliable enough yet to associate a recipe with
a *specific* building with full confidence, unlike everything in `object_table.py`).
"""

from __future__ import annotations

from pioneer.contracts import PlacementRecord
from pioneer.save_parser.object_table import RawObjectHeader

_BUILDABLE_PATH_MARKER = "/Buildable/"


def is_building(header: RawObjectHeader) -> bool:
    return (
        header.is_actor
        and header.position is not None
        and _BUILDABLE_PATH_MARKER in header.class_name
    )


def _building_id(class_name: str) -> str:
    """The short class name, e.g. `Build_SmelterMk1_C` from
    `/Game/FactoryGame/Buildable/Factory/SmelterMk1/Build_SmelterMk1.Build_SmelterMk1_C`."""
    return class_name.rsplit(".", 1)[-1]


def to_placement_records(headers: tuple[RawObjectHeader, ...]) -> tuple[PlacementRecord, ...]:
    """Every placed building in `headers`, in TOC order."""
    records = []
    for header in headers:
        if not is_building(header):
            continue
        assert header.position is not None  # guaranteed by is_building, for the type checker
        records.append(
            PlacementRecord(building_id=_building_id(header.class_name), position=header.position)
        )
    return tuple(records)
