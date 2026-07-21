"""Shared coordinate shape, used by resource nodes, placements, and distance calculations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Coordinates:
    x: float
    y: float
    z: float = 0.0
