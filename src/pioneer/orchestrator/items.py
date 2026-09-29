"""Items by name: resolving what the model passes to a tool into an item id, and the
`names` map that lets it answer with in-game names instead of class ids."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from pioneer.contracts import (
    Item,
)
from pioneer.knowledge_base import (
    find_items,
    resolve_item,
)
from pioneer.orchestrator.base import OrchestratorContext, _ToolError

_GEYSER_ID = "Desc_Geyser_C"
"""The resource database's id for geysers -- made up by the community data, since no item
backs a geyser (see resource_db/loader.py)."""


def raw_resource_ids(context: OrchestratorContext) -> frozenset[str]:
    """Every raw resource the knowledge base knows — what planning stops at, and what a graph is
    allowed to take in from outside. The same set as `knowledge_base.raw_resource_ids`, read off a
    context instead of a `KnowledgeBase`."""
    return frozenset(item.item_id for item in context.items if item.is_raw_resource)


def _uncraftable_item_ids(context: OrchestratorContext, item_ids: Iterable[str]) -> frozenset[str]:
    """Of `item_ids`, those no factory recipe makes -- Wood, Mycelia, creature remains: gathered by
    hand, so like raw resources they come into the factory from outside rather than running short
    inside it."""
    craftable = {product.item_id for recipe in context.recipes for product in recipe.outputs}
    return frozenset(item_id for item_id in item_ids if item_id not in craftable)


def _resolve_item_id(raw: object, context: OrchestratorContext) -> str:
    """The item id `raw` names: the id itself, or whatever name `knowledge_base.resolve_item`
    takes as unambiguous ("Screw", "iron plates"). Anything else raises a `_ToolError` carrying
    the closest matches. Passed through unchecked when no knowledge base is loaded -- there's
    nothing to check it against."""
    query = str(raw).strip()
    if query.casefold() in ("geyser", "geysers", _GEYSER_ID.casefold()):
        return _GEYSER_ID
    if not context.items:
        return query

    item = resolve_item(context.items, query)
    if item is None and _CLASS_ID.fullmatch(query):
        # An id the model made up from the name, `Desc_ModularEngine_C` for what the export calls
        # Desc_SpaceElevatorPart_4_C: the name it was made from still says which item is meant.
        item = resolve_item(context.items, _name_in_class_id(query))
    if item is not None:
        return item.item_id
    if any(product.item_id == query for recipe in context.recipes for product in recipe.outputs):
        return query  # a real recipe product the export just has no item descriptor for
    matches = find_items(context.items, query)
    message = (
        f"{query!r} could be several items -- pass one of these by id or full name"
        if matches
        else f"unknown item {query!r}"
    )
    raise _ToolError(message, did_you_mean=[_item_summary(match) for match in matches])


def _item_summary(item: Item) -> dict[str, Any]:
    return {"item_id": item.item_id, "name": item.name, "raw_resource": item.is_raw_resource}


_CLASS_ID = re.compile(r"\b(?:Desc|Recipe|Build|BP|Schematic|Research)_[\w-]+?_C\b")
_CLASS_ID_AFFIX = re.compile(r"^(?:Desc|Recipe|Build|BP|Schematic|Research)_|_C$")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _name_in_class_id(class_id: str) -> str:
    """`Desc_ModularEngine_C` -> `Modular Engine`."""
    words = _CAMEL_BOUNDARY.sub(" ", _CLASS_ID_AFFIX.sub("", class_id)).replace("_", " ")
    return " ".join(words.split())


def display_names(context: OrchestratorContext) -> dict[str, str]:
    """The in-game name of every recipe, building, belt and pipe tier and item `context` knows,
    by id."""
    names = {recipe.recipe_id: recipe.name for recipe in context.recipes}
    names.update({building.building_id: building.name for building in context.buildings})
    names.update({item.item_id: item.name for item in context.items})
    names.update({t.technology_id: t.name for t in context.technologies})
    names.update({tier.building_id: tier.name for tier in context.transport_tiers})
    return names


def _names_mentioned(text: str, names: Mapping[str, str]) -> dict[str, str]:
    """The in-game name of every id in `text` that has one: tool results speak in ids, while the
    model answers — and its answer is checked — in the names the player knows."""
    return {class_id: names[class_id] for class_id in _CLASS_ID.findall(text) if class_id in names}
