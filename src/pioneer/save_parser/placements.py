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

`recipe_id`, `clock_speed` and `fuel_item_id` are filled in by `to_placement_records_with_recipes`,
which reads each building's own `mCurrentRecipe`, `mCurrentPotential` and `mCurrentFuelClass` out
of its entity span (see entities.py for the framing and properties.py for the extraction).
`to_placement_records` is the headers-only version, for callers that have no decompressed body to
search — it leaves `recipe_id` as `None`, which is a real "not known", not "not running a
recipe", and the clock speed at its 100% default.
"""

from __future__ import annotations

from collections.abc import Sequence

from pioneer.contracts import PlacementRecord
from pioneer.save_parser.entities import EntitySpan
from pioneer.save_parser.object_table import RawObjectHeader
from pioneer.save_parser.properties import find_recipe_ids, read_float_property

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
    """Every placed building in `headers`, in TOC order, with `recipe_id` left unknown (`None`)."""
    records = []
    for header in headers:
        if not is_building(header):
            continue
        assert header.position is not None  # guaranteed by is_building, for the type checker
        records.append(
            PlacementRecord(building_id=_building_id(header.class_name), position=header.position)
        )
    return tuple(records)


def to_placement_records_with_recipes(
    headers: tuple[RawObjectHeader, ...],
    body: bytes,
    spans: Sequence[EntitySpan],
) -> tuple[PlacementRecord, ...]:
    """Every placed building in `headers`, in TOC order, with `recipe_id`, `clock_speed` and (for
    generators) `fuel_item_id` read from each one's own entity span. `spans[i]` must be the span
    for `headers[i]` — the pairing `entities.find_entity_spans` guarantees.

    A building with no `mCurrentRecipe` keeps `recipe_id=None`: most buildings genuinely have no
    recipe (belts, storage, poles, miners), and a manufacturer the player never configured hasn't
    got one either. Where a span somehow holds more than one, the first is used — no such case
    occurs in either real fixture save. No `mCurrentPotential` means the default 100% clock (the
    game doesn't save defaults), and `mCurrentFuelClass` is an object reference shaped exactly like
    `mCurrentRecipe`, so the same reader resolves it to the fuel's item id.
    """
    if len(spans) != len(headers):
        raise ValueError(
            f"got {len(spans)} entity spans for {len(headers)} object headers -- they must pair up"
        )

    records = []
    for header, span in zip(headers, spans, strict=True):
        if not is_building(header):
            continue
        assert header.position is not None  # guaranteed by is_building, for the type checker
        recipe_ids = find_recipe_ids(body, start=span.start, end=span.end)
        fuel_ids = find_recipe_ids(
            body, start=span.start, end=span.end, property_name="mCurrentFuelClass"
        )
        clock_speed = read_float_property(body, "mCurrentPotential", start=span.start, end=span.end)
        records.append(
            PlacementRecord(
                building_id=_building_id(header.class_name),
                position=header.position,
                recipe_id=recipe_ids[0] if recipe_ids else None,
                clock_speed=1.0 if clock_speed is None else clock_speed,
                fuel_item_id=fuel_ids[0] if fuel_ids else None,
            )
        )
    return tuple(records)
