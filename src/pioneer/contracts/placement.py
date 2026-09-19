"""Save-file building placement shape, and how the save's belts and pipes join buildings.

Produced by the Save Parser (Stage 5); consumed by the Location Advisor (Stage 9) and Anomaly
Detector (Stage 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pioneer.contracts.geometry import Coordinates

Carrier = Literal["belt", "pipe"]


@dataclass(frozen=True)
class PlacementRecord:
    building_id: str
    position: Coordinates
    recipe_id: str | None = None
    """`None` for non-production buildings (storage, power, belts, ...)."""
    clock_speed: float = 1.0
    """The building's clock speed (`mCurrentPotential`): 1.0 is 100%; power shards take it up to
    2.5, underclocking down to 0.01. What it produces and consumes scales with it."""
    fuel_item_id: str | None = None
    """What a generator is burning (`mCurrentFuelClass`). `None` for every other building, and for
    a generator that has never been fueled."""
    resource_node_id: str | None = None
    """What an extractor extracts from (`mExtractableResource`), as that object's path name —
    for miners and oil/well extractors a resource node, keyed exactly as the resource database keys
    nodes; for a Water Extractor the water volume it draws from. `None` for other buildings."""
    is_paused: bool = False
    """Put on standby by the player (`mIsProductionPaused`): makes, consumes and draws nothing."""
    production_boost: float = 1.0
    """Production amplification from Somersloops (`mCurrentProductionBoost`): 1.0 is none, 2.0 a
    fully slotted building. It multiplies what the building makes, not what it consumes, and its
    power draw by the boost squared."""
    object_id: str | None = None
    """The building's own name in the save (`Persistent_Level:PersistentLevel.Build_SmelterMk1_C_
    2147483647`): what a `TransportLink` points at. `None` for a placement not read from a save."""


@dataclass(frozen=True)
class TransportLink:
    """One way items get from one building to another: out of an output of `source_id` and into
    an input of `target_id` (both `PlacementRecord.object_id`s), along the save's belts or pipes."""

    source_id: str
    target_id: str
    carrier: Carrier
    via: tuple[str, ...] = ()
    """The belts and lifts on the way, by object id -- one route of them where splitters and
    mergers offer several. Empty for a pipe: a pipe network carries fluid either way."""
