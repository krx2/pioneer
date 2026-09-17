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
class GeneratorFuel:
    """One fuel a generator can burn, and what burning it takes and leaves besides the fuel."""

    fuel_item_id: str
    supplemental_item_id: str | None = None
    """What else the generator consumes while burning this fuel — water, for the Coal-Powered
    Generator and the Nuclear Power Plant. At what rate is the building's
    `Building.supplemental_per_minute_per_mw`."""
    byproduct_item_id: str | None = None
    """What burning it leaves behind — nuclear waste, for fuel rods."""
    byproduct_per_fuel_unit: float = 0.0
    """Units of `byproduct_item_id` per unit of fuel burned: 50 waste per Uranium Fuel Rod."""


@dataclass(frozen=True)
class Building:
    building_id: str
    name: str
    power_consumption_mw: float
    """Net power draw: positive = consumes power, negative = generates power (for generator
    buildings). A single signed field so the Verifier can sum it directly for a power balance."""
    input_slots: int
    output_slots: int
    extraction_rate_per_minute: float = 0.0
    """For an extractor: what one extracts per minute (m³ for fluids) at 100% clock speed on a
    normal-purity node — impure halves that, pure doubles it. 0 for every other building."""
    fixed_resource_id: str | None = None
    """For an extractor that can only ever extract one resource (Water Extractor, Oil Extractor):
    that resource. `None` where it depends on the node it's built on (miners, well extractors), and
    for every other building."""
    fuels: tuple[GeneratorFuel, ...] = ()
    """For a generator: every fuel it can burn. Empty for every other building, and for generators
    that burn nothing (geothermal)."""
    supplemental_per_minute_per_mw: float = 0.0
    """For a generator whose fuels need a supplemental resource: how much of it (m³ for fluids) it
    consumes per minute per MW it generates — 0.6 for the Coal-Powered Generator, whose 75 MW take
    45 m³ of water a minute. 0 for every other building."""


@dataclass(frozen=True)
class TransportTier:
    """One conveyor belt or pipeline tier and how much it carries."""

    building_id: str
    name: str
    capacity_per_minute: float
    """Items per minute for a belt, m³ per minute for a pipe."""
    carries_fluids: bool


@dataclass(frozen=True)
class ItemCount:
    """A number of items, not a rate — what a technology costs to unlock."""

    item_id: str
    amount: float


@dataclass(frozen=True)
class Technology:
    technology_id: str
    name: str
    tier: int
    prerequisites: tuple[str, ...] = ()
    kind: str = ""
    """How the player unlocks it: "milestone" (the HUB), "mam" (MAM research), "alternate" (a hard
    drive), "tutorial" (the onboarding HUB upgrades) or "custom" (granted along the way, e.g. the
    starting recipes) — the export's `mType` without its `EST_` prefix, lower-cased."""
    cost: tuple[ItemCount, ...] = ()
    """What the player hands in to unlock it."""


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
    """`Technology.technology_id` that unlocks this recipe, if any — the first of
    `unlockable_by`."""
    unlockable_by: tuple[str, ...] = ()
    """Every technology that unlocks this recipe; any one of them is enough (Silica comes with MAM
    quartz research, or with a later milestone). Empty when the data names none."""
    is_alternate: bool = False
    """One the game presents as an alternate ("Alternate: ..."), unlocked by optional research —
    mostly hard drives, a few MAM nodes. The Production Planner only falls back to alternates for
    an item with no standard recipe, unless a caller explicitly picks one: the player may not have
    researched them."""


@dataclass(frozen=True)
class ClassDescription:
    """The game's own description of one of its classes — an item, building, belt, piece of
    equipment, schematic, ... — as its UI shows it: what the Q&A Engine answers "what is X / what
    does X do" questions from."""

    class_id: str
    name: str
    text: str
    category: str
    """The export's native class for it, e.g. `FGItemDescriptor` or `FGBuildableConveyorBelt`."""
