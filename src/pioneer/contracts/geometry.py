"""Shared coordinate shape, used by resource nodes, placements, and distance calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Coordinates:
    x: float
    y: float
    z: float = 0.0

    def distance_to(self, other: Coordinates) -> float:
        """Straight-line distance, in the coordinates' own units (centimetres, in a save)."""
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))
