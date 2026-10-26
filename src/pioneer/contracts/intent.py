"""Player-intent classification shape."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IntentKind(StrEnum):
    NEW_PLAN = "new_plan"
    EXPAND_EXISTING = "expand_existing"
    LOCATE_FACTORY = "locate_factory"
    DIAGNOSE_PROBLEM = "diagnose_problem"
    GENERAL_QUESTION = "general_question"


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    raw_query: str
    target_item_id: str | None = None
    target_rate_per_minute: float | None = None
