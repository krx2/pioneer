"""Live game state shape, distinct from the `.sav` snapshot.

Produced by the Dedicated Server API Client module (Stage 6).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GameState:
    phase: str
    """Current tech/space-elevator phase, as reported by the server (free-form: the game's own
    phase names change across updates, so this is intentionally not a fixed enum)."""
    progress_percent: float
    session_name: str | None = None
