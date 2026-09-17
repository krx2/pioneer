"""Location shapes: ranked build sites for new extraction, and the player's existing factories.

Produced by the Location Advisor (Stage 9); consumed by Map presentation (Stage 14).
"""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.contracts.geometry import Coordinates
from pioneer.contracts.placement import PlacementRecord
from pioneer.contracts.resources import Purity


@dataclass(frozen=True)
class RankedLocation:
    resource_node_id: str
    position: Coordinates
    purity: Purity
    distance_to_reference: float
    score: float


@dataclass(frozen=True)
class FactorySite:
    """One of the player's factories: running production buildings standing close together."""

    site_id: str
    position: Coordinates
    """The middle of its buildings."""
    placements: tuple[PlacementRecord, ...]
