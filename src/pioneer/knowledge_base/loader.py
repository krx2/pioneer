"""Loads recipes, buildings, and technologies from a Satisfactory `Docs.json`-shaped export.

`load_from_dict` is the pure entry point every test in `tests/knowledge_base/test_loader.py`
exercises, against `fixtures/mini_docs.json`. `load_from_file` is the thin I/O wrapper around it
for the real game export at `docs/en-US.json` (which ships UTF-16 encoded, per the game's own
tooling); `tests/knowledge_base/test_real_docs.py` runs the same kind of checks against that real
file as a bonus confidence check.

Known limitations of the source data, discovered by inspecting the real export (see
docs/implementation.md Stage 2 discussion) and encoded as deliberate choices below:

- `mProducedIn` often lists more than one building for a recipe (e.g. a manual Workbench
  recipe that's also automatable in a Constructor) -> `Recipe.building_ids` is a tuple.
- Building input/output slot counts aren't present as data anywhere in the export. They're
  derived from the max ingredient/product count across recipes that use each building; for
  buildings with no matching recipe (extractors, generators) a small documented fallback is
  used instead of inventing precision the source data doesn't have.
- `mSchematicDependencies` (technology prerequisites) is populated for alternate-recipe and
  custom schematics, but is empty for every milestone and MAM schematic in the export — the
  game encodes milestone order via `mTechTier` and MAM unlocks via in-game item scanning,
  neither of which is prerequisite data we can parse out of this file. `Technology.prerequisites`
  is therefore genuinely empty for milestones/MAM, not a parsing gap.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from pioneer.contracts import Building, ItemAmount, Recipe, Technology
from pioneer.knowledge_base.parsing import (
    class_name_from_path,
    parse_item_amounts,
    parse_quoted_class_list,
)
from pioneer.knowledge_base.queries import KnowledgeBase

_RECIPE_NATIVE_CLASS = "FGRecipe"
_SCHEMATIC_NATIVE_CLASS = "FGSchematic"
_MANUFACTURING_BUILDING_NATIVE_CLASSES = (
    "FGBuildableManufacturer",
    "FGBuildableManufacturerVariablePower",
)
_EXTRACTOR_NATIVE_CLASSES = ("FGBuildableResourceExtractor",)
_GENERATOR_NATIVE_CLASSES = (
    "FGBuildableGeneratorFuel",
    "FGBuildableGeneratorNuclear",
    "FGBuildableGeneratorGeoThermal",
)
_BUILDING_NATIVE_CLASSES = (
    _MANUFACTURING_BUILDING_NATIVE_CLASSES + _EXTRACTOR_NATIVE_CLASSES + _GENERATOR_NATIVE_CLASSES
)

# Schematic categories that never gate a production recipe: cosmetics, resource-sink point
# unlocks, and the tutorial. Milestones, MAM research, alternates, and custom (e.g. the starting
# recipes) all can.
_IRRELEVANT_SCHEMATIC_TYPES = frozenset({"EST_ResourceSink", "EST_Customization", "EST_Tutorial"})


def load_from_file(path: Path | str) -> KnowledgeBase:
    with open(path, encoding="utf-16") as f:
        raw_docs = json.load(f)
    return load_from_dict(raw_docs)


def load_from_dict(raw_docs: list[dict[str, Any]]) -> KnowledgeBase:
    entries_by_native_class = _index_by_native_class(raw_docs)

    recipes = _load_recipes(entries_by_native_class.get(_RECIPE_NATIVE_CLASS, []))
    buildings = _load_buildings(entries_by_native_class, recipes)
    technologies, recipe_to_technology = _load_technologies(
        entries_by_native_class.get(_SCHEMATIC_NATIVE_CLASS, [])
    )
    recipes = tuple(
        replace(recipe, unlocked_by=recipe_to_technology.get(recipe.recipe_id))
        for recipe in recipes
    )

    return KnowledgeBase(recipes=recipes, buildings=buildings, technologies=technologies)


def _index_by_native_class(raw_docs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {class_name_from_path(entry["NativeClass"]): entry["Classes"] for entry in raw_docs}


def _rate_per_minute(amount: float, duration_seconds: float) -> float:
    if duration_seconds <= 0:
        return 0.0
    return amount / duration_seconds * 60.0


def _load_recipes(entries: list[dict[str, Any]]) -> tuple[Recipe, ...]:
    recipes = []
    for entry in entries:
        duration = float(entry.get("mManufactoringDuration") or 0)
        inputs = tuple(
            ItemAmount(item_id=item_id, amount_per_minute=_rate_per_minute(amount, duration))
            for item_id, amount in parse_item_amounts(entry.get("mIngredients", ""))
        )
        outputs = tuple(
            ItemAmount(item_id=item_id, amount_per_minute=_rate_per_minute(amount, duration))
            for item_id, amount in parse_item_amounts(entry.get("mProduct", ""))
        )
        building_ids = parse_quoted_class_list(entry.get("mProducedIn", ""))
        recipes.append(
            Recipe(
                recipe_id=entry["ClassName"],
                name=entry.get("mDisplayName") or entry["ClassName"],
                building_ids=building_ids,
                inputs=inputs,
                outputs=outputs,
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


def _load_buildings(
    entries_by_native_class: dict[str, list[dict[str, Any]]], recipes: tuple[Recipe, ...]
) -> tuple[Building, ...]:
    buildings = []
    for native_class in _BUILDING_NATIVE_CLASSES:
        for entry in entries_by_native_class.get(native_class, []):
            building_id = entry["ClassName"]
            consumption = float(entry.get("mPowerConsumption") or 0)
            production = float(entry.get("mPowerProduction") or 0)
            input_slots, output_slots = _slots_from_recipes(building_id, recipes)
            if input_slots == 0 and output_slots == 0:
                if native_class in _EXTRACTOR_NATIVE_CLASSES:
                    input_slots, output_slots = 0, 1
                elif native_class in _GENERATOR_NATIVE_CLASSES:
                    input_slots, output_slots = 1, 0
            buildings.append(
                Building(
                    building_id=building_id,
                    name=entry.get("mDisplayName") or building_id,
                    power_consumption_mw=consumption - production,
                    input_slots=input_slots,
                    output_slots=output_slots,
                )
            )
    return tuple(buildings)


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
