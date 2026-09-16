"""Static game knowledge shapes: items, recipes, buildings, technologies.

Produced by the Knowledge Base module (Stage 2); consumed by the Production Planner (Stage 7),
Verifier (Stage 4), Q&A Engine (Stage 11), and the Orchestrator (Stage 16), which resolves the
item names a player uses into ids.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    item_id: str
    name: str
    """The in-game display name, e.g. "Reinforced Iron Plate" for `Desc_IronPlateReinforced_C`."""
    is_fluid: bool = False
    """Liquid or gas: amounts of it are m³ per minute rather than units per minute."""
    is_raw_resource: bool = False
    """Extracted from the map (ores, water, crude oil, nitrogen, SAM, ...) rather than crafted. The
    game does have recipes producing most of these (1.0's Converter turns one ore into another,
    unpackaging yields crude oil), so "has a recipe" can't tell them apart — this flag can. The
    Production Planner stops expanding at raw resources."""
    energy_value_mj: float = 0.0
    """Energy one unit (one m³, for fluids) releases when burned in a generator, in MJ — what turns
    a generator's output into a fuel burn rate. 0 for anything that isn't a fuel."""


@dataclass(frozen=True)
class ItemAmount:
    item_id: str
    amount_per_minute: float
    """Units per minute for solid items, m³ per minute for liquids and gases — the same numbers
    the game's own UI shows. (The `Docs.json` export stores fluids in litres; normalizing that is
    the Knowledge Base loader's job, see its module docstring.)"""


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
    """Every factory `Building.building_id` this recipe can run in, in the source data's own order
    (first entry is the primary building, by convention). Manual crafting stations — the Craft
    Bench, Equipment Workshop and build gun — aren't `Building`s, so they never appear here."""
    inputs: tuple[ItemAmount, ...]
    outputs: tuple[ItemAmount, ...]
    unlocked_by: str | None = None
    """`Technology.technology_id` that unlocks this recipe, if any."""
    is_alternate: bool = False
    """One the game presents as an alternate ("Alternate: ..."), unlocked by optional research —
    mostly hard drives, a few MAM nodes. The Production Planner only falls back to alternates for
    an item with no standard recipe, unless a caller explicitly picks one: the player may not have
    researched them."""
