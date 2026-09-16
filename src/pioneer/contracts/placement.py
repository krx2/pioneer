"""Save-file building placement shape.

Produced by the Save Parser (Stage 5); consumed by the Location Advisor (Stage 9) and Anomaly
Detector (Stage 10).
"""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.contracts.geometry import Coordinates


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
