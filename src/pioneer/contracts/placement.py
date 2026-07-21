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
