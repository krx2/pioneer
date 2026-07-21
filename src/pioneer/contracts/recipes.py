"""Static game knowledge shapes: recipes, buildings, technologies.

Produced by the Knowledge Base module (Stage 2); consumed by the Production Planner (Stage 7),
Verifier (Stage 4), and Q&A Engine (Stage 11).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ItemAmount:
    item_id: str
    amount_per_minute: float


@dataclass(frozen=True)
class Building:
    building_id: str
    name: str
    power_consumption_mw: float
    input_slots: int
    output_slots: int


@dataclass(frozen=True)
class Technology:
    technology_id: str
    name: str
    tier: int
    prerequisites: tuple[str, ...] = ()


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    name: str
    building_id: str
    inputs: tuple[ItemAmount, ...]
    outputs: tuple[ItemAmount, ...]
    unlocked_by: str | None = None
    """`Technology.technology_id` that unlocks this recipe, if any."""
