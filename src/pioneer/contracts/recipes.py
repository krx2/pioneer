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
    """Net power draw: positive = consumes power, negative = generates power (for generator
    buildings). A single signed field so the Verifier can sum it directly for a power balance."""
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
    building_ids: tuple[str, ...]
    """A recipe can be craftable in more than one building (e.g. a manual Workbench recipe that's
    also automatable in a Constructor) — every `Building.building_id` it can run in, in the
    source data's own order (first entry is the primary/automated building, by convention)."""
    inputs: tuple[ItemAmount, ...]
    outputs: tuple[ItemAmount, ...]
    unlocked_by: str | None = None
    """`Technology.technology_id` that unlocks this recipe, if any."""
