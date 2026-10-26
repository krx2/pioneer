"""Loads items, recipes, buildings, technologies and the game's own descriptions from a
Satisfactory `Docs.json`-shaped export (the real one ships UTF-16 encoded).

Quirks of the export, handled here:

- Only factory recipes are kept: `Recipe.building_ids` keeps the `FGBuildableManufacturer*`
  buildings, dropping the build gun, Equipment Workshop and Craft Bench, and a recipe left with
  none is dropped (581 of 872).
- Raw resources are the `FGResourceDescriptor` items. Recipes can't tell them apart: the Converter
  and unpackaging produce most of them.
- Alternates are recipes named "Alternate: ..."; neither schematic type nor class name agrees.
- Slot counts aren't in the data: derived from the recipes a building runs, else `_FALLBACK_SLOTS`.
- Variable-power buildings list 0 MW: the midpoint of their estimated range is used, and the
  Geothermal Generator's constant plus factor (200 MW).
- `mSchematicDependencies` is empty for every milestone and MAM schematic, so are their
  `Technology.prerequisites`. A MAM tree is named after its research schematics' folder.
- Fluid amounts ship in litres, scaled back to m³ by the descriptor's `mForm`; without that,
  anything touching fluids is off by three orders of magnitude.
- A belt carries `mSpeed` / 2 a minute, a pipe `mFlowLimit` m³ a second; each pipe is listed twice,
  only the plain one is kept.
- A building is unlocked by whatever unlocks its build-gun recipe.
- A generator's `mSupplementalToPowerRatio` is litres per MW per second for a fluid: the
  Coal-Powered Generator's 10 makes 75 MW take 45 m³/min of water.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from pioneer.common import class_name_from_path
from pioneer.contracts import (
    Building,
    ClassDescription,
    GeneratorFuel,
    Item,
    ItemAmount,
    ItemCount,
    Recipe,
    Technology,
    TransportTier,
)
from pioneer.knowledge_base.parsing import parse_item_amounts, parse_quoted_class_list
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
_EXTRACTS_FROM = {
    "FGBuildableResourceExtractor": "node",
    "FGBuildableFrackingExtractor": "well",
    "FGBuildableWaterPump": "water",
}
_BUILD_GUN = "BP_BuildGun_C"
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
"""(input, output) slots for buildings no recipe runs in. Anything not listed (the pressurizer,
power storage) moves no items at all."""

# Schematic categories that never gate a production recipe: cosmetics and resource-sink point
# unlocks. Milestones, MAM research, alternates, the tutorial's HUB upgrades and custom (e.g. the
# starting recipes) all can.
_IRRELEVANT_SCHEMATIC_TYPES = frozenset({"EST_ResourceSink", "EST_Customization"})
_ALTERNATE_NAME_PREFIX = "Alternate:"

_BELT_NATIVE_CLASS = "FGBuildableConveyorBelt"
_PIPELINE_NATIVE_CLASS = "FGBuildablePipeline"
_ITEM_FORMS = frozenset({"RF_SOLID", "RF_LIQUID", "RF_GAS"})
"""`mForm` values of real items — building and vehicle descriptors carry `RF_INVALID`."""
_FLUID_FORMS = frozenset({"RF_LIQUID", "RF_GAS"})
_LITRES_PER_CUBIC_METRE = 1000.0
"""Fluid amounts ship in litres; every consumer of this data works in m³."""


def load_from_file(path: Path | str) -> KnowledgeBase:
    with open(path, encoding="utf-16") as f:
        raw_docs = json.load(f)
    return load_from_dict(raw_docs)


def load_from_dict(raw_docs: list[dict[str, Any]]) -> KnowledgeBase:
    entries_by_native_class = _index_by_native_class(raw_docs)

    items = _load_items(entries_by_native_class)
    fluid_item_ids = frozenset(item.item_id for item in items if item.is_fluid)
    manufacturing_building_ids = frozenset(
        entry["ClassName"]
        for native_class in _MANUFACTURING_BUILDING_NATIVE_CLASSES
        for entry in entries_by_native_class.get(native_class, [])
    )
    recipes = _load_recipes(
        entries_by_native_class.get(_RECIPE_NATIVE_CLASS, []),
        fluid_item_ids=fluid_item_ids,
        manufacturing_building_ids=manufacturing_building_ids,
    )
    buildings = _load_buildings(entries_by_native_class, recipes, fluid_item_ids)
    technologies, recipe_to_technologies = _load_technologies(
        entries_by_native_class.get(_SCHEMATIC_NATIVE_CLASS, [])
    )
    recipes = tuple(
        replace(
            recipe,
            unlocked_by=next(iter(recipe_to_technologies.get(recipe.recipe_id, ())), None),
            unlockable_by=tuple(recipe_to_technologies.get(recipe.recipe_id, ())),
        )
        for recipe in recipes
    )
    building_unlocks = _building_unlocks(
        entries_by_native_class.get(_RECIPE_NATIVE_CLASS, []), recipe_to_technologies
    )

    return KnowledgeBase(
        recipes=recipes,
        buildings=tuple(
            replace(building, unlockable_by=building_unlocks.get(building.building_id, ()))
            for building in buildings
        ),
        technologies=technologies,
        items=items,
        descriptions=_load_descriptions(entries_by_native_class),
        transport_tiers=tuple(
            replace(tier, unlockable_by=building_unlocks.get(tier.building_id, ()))
            for tier in _load_transport_tiers(entries_by_native_class)
        ),
    )


def _building_unlocks(
    recipe_entries: list[dict[str, Any]], recipe_to_technologies: dict[str, list[str]]
) -> dict[str, tuple[str, ...]]:
    """Per building, every technology unlocking the build-gun recipe that places it."""
    unlocks: dict[str, tuple[str, ...]] = {}
    for entry in recipe_entries:
        if _BUILD_GUN not in parse_quoted_class_list(entry.get("mProducedIn", "")):
            continue
        technologies = tuple(recipe_to_technologies.get(entry["ClassName"], ()))
        for descriptor, _ in parse_item_amounts(entry.get("mProduct", "")):
            if descriptor.startswith("Desc_") and technologies:
                unlocks.setdefault("Build_" + descriptor.removeprefix("Desc_"), technologies)
    return unlocks


def _load_transport_tiers(
    entries_by_native_class: dict[str, list[dict[str, Any]]],
) -> tuple[TransportTier, ...]:
    """Every belt and pipeline tier, slowest first."""
    belts = [
        TransportTier(
            building_id=entry["ClassName"],
            name=entry.get("mDisplayName") or entry["ClassName"],
            capacity_per_minute=float(entry.get("mSpeed") or 0) / 2,
            carries_fluids=False,
        )
        for entry in entries_by_native_class.get(_BELT_NATIVE_CLASS, [])
    ]
    pipes = [
        TransportTier(
            building_id=entry["ClassName"],
            name=entry.get("mDisplayName") or entry["ClassName"],
            capacity_per_minute=float(entry.get("mFlowLimit") or 0) * 60,
            carries_fluids=True,
        )
        for entry in entries_by_native_class.get(_PIPELINE_NATIVE_CLASS, [])
        if "NoIndicator" not in entry["ClassName"]
    ]
    tiers = [tier for tier in belts + pipes if tier.capacity_per_minute > 0]
    return tuple(sorted(tiers, key=lambda tier: (tier.carries_fluids, tier.capacity_per_minute)))


def _index_by_native_class(raw_docs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {class_name_from_path(entry["NativeClass"]): entry["Classes"] for entry in raw_docs}


def _load_items(entries_by_native_class: dict[str, list[dict[str, Any]]]) -> tuple[Item, ...]:
    """Every item descriptor in the export, found by its `mForm` rather than a fixed list of
    descriptor classes, of which the game adds more between versions."""
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
    """`mEnergyValue` is per unit for solids but per litre for fluids, so a fluid's scales up to
    MJ per m³: Fuel's 0.75 MJ/L is 750 MJ/m³."""
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
    """One ingredient/product, in units (or m³ for fluids) per minute. An item with no descriptor
    is treated as solid: leaving an amount alone is the harmless failure."""
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
    """Net draw, signed the way `Building.power_consumption_mw` wants it."""
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
    entries_by_native_class: dict[str, list[dict[str, Any]]],
    recipes: tuple[Recipe, ...],
    fluid_item_ids: frozenset[str],
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
            fuels = _generator_fuels(entry) if native_class in _GENERATOR_NATIVE_CLASSES else ()
            buildings.append(
                Building(
                    building_id=building_id,
                    name=entry.get("mDisplayName") or building_id,
                    power_consumption_mw=_power_consumption_mw(entry),
                    input_slots=input_slots,
                    output_slots=output_slots,
                    extraction_rate_per_minute=extraction_rate,
                    fixed_resource_id=fixed_resource_id,
                    fuels=fuels,
                    supplemental_per_minute_per_mw=_supplemental_rate(entry, fuels, fluid_item_ids),
                    extracts_from=_EXTRACTS_FROM.get(native_class),
                )
            )
    return tuple(buildings)


def _generator_fuels(entry: dict[str, Any]) -> tuple[GeneratorFuel, ...]:
    """Every fuel in a generator's `mFuel` list, with its supplemental resource and byproduct —
    the export leaves an unused one as an empty string."""
    return tuple(
        GeneratorFuel(
            fuel_item_id=class_name_from_path(fuel["mFuelClass"]),
            supplemental_item_id=_class_name_or_none(fuel.get("mSupplementalResourceClass")),
            byproduct_item_id=_class_name_or_none(fuel.get("mByproduct")),
            byproduct_per_fuel_unit=float(fuel.get("mByproductAmount") or 0),
        )
        for fuel in entry.get("mFuel") or []
        if fuel.get("mFuelClass")
    )


def _class_name_or_none(raw: str | None) -> str | None:
    return class_name_from_path(raw) if raw else None


def _supplemental_rate(
    entry: dict[str, Any], fuels: tuple[GeneratorFuel, ...], fluid_item_ids: frozenset[str]
) -> float:
    """`mSupplementalToPowerRatio` as units (m³ for fluids) per minute per MW. 0 for a generator
    none of whose fuels needs a supplemental resource."""
    supplemental = next(
        (fuel.supplemental_item_id for fuel in fuels if fuel.supplemental_item_id), None
    )
    if supplemental is None:
        return 0.0
    per_second = float(entry.get("mSupplementalToPowerRatio") or 0)
    if supplemental in fluid_item_ids:
        per_second /= _LITRES_PER_CUBIC_METRE
    return per_second * 60.0


def _extraction(entry: dict[str, Any]) -> tuple[float, str | None]:
    """An extractor's rate at 100% clock on a normal node, and the one resource it's limited to,
    if any (miners list none). `mItemsPerCycle` is in litres for fluid-only extractors."""
    cycle_seconds = float(entry.get("mExtractCycleTime") or 0)
    if cycle_seconds <= 0:
        return 0.0, None
    per_cycle = float(entry.get("mItemsPerCycle") or 0)
    if "RF_SOLID" not in (entry.get("mAllowedResourceForms") or ""):
        per_cycle /= _LITRES_PER_CUBIC_METRE
    allowed = parse_quoted_class_list(entry.get("mAllowedResources") or "")
    return per_cycle / cycle_seconds * 60.0, allowed[0] if len(allowed) == 1 else None


_RESEARCH_FOLDER = re.compile(r"/Schematics/Research/([^/]+?)(?:_RS)?/")
_TREE_NAMES = {"AlienTech": "Alien Technology", "XMas": "FICSMAS"}


def research_tree(full_name: str) -> str | None:
    """The MAM tree of the research schematic at `full_name` (its `FullName`), from its folder."""
    match = _RESEARCH_FOLDER.search(full_name)
    if match is None:
        return None
    folder = match.group(1)
    return _TREE_NAMES.get(folder) or re.sub(r"(?<=[a-z])(?=[A-Z])", " ", folder)


def _parse_prerequisites(raw_dependencies: list[dict[str, Any]] | None) -> tuple[str, ...]:
    prerequisites: list[str] = []
    for dependency in raw_dependencies or []:
        if dependency.get("Class") != "BP_SchematicPurchasedDependency_C":
            continue
        prerequisites.extend(parse_quoted_class_list(dependency.get("mSchematics", "")))
    return tuple(prerequisites)


def _load_technologies(
    entries: list[dict[str, Any]],
) -> tuple[tuple[Technology, ...], dict[str, list[str]]]:
    """Every relevant schematic, and per recipe every schematic unlocking it, in export order."""
    technologies = []
    recipe_to_technologies: dict[str, list[str]] = {}
    for entry in entries:
        if entry.get("mType") in _IRRELEVANT_SCHEMATIC_TYPES:
            continue
        technology_id = entry["ClassName"]
        kind = str(entry.get("mType") or "").removeprefix("EST_").lower()
        technologies.append(
            Technology(
                technology_id=technology_id,
                name=entry.get("mDisplayName") or technology_id,
                tier=int(float(entry.get("mTechTier") or 0)),
                prerequisites=_parse_prerequisites(entry.get("mSchematicDependencies")),
                kind=kind,
                cost=tuple(
                    ItemCount(item_id=item_id, amount=amount)
                    for item_id, amount in parse_item_amounts(entry.get("mCost") or "")
                ),
                tree=research_tree(str(entry.get("FullName") or "")) if kind == "mam" else None,
            )
        )
        for unlock in entry.get("mUnlocks") or []:
            if unlock.get("Class") != "BP_UnlockRecipe_C":
                continue
            for recipe_id in parse_quoted_class_list(unlock.get("mRecipes", "")):
                unlockers = recipe_to_technologies.setdefault(recipe_id, [])
                if technology_id not in unlockers:
                    unlockers.append(technology_id)
    return tuple(technologies), recipe_to_technologies
