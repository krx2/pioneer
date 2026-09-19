"""Anomaly report shape.

Produced by the Anomaly Detector (Stage 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AnomalyKind(StrEnum):
    RESOURCE_DEFICIT = "resource_deficit"
    RESOURCE_SURPLUS = "resource_surplus"
    POWER_BLACKOUT = "power_blackout"
    CONGESTION = "congestion"
    MACHINE_NOT_FED = "machine_not_fed"
    """A running machine none of whose belts or pipes brings one of its ingredients."""
    OUTPUT_BLOCKED = "output_blocked"
    """A running machine whose belts or pipes take one of its products nowhere that accepts it:
    it fills up and stops."""


class AnomalySeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class AnomalyRecord:
    kind: AnomalyKind
    severity: AnomalySeverity
    description: str
    item_id: str | None = None
    node_id: str | None = None
