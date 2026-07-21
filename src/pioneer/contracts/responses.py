"""The response artifact shape: one assistant turn's Chat + Graph + Map payload, plus feedback.

Produced by the Orchestrator (Stage 16); consumed by all three Presentation modules (Stages
12-14) and by the Verification & Feedback module (Stage 15).
"""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.contracts.locations import RankedLocation
from pioneer.contracts.production_graph import ProductionGraph


@dataclass(frozen=True)
class Feedback:
    thumbs_up: bool | None = None
    """Chat channel: architecture.md §6 "thumbs up / down"."""
    applied_plan: bool | None = None
    """Graph channel: architecture.md §6 "did they apply the plan"."""
    built_at_location: bool | None = None
    """Map channel: architecture.md §6 "did they build there"."""
    qualitative_score: int | None = None
    """1-5, per architecture.md §6's "ocena jakościowa"/"ocena mieszana" scoring."""


@dataclass(frozen=True)
class ResponseArtifact:
    response_id: str
    chat: str | None = None
    graph: ProductionGraph | None = None
    map_locations: tuple[RankedLocation, ...] | None = None
    feedback: Feedback | None = None
