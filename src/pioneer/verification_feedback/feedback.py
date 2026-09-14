"""Feedback capture (implementation.md Stage 15).

Keyed to a specific `ResponseArtifact.response_id`, not a session — architecture.md §6 treats
player feedback as the ground-truth signal for a *particular* plan/graph/location, not a whole
conversation. In-memory only: swapping in real persistence is Stage 16's concern, not this
module's; the storage *shape* (one `Feedback` per response id) is what's being frozen here.
"""

from __future__ import annotations

from pioneer.contracts import Feedback


class FeedbackStore:
    def __init__(self) -> None:
        self._by_response_id: dict[str, Feedback] = {}

    def record(self, response_id: str, feedback: Feedback) -> None:
        """Overwrites any feedback already recorded for `response_id` — a player revising their
        thumbs up/down is expected to replace the old verdict, not accumulate alongside it."""
        self._by_response_id[response_id] = feedback

    def get(self, response_id: str) -> Feedback | None:
        return self._by_response_id.get(response_id)
