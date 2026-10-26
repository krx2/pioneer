"""The player's plan as a conversation keeps it on disk: `contracts.Plan` to JSON and back, and
what the page shows of it (`plan_summary`, `history_summary`).

The JSON is the contracts' own shape (`pydantic.TypeAdapter`), but for the item- and recipe-keyed
pairs, which go out as JSON objects. What comes back in is validated against the contracts:
anything that doesn't fit is a `ValueError`, never a plan half made up. Fields added later
(`baseline_buildings`, `already_built`, `sites`, `reservations`) may be missing from older plans and
read back as unknown. What the player marked built is kept beside the plan, not in its JSON
(`marks_from_json`).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter

from pioneer.contracts import ChangeItem, Plan, StageProgress

_PLAN = TypeAdapter(Plan)
_CHANGE = TypeAdapter(ChangeItem)
_PAIRS = ("recipe_choices", "available_supply", "baseline_machines", "baseline_buildings")
"""The plan's fields of (key, value) pairs, which the JSON keeps as objects."""
_REQUIRED = ("targets", "graph", "recipe_choices", "extends_factory", "available_supply")
_REQUIRED += ("baseline_machines", "revision", "changes")
"""Every field a stored plan has always had: one without them is no stored plan."""


def plan_to_json(plan: Plan) -> dict[str, Any]:
    data = _PLAN.dump_python(plan, mode="json", exclude={"marked_built"})
    for key in _PAIRS:
        data[key] = dict(data[key])
    if data["sites"] is not None:
        data["sites"]["shortfall"] = dict(data["sites"]["shortfall"])
    return data


def plan_from_json(data: Any) -> Plan:
    """The plan `plan_to_json` made; `ValueError` for anything else."""
    if not isinstance(data, dict) or not all(key in data for key in _REQUIRED):
        raise ValueError("not a stored plan")
    data = {key: value for key, value in data.items() if key != "marked_built"}
    for key in _PAIRS:
        if key in data:
            data[key] = _pairs(data[key])
    if isinstance(data.get("sites"), dict) and "shortfall" in data["sites"]:
        data["sites"] = {**data["sites"], "shortfall": _pairs(data["sites"]["shortfall"])}
    return _PLAN.validate_python(data)  # a pydantic ValidationError is a ValueError


def marks_from_json(data: Any) -> tuple[tuple[str, float], ...]:
    """What the player marked built, recipe id -> machines, as `Plan.marked_built` takes it --
    anything that isn't a count left out."""
    if not isinstance(data, dict):
        return ()
    return tuple(
        (str(recipe_id), float(count))
        for recipe_id, count in data.items()
        if isinstance(count, int | float) and not isinstance(count, bool) and count > 0
    )


def plan_summary(
    plan: Plan,
    names: Mapping[str, str],
    progress: Sequence[StageProgress] | None = None,
    *,
    as_of: float | None = None,
    icons: Mapping[str, str] | None = None,
    worked_nodes: Collection[str] | None = None,
) -> dict[str, Any]:
    """What the page shows of a plan: its revision and what it makes, in the game's names, and
    where it's built -- each site with its deposits, of which as many as `worked_nodes` (nodes an
    extractor stands on in the save; `None`: unknown) have an extractor yet, and what the plan
    reserves of the factories' spare output -- and, given its `progress`, how far it's built,
    stage by stage and in all, by the save written at `as_of` (a Unix time). Totals count only the
    stages whose progress can be told: `known` says whether that's all of them."""
    summary: dict[str, Any] = {
        "revision": plan.revision,
        "targets": [
            {
                "item_id": target.item_id,
                "name": names.get(target.item_id, target.item_id),
                "rate_per_minute": target.rate_per_minute,
            }
            for target in plan.targets
        ],
    }
    where = _where(plan, names, worked_nodes)
    if where:
        summary["where"] = where
    if progress is None:
        return summary
    told = [stage for stage in progress if stage.built is not None]
    summary["progress"] = {
        "built": sum(stage.built or 0.0 for stage in told),
        "to_build": sum(stage.to_build for stage in told),
        "known": "all" if len(told) == len(progress) else "some" if told else "none",
        "as_of": as_of,
        "stages": [
            {
                "recipe_id": stage.recipe_id,
                "recipe": names.get(stage.recipe_id, stage.recipe_id),
                "building": names.get(stage.building_id, stage.building_id),
                "icon": (icons or {}).get(stage.recipe_id),
                "to_build": stage.to_build,
                "built": stage.built,
                "in_save": stage.in_save,
                "marked": stage.marked,
            }
            for stage in progress
        ],
    }
    return summary


def _where(
    plan: Plan, names: Mapping[str, str], worked_nodes: Collection[str] | None
) -> dict[str, Any] | None:
    """Where `plan` is built, as the page shows it; `None` when it keeps nothing of it."""
    if (plan.sites is None or not plan.sites.sites) and not plan.reservations:
        return None
    sites = []
    for site in plan.sites.sites if plan.sites is not None else ():
        deposits = [d for d in plan.sites.deposits if d.site_id == site.site_id]
        sites.append(
            {
                "site_id": site.site_id,
                "deposits": len(deposits),
                "with_extractor": (
                    sum(d.resource_node_id in worked_nodes for d in deposits)
                    if worked_nodes is not None
                    else None
                ),
                "mines": sorted({names.get(d.item_id, d.item_id) for d in deposits}),
                "builds": [names.get(stage.recipe_id, stage.recipe_id) for stage in site.stages],
            }
        )
    return {
        "sites": sites,
        "reserved": [
            {
                "site_id": reservation.site_id,
                "item": names.get(reservation.item_id, reservation.item_id),
                "rate_per_minute": reservation.rate_per_minute,
            }
            for reservation in plan.reservations
        ],
    }


def history_summary(entries: Any, names: Mapping[str, str]) -> list[dict[str, Any]]:
    """The revisions a conversation's plan went through, newest first, as the page shows them:
    when each was accepted, the question that asked for it, and what it changed. An entry that
    can't be read is left out."""

    def named(class_id: str | None) -> str | None:
        return names.get(class_id, class_id) if class_id is not None else None

    shown = []
    for entry in entries if isinstance(entries, list) else ():
        try:
            changes = [_CHANGE.validate_python(change) for change in entry.get("changes", [])]
            shown.append(
                {
                    "revision": int(entry["revision"]),
                    "accepted_at": _optional(entry.get("accepted_at")),
                    "question": _optional(entry.get("question")),
                    "response_id": _optional(entry.get("response_id")),
                    "changes": [
                        {
                            "action": change.action.value,
                            "recipe": named(change.recipe_id),
                            "from_recipe": named(change.from_recipe_id),
                            "building": named(change.building_id),
                            "machines": change.additional_machine_count,
                            "already_built": change.already_built,
                        }
                        for change in changes
                    ],
                }
            )
        except (KeyError, TypeError, AttributeError, ValueError):
            continue
    return sorted(shown, key=lambda entry: entry["revision"], reverse=True)


def _pairs(value: Any) -> list[tuple[Any, Any]]:
    """A JSON object's (key, value) pairs; a `ValueError` for anything else."""
    if not isinstance(value, dict):
        raise ValueError(f"expected an object, got {type(value).__name__}")
    return list(value.items())


def _optional(value: Any) -> str | None:
    return None if value is None else str(value)
