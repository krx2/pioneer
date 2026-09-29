"""What a tool call is doing, in words the player can follow while they wait: the line the page
shows under an answer that's still on its way ("Planning 10/min of Reinforced Iron Plate")."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

_Arguments = Mapping[str, Any]


def _named(value: Any, names: Mapping[str, str]) -> str:
    """An item as the player knows it: its in-game name for an id, what the model wrote else."""
    text = str(value).strip()
    return names.get(text, text)


def _for(item_key: str, prefix: str) -> Callable[[_Arguments, Mapping], str]:
    """`prefix` and the item under `item_key`, or `prefix` alone without one."""

    def describe(arguments: _Arguments, names: Mapping[str, str]) -> str:
        item = arguments.get(item_key)
        return f"{prefix} {_named(item, names)}" if item else prefix

    return describe


def _rate(prefix: str) -> Callable[[_Arguments, Mapping], str]:
    def describe(arguments: _Arguments, names: Mapping[str, str]) -> str:
        item, rate = arguments.get("target_item_id"), arguments.get("target_rate_per_minute")
        if item and isinstance(rate, int | float):
            return f"{prefix} {rate:g}/min of {_named(item, names)}"
        return f"{prefix} {_named(item, names)}" if item else prefix

    return describe


def _power(arguments: _Arguments, names: Mapping[str, str]) -> str:
    target = arguments.get("target_mw")
    return (
        f"Planning {target:g} MW of power" if isinstance(target, int | float) else "Planning power"
    )


_ACTIVITIES: dict[str, Callable[[_Arguments, Mapping[str, str]], str]] = {
    "find_item": _for("query", "Looking up"),
    "list_recipes_for_item": _for("item", "Looking up recipes for"),
    "plan_production": _rate("Planning"),
    "expand_existing_factory": _rate("Planning how to extend your factory to"),
    "show_existing_factory": lambda arguments, names: "Drawing your existing factory",
    "factory_item_balance": _for("item", "Checking your factory's balance of"),
    "factory_power": lambda arguments, names: "Checking your factory's power",
    "compare_recipes": _for("item", "Comparing recipes for"),
    "plan_power": _power,
    "plan_unlocks": _for("item", "Working out what to unlock for"),
    "rank_build_locations": _for("item_id", "Ranking build sites for"),
    "diagnose_factory_problems": lambda arguments, names: "Diagnosing your factory",
    "answer_game_question": lambda arguments, names: "Searching the game's documentation",
}


def describe_tool_call(name: str, arguments: _Arguments, names: Mapping[str, str]) -> str:
    """What running tool `name` with `arguments` is doing, as one short line."""
    describe = _ACTIVITIES.get(name)
    if describe is None:
        return f"Running {name}"
    try:
        return describe(arguments, names)
    except (TypeError, ValueError):  # arguments of a shape the tool itself will reject
        return describe({}, names)
