"""Ranked building-location recommendation shape.

Produced by the Location Advisor (Stage 9); consumed by Map presentation (Stage 14).
"""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.contracts.geometry import Coordinates
from pioneer.contracts.resources import Purity


@dataclass(frozen=True)
class RankedLocation:
    resource_node_id: str
    position: Coordinates
    purity: Purity
    distance_to_reference: float
    score: float
