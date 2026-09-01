"""Live game state shape, distinct from the `.sav` snapshot.

Produced by the Dedicated Server API Client module (Stage 6).

Evolved from an originally-planned `progress_percent: float` once Stage 6 checked the real
Dedicated Server API (`QueryServerState`, documented at
`CommunityResources/DedicatedServerAPIDocs.md` in the game's own install) and found it has no
literal progress percentage — `TechTier` (highest tech tier of all currently-unlocked schematics)
is the closest real signal the API exposes, so that's what this contract carries instead. Per
implementation.md's own methodology, contracts are allowed to evolve when a stage discovers the
original shape doesn't match reality.
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
