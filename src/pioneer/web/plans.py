"""The player's plan as a conversation keeps it on disk: `contracts.Plan` to JSON and back, and the
few words the page shows of it.

Mappings go out as JSON objects, in order; what comes back in is checked field by field, and
anything that doesn't fit -- a file torn or edited by hand -- is a `ValueError`, never a plan
half made up. The fields that came with tracking how far a plan is built (`baseline_buildings`,
a change's `already_built`) may be missing from a plan kept before: it reads back without them,
its progress unknown. What the player marked built isn't part of a plan's JSON: the conversation
keeps it beside the plan (see `conversations`), and `marks_from_json` reads it back.

The page is shown a plan's revision, targets and progress (`plan_summary`) and the revisions it
went through, newest first (`history_summary`), all in the game's names.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from pioneer.contracts import (
    ChangeAction,
    ChangeItem,
    MaterialFlow,
    Plan,
    PlanTarget,
    ProductionGraph,
    ProductionNode,
    StageProgress,
)


def plan_to_json(plan: Plan) -> dict[str, Any]:
    return {
        "targets": [asdict(target) for target in plan.targets],
        "graph": {
            "nodes": [asdict(node) for node in plan.graph.nodes],
            "flows": [asdict(flow) for flow in plan.graph.flows],
        },
        "recipe_choices": dict(plan.recipe_choices),
        "extends_factory": plan.extends_factory,
        "available_supply": dict(plan.available_supply),
        "baseline_machines": dict(plan.baseline_machines),
        "baseline_buildings": dict(plan.baseline_buildings),
        "revision": plan.revision,
        "changes": [{**asdict(change), "action": change.action.value} for change in plan.changes],
    }


def plan_from_json(data: Any) -> Plan:
    """The plan `plan_to_json` made; `ValueError` for anything else."""
    try:
        graph = data["graph"]
        return Plan(
            targets=tuple(
                PlanTarget(str(t["item_id"]), float(t["rate_per_minute"])) for t in data["targets"]
            ),
            graph=ProductionGraph(
                nodes=tuple(_node(node) for node in graph["nodes"]),
                flows=tuple(_flow(flow) for flow in graph["flows"]),
            ),
            recipe_choices=tuple(
                (str(item), str(recipe)) for item, recipe in _mapping(data["recipe_choices"])
            ),
            extends_factory=bool(data["extends_factory"]),
            available_supply=tuple(
                (str(item), float(rate)) for item, rate in _mapping(data["available_supply"])
            ),
            baseline_machines=tuple(
                (str(recipe), float(count)) for recipe, count in _mapping(data["baseline_machines"])
            ),
            baseline_buildings=tuple(
                (str(recipe), int(count))
                for recipe, count in _mapping(data.get("baseline_buildings", {}))
            ),
            revision=int(data["revision"]),
            changes=tuple(_change(change) for change in data["changes"]),
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"not a stored plan: {error!r}") from None


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
) -> dict[str, Any]:
    """What the page shows of a plan: its revision and what it makes, in the game's names -- and,
    given its `progress`, how far it's built, stage by stage and in all, by the save written at
    `as_of` (a Unix time). Totals count only the stages whose progress can be told: `known` says
    whether that's all of them."""
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


def history_summary(entries: Any, names: Mapping[str, str]) -> list[dict[str, Any]]:
    """The revisions a conversation's plan went through, newest first, as the page shows them:
    when each was accepted, the question that asked for it, and what it changed. An entry that
    can't be read is left out."""

    def named(class_id: str | None) -> str | None:
        return names.get(class_id, class_id) if class_id is not None else None

    shown = []
    for entry in entries if isinstance(entries, list) else ():
        try:
            changes = [_change(change) for change in entry.get("changes", [])]
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


def _mapping(value: Any) -> list[tuple[Any, Any]]:
    if not isinstance(value, dict):
        raise TypeError(f"expected an object, got {type(value).__name__}")
    return list(value.items())


def _optional(value: Any) -> str | None:
    return None if value is None else str(value)


def _node(data: Any) -> ProductionNode:
    return ProductionNode(
        node_id=str(data["node_id"]),
        recipe_id=str(data["recipe_id"]),
        building_id=str(data["building_id"]),
        machine_count=float(data["machine_count"]),
        is_existing=bool(data["is_existing"]),
        existing_machine_count=float(data["existing_machine_count"]),
        production_boost=float(data["production_boost"]),
    )


def _flow(data: Any) -> MaterialFlow:
    return MaterialFlow(
        item_id=str(data["item_id"]),
        amount_per_minute=float(data["amount_per_minute"]),
        source_node_id=_optional(data["source_node_id"]),
        target_node_id=_optional(data["target_node_id"]),
    )


def _change(data: Any) -> ChangeItem:
    return ChangeItem(
        action=ChangeAction(data["action"]),
        recipe_id=str(data["recipe_id"]),
        additional_machine_count=float(data["additional_machine_count"]),
        target_node_id=_optional(data["target_node_id"]),
        from_recipe_id=_optional(data["from_recipe_id"]),
        already_built=(
            float(data["already_built"]) if data.get("already_built") is not None else None
        ),
        building_id=_optional(data.get("building_id")),
    )
