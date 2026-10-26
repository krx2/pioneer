"""Live game state from the Dedicated Server API (`QueryServerState`), distinct from the `.sav`
snapshot. The API has no progress percentage; `TechTier` is the closest signal it exposes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GameState:
    phase: str
    """Current game phase (`GamePhase` from the API) — free-form, since the game's own phase names
    change across updates, so this is intentionally not a fixed enum. `"None"` when no session is
    loaded."""
    tech_tier: int
    """Highest tech tier of all currently-unlocked schematics (`TechTier`)."""
    session_name: str | None = None
