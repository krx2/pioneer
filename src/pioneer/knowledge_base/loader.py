"""Loads items, recipes, buildings, technologies and the game's own descriptions from a
Satisfactory `Docs.json`-shaped export.

`load_from_dict` is the pure entry point every test in `tests/knowledge_base/test_loader.py`
exercises, against `fixtures/mini_docs.json`. `load_from_file` is the thin I/O wrapper around it
for the real game export at `docs/en-US.json` (which ships UTF-16 encoded, per the game's own
tooling); `tests/knowledge_base/test_real_docs.py` runs the same kind of checks against that real
file as a bonus confidence check.

Known limitations of the source data, discovered by inspecting the real export (see
docs/implementation.md Stage 2 discussion) and encoded as deliberate choices below:

- **Only factory recipes are kept.** Two thirds of the export's recipes (581 of 872) run only in
  the build gun (the cost of placing a building), the Equipment Workshop (gear) or the Craft
  Bench — manual stations, none of which is a `Building` this project can plan, place or count.
  `mProducedIn` also lists those stations next to the real machine for many factory recipes (Iron
  Plate: Constructor *and* Craft Bench), so `Recipe.building_ids` keeps only the
  `FGBuildableManufacturer*` buildings a recipe runs in, and a recipe left with none is dropped.
- **Raw resources** are the `FGResourceDescriptor` items (ores, water, crude oil, nitrogen gas,
  SAM, ...), flagged on `Item.is_raw_resource`. Recipes alone can't identify them: 1.0's Converter
  turns ores into other ores and unpackaging yields crude oil, so most raw resources *have* a
  recipe producing them — a planner walking recipes backwards would never stop.
- **Alternates** are recipes whose display name the game prefixes with "Alternate:" — exactly
  the ones it presents to the player as alternates. Neither structural signal agrees with that:
  `EST_Alternate` (hard-drive) schematics also unlock the standard Turbofuel packaging recipes,
  some alternates (Compacted Coal, Polyester Fabric) come from MAM research instead, and class
  names are unreliable in both directions (`Recipe_Alternate_Turbofuel_C` is the plain,
  MAM-unlocked "Turbofuel"; `Recipe_PureAluminumIngot_C` is "Alternate: Pure Aluminum Ingot").
- Building input/output slot counts aren't present as data anywhere in the export. They're
  derived from the max ingredient/product count across recipes that use each building; for
  buildings with no matching recipe (extractors, generators, ...) a small documented fallback is
  used instead of inventing precision the source data doesn't have.
- **Variable-power buildings have no fixed rating.** The Particle Accelerator, Converter and
  Quantum Encoder list `mPowerConsumption` 0 because their draw cycles between
  `mEstimatedMininumPowerConsumption` (sic) and `mEstimatedMaximumPowerConsumption`; the midpoint
  is used. The Geothermal Generator likewise lists `mPowerProduction` 0 — its output cycles too,
  and `mVariablePowerProductionConstant + mVariablePowerProductionFactor` comes to 200 MW, the
  game's stated average on a normal geyser, which is what's used.
- `mSchematicDependencies` (technology prerequisites) is populated for alternate-recipe and
  custom schematics, but is empty for every milestone and MAM schematic in the export — the
  game encodes milestone order via `mTechTier` and MAM unlocks via in-game item scanning,
  neither of which is prerequisite data we can parse out of this file. `Technology.prerequisites`
  is therefore genuinely empty for milestones/MAM, not a parsing gap.
- **Fluid amounts are stored scaled by 1000.** The export writes liquid and gas quantities in
  litres while the game's own UI (and every other tool) talks in m³ — `Recipe_LiquidFuel_C` lists
  its crude oil ingredient as `60000`, meaning 60 m³/min. Solids are unscaled. Which items are
  fluids comes from the item descriptors' `mForm` field (`RF_LIQUID`/`RF_GAS` vs `RF_SOLID`), so
  `_load_items` indexes every descriptor in the export and `_item_amount` divides fluid amounts
  back down. Without this, anything touching oil, water, gas or their derivatives is off by three
  orders of magnitude — machine counts, balance, power, anomaly severities.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from pioneer.contracts import Building, ClassDescription, Item, ItemAmount, Recipe, Technology
from pioneer.knowledge_base.parsing import (
    class_name_from_path,
    parse_item_amounts,
    parse_quoted_class_list,
)
from pioneer.knowledge_base.queries import KnowledgeBase

_RECIPE_NATIVE_CLASS = "FGRecipe"
_SCHEMATIC_NATIVE_CLASS = "FGSchematic"
_RAW_RESOURCE_NATIVE_CLASS = "FGResourceDescriptor"
_MANUFACTURING_BUILDING_NATIVE_CLASSES = (
    "FGBuildableManufacturer",
    "FGBuildableManufacturerVariablePower",
)
_EXTRACTOR_NATIVE_CLASSES = (
    "FGBuildableResourceExtractor",
    "FGBuildableWaterPump",
    "FGBuildableFrackingExtractor",
)
_GENERATOR_NATIVE_CLASSES = (
    "FGBuildableGeneratorFuel",
    "FGBuildableGeneratorNuclear",
    "FGBuildableGeneratorGeoThermal",
)
_OTHER_POWERED_NATIVE_CLASSES = (
    "FGBuildableFrackingActivator",  # the Resource Well Pressurizer
    "FGBuildablePowerStorage",
    "FGBuildableResourceSink",
)
_BUILDING_NATIVE_CLASSES = (
    _MANUFACTURING_BUILDING_NATIVE_CLASSES
    + _EXTRACTOR_NATIVE_CLASSES
    + _GENERATOR_NATIVE_CLASSES
    + _OTHER_POWERED_NATIVE_CLASSES
)
_FALLBACK_SLOTS: dict[str, tuple[int, int]] = {
    **dict.fromkeys(_EXTRACTOR_NATIVE_CLASSES, (0, 1)),
    **dict.fromkeys(_GENERATOR_NATIVE_CLASSES, (1, 0)),
    "FGBuildableResourceSink": (1, 0),
}
"""(input, output) slots for buildings no recipe runs in — see module docstring. Anything not
listed (the pressurizer, power storage) moves no items at all."""

# Schematic categories that never gate a production recipe: cosmetics, resource-sink point
# unlocks, and the tutorial. Milestones, MAM research, alternates, and custom (e.g. the starting
# recipes) all can.
_IRRELEVANT_SCHEMATIC_TYPES = frozenset({"EST_ResourceSink", "EST_Customization", "EST_Tutorial"})
_ALTERNATE_NAME_PREFIX = "Alternate:"

_ITEM_FORMS = frozenset({"RF_SOLID", "RF_LIQUID", "RF_GAS"})
"""`mForm` values of real items — building and vehicle descriptors carry `RF_INVALID`."""
_FLUID_FORMS = frozenset({"RF_LIQUID", "RF_GAS"})
_LITRES_PER_CUBIC_METRE = 1000.0
"""Fluid amounts ship in litres; every consumer of this data works in m³ — see module docstring."""


def load_from_file(path: Path | str) -> KnowledgeBase:
    with open(path, encoding="utf-16") as f:
        raw_docs = json.load(f)
    return load_from_dict(raw_docs)


def load_from_dict(raw_docs: list[dict[str, Any]]) -> KnowledgeBase:
    entries_by_native_class = _index_by_native_class(raw_docs)

    items = _load_items(entries_by_native_class)
    manufacturing_building_ids = frozenset(
        entry["ClassName"]
        for native_class in _MANUFACTURING_BUILDING_NATIVE_CLASSES
        for entry in entries_by_native_class.get(native_class, [])
    )
    recipes = _load_recipes(
        entries_by_native_class.get(_RECIPE_NATIVE_CLASS, []),
        fluid_item_ids=frozenset(item.item_id for item in items if item.is_fluid),
        manufacturing_building_ids=manufacturing_building_ids,
    )
    buildings = _load_buildings(entries_by_native_class, recipes)
    technologies, recipe_to_technology = _load_technologies(
        entries_by_native_class.get(_SCHEMATIC_NATIVE_CLASS, [])
    )
    recipes = tuple(
        replace(recipe, unlocked_by=recipe_to_technology.get(recipe.recipe_id))
        for recipe in recipes
    )

    return KnowledgeBase(
        recipes=recipes,
        buildings=buildings,
        technologies=technologies,
        items=items,
        descriptions=_load_descriptions(entries_by_native_class),
    )


def _index_by_native_class(raw_docs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {class_name_from_path(entry["NativeClass"]): entry["Classes"] for entry in raw_docs}


def _load_items(entries_by_native_class: dict[str, list[dict[str, Any]]]) -> tuple[Item, ...]:
    """Every item descriptor in the export, across all of its descriptor native classes.

    Scanned by `mForm` rather than from a fixed list of descriptor native classes
    (`FGItemDescriptor`, `FGResourceDescriptor`, `FGItemDescriptorBiomass`, ...) — there are a
    dozen-odd of them and the game adds more between versions, but every real item carries a
    solid/liquid/gas form.
    """
    return tuple(
        Item(
            item_id=entry["ClassName"],
            name=entry.get("mDisplayName") or entry["ClassName"],
            is_fluid=entry["mForm"] in _FLUID_FORMS,
            is_raw_resource=native_class == _RAW_RESOURCE_NATIVE_CLASS,
            energy_value_mj=_energy_value_mj(entry),
        )
        for native_class, entries in entries_by_native_class.items()
        for entry in entries
        if entry.get("mForm") in _ITEM_FORMS and "ClassName" in entry
    )


def _energy_value_mj(entry: dict[str, Any]) -> float:
    """`mEnergyValue` is per unit for solids but per *litre* for fluids — the same litres-vs-m³
    split as recipe amounts (see module docstring) — so a fluid's scales up to MJ per m³: Fuel's
    0.75 MJ/L is 750 MJ/m³, which a 250 MW Fuel Generator burns at 20 m³/min, as in game."""
    energy = float(entry.get("mEnergyValue") or 0)
    return energy * _LITRES_PER_CUBIC_METRE if entry["mForm"] in _FLUID_FORMS else energy


def _load_descriptions(
    entries_by_native_class: dict[str, list[dict[str, Any]]],
) -> tuple[ClassDescription, ...]:
    """Every class the export describes in words, whatever its kind — the text the Q&A Engine
    answers from."""
    return tuple(
        ClassDescription(
            class_id=entry["ClassName"],
            name=_flatten(entry["mDisplayName"]),
            text=_flatten(entry["mDescription"]),
            category=native_class,
        )
        for native_class, entries in entries_by_native_class.items()
        for entry in entries
        if entry.get("mDescription") and entry.get("mDisplayName") and "ClassName" in entry
    )


def _flatten(ui_text: str) -> str:
    """UI strings with their line breaks and (narrow) non-breaking spaces made plain spaces."""
    return " ".join(ui_text.replace("\u202f", " ").replace("\xa0", " ").split())


def _rate_per_minute(amount: float, duration_seconds: float) -> float:
    if duration_seconds <= 0:
        return 0.0
    return amount / duration_seconds * 60.0


def _item_amount(
    item_id: str, amount: float, duration: float, fluid_item_ids: frozenset[str]
) -> ItemAmount:
    """One ingredient/product, in units (or m³ for fluids) per minute.

    An item with no descriptor in the export is treated as solid: an unknown item is far likelier
    to be a descriptor type this parser hasn't seen than a secret fluid, and leaving an amount
    alone is the harmless failure — scaling one that shouldn't be is not.
    """
    if item_id in fluid_item_ids:
        amount /= _LITRES_PER_CUBIC_METRE
    return ItemAmount(item_id=item_id, amount_per_minute=_rate_per_minute(amount, duration))


def _load_recipes(
    entries: list[dict[str, Any]],
    *,
    fluid_item_ids: frozenset[str],
    manufacturing_building_ids: frozenset[str],
) -> tuple[Recipe, ...]:
    recipes = []
    for entry in entries:
        building_ids = tuple(
            building_id
            for building_id in parse_quoted_class_list(entry.get("mProducedIn", ""))
            if building_id in manufacturing_building_ids
        )
        if not building_ids:
            continue  # build gun / Workshop / Craft Bench only -- see module docstring

        duration = float(entry.get("mManufactoringDuration") or 0)
        inputs = tuple(
            _item_amount(item_id, amount, duration, fluid_item_ids)
            for item_id, amount in parse_item_amounts(entry.get("mIngredients", ""))
        )
        outputs = tuple(
            _item_amount(item_id, amount, duration, fluid_item_ids)
            for item_id, amount in parse_item_amounts(entry.get("mProduct", ""))
        )
        name = entry.get("mDisplayName") or entry["ClassName"]
        recipes.append(
            Recipe(
                recipe_id=entry["ClassName"],
                name=name,
                building_ids=building_ids,
                inputs=inputs,
                outputs=outputs,
                is_alternate=name.startswith(_ALTERNATE_NAME_PREFIX),
            )
        )
    return tuple(recipes)


def _slots_from_recipes(building_id: str, recipes: tuple[Recipe, ...]) -> tuple[int, int]:
    matching = [r for r in recipes if building_id in r.building_ids]
    if not matching:
        return (0, 0)
    return (
        max(len(r.inputs) for r in matching),
        max(len(r.outputs) for r in matching),
    )


def _power_consumption_mw(entry: dict[str, Any]) -> float:
    """Net draw, signed the way `Building.power_consumption_mw` wants it. See the module docstring
    for the variable-power buildings, whose fixed ratings read 0."""
    consumption = float(entry.get("mPowerConsumption") or 0)
    if consumption == 0 and "mEstimatedMaximumPowerConsumption" in entry:
        low = float(entry.get("mEstimatedMininumPowerConsumption") or 0)
        high = float(entry.get("mEstimatedMaximumPowerConsumption") or 0)
        consumption = (low + high) / 2

    production = float(entry.get("mPowerProduction") or 0)
    if production == 0 and "mVariablePowerProductionFactor" in entry:
        production = float(entry.get("mVariablePowerProductionConstant") or 0) + float(
            entry.get("mVariablePowerProductionFactor") or 0
        )
    return consumption - production


def _load_buildings(
    entries_by_native_class: dict[str, list[dict[str, Any]]], recipes: tuple[Recipe, ...]
) -> tuple[Building, ...]:
    buildings = []
    for native_class in _BUILDING_NATIVE_CLASSES:
        for entry in entries_by_native_class.get(native_class, []):
            building_id = entry["ClassName"]
            input_slots, output_slots = _slots_from_recipes(building_id, recipes)
            if input_slots == 0 and output_slots == 0:
                input_slots, output_slots = _FALLBACK_SLOTS.get(native_class, (0, 0))
            extraction_rate, fixed_resource_id = (
                _extraction(entry) if native_class in _EXTRACTOR_NATIVE_CLASSES else (0.0, None)
            )
            buildings.append(
                Building(
                    building_id=building_id,
                    name=entry.get("mDisplayName") or building_id,
                    power_consumption_mw=_power_consumption_mw(entry),
                    input_slots=input_slots,
                    output_slots=output_slots,
                    extraction_rate_per_minute=extraction_rate,
                    fixed_resource_id=fixed_resource_id,
                )
            )
    return tuple(buildings)


def _extraction(entry: dict[str, Any]) -> tuple[float, str | None]:
    """An extractor's rate at 100% clock on a normal node, and the one resource it's limited to,
    if any (`mAllowedResources` lists exactly one; miners list none, meaning "whatever the node
    has"). `mItemsPerCycle` is in litres for extractors that allow no solid form — the same
    litres-vs-m³ split as recipe amounts — so a Water Extractor's 2000 per second is 120 m³/min."""
    cycle_seconds = float(entry.get("mExtractCycleTime") or 0)
    if cycle_seconds <= 0:
        return 0.0, None
    per_cycle = float(entry.get("mItemsPerCycle") or 0)
    if "RF_SOLID" not in (entry.get("mAllowedResourceForms") or ""):
        per_cycle /= _LITRES_PER_CUBIC_METRE
    allowed = parse_quoted_class_list(entry.get("mAllowedResources") or "")
    return per_cycle / cycle_seconds * 60.0, allowed[0] if len(allowed) == 1 else None


def _parse_prerequisites(raw_dependencies: list[dict[str, Any]] | None) -> tuple[str, ...]:
    prerequisites: list[str] = []
    for dependency in raw_dependencies or []:
        if dependency.get("Class") != "BP_SchematicPurchasedDependency_C":
            continue
        prerequisites.extend(parse_quoted_class_list(dependency.get("mSchematics", "")))
    return tuple(prerequisites)


def _load_technologies(
    entries: list[dict[str, Any]],
) -> tuple[tuple[Technology, ...], dict[str, str]]:
    technologies = []
    recipe_to_technology: dict[str, str] = {}
    for entry in entries:
        if entry.get("mType") in _IRRELEVANT_SCHEMATIC_TYPES:
            continue
        technology_id = entry["ClassName"]
        technologies.append(
            Technology(
                technology_id=technology_id,
                name=entry.get("mDisplayName") or technology_id,
                tier=int(float(entry.get("mTechTier") or 0)),
                prerequisites=_parse_prerequisites(entry.get("mSchematicDependencies")),
            )
        )
        for unlock in entry.get("mUnlocks") or []:
            if unlock.get("Class") != "BP_UnlockRecipe_C":
                continue
            for recipe_id in parse_quoted_class_list(unlock.get("mRecipes", "")):
                recipe_to_technology.setdefault(recipe_id, technology_id)
    return tuple(technologies), recipe_to_technology
