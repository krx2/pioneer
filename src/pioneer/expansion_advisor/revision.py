"""What a revision changes in the player's plan: the machines to build, take down or switch over to
go from one version of a production chain to the next.

Compared stage by stage, by recipe, on the machines still to be built: more machines is an
`EXTEND`, fewer a `REMOVE`, a recipe only one version runs an `ADD` or a `REMOVE` of all of it.
An item made with another recipe in the same building is a `RECONFIGURE` of as many machines as
both have. Given what the player has built, each `REMOVE` and `RECONFIGURE` says how many of its
machines already stand (`already_built`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from pioneer.contracts import ChangeAction, ChangeItem, ProductionGraph

_EPSILON = 1e-9


@dataclass(frozen=True)
class _Stage:
    recipe_id: str
    node_id: str
    building_id: str
    item_id: str | None
    machines: float


def plan_changes(
    before: ProductionGraph,
    after: ProductionGraph,
    built: Mapping[str, float | None] | None = None,
) -> tuple[ChangeItem, ...]:
    """The changes going from plan `before` to plan `after`: in `after`'s stage order, then what
    `before` had that `after` drops altogether. Empty when the two build the same. `built` is how
    much of `before` stands, by recipe (`None` where it can't be told): with it, every `REMOVE`
    and `RECONFIGURE` says how many of its machines are built already."""
    old = _stages(before)
    new = _stages(after)
    dropped = {recipe_id: stage for recipe_id, stage in old.items() if recipe_id not in new}
    changes: list[ChangeItem] = []

    for recipe_id, stage in new.items():
        if recipe_id in old:
            delta = stage.machines - old[recipe_id].machines
            if delta > _EPSILON:
                changes.append(_change(ChangeAction.EXTEND, stage, delta))
            elif delta < -_EPSILON:
                changes.append(_change(ChangeAction.REMOVE, stage, delta))
            continue
        swapped = next(
            (
                gone
                for gone in dropped.values()
                if gone.item_id is not None and gone.item_id == stage.item_id
            ),
            None,
        )
        if swapped is None:
            changes.append(_change(ChangeAction.ADD, stage, stage.machines))
            continue
        del dropped[swapped.recipe_id]
        if swapped.building_id != stage.building_id:
            changes.append(_change(ChangeAction.REMOVE, swapped, -swapped.machines))
            changes.append(_change(ChangeAction.ADD, stage, stage.machines))
            continue
        switched = min(swapped.machines, stage.machines)
        changes.append(
            ChangeItem(
                action=ChangeAction.RECONFIGURE,
                recipe_id=recipe_id,
                additional_machine_count=switched,
                target_node_id=stage.node_id,
                from_recipe_id=swapped.recipe_id,
                building_id=stage.building_id,
            )
        )
        if stage.machines - switched > _EPSILON:
            changes.append(_change(ChangeAction.EXTEND, stage, stage.machines - switched))
        if swapped.machines - switched > _EPSILON:
            changes.append(_change(ChangeAction.REMOVE, swapped, switched - swapped.machines))

    changes.extend(_change(ChangeAction.REMOVE, gone, -gone.machines) for gone in dropped.values())
    return tuple(changes) if built is None else _with_built(changes, built, new)


def _with_built(
    changes: list[ChangeItem], built: Mapping[str, float | None], new: dict[str, _Stage]
) -> tuple[ChangeItem, ...]:
    """`changes`, each `REMOVE` and `RECONFIGURE` saying how many of its machines stand: of what's
    built of its old recipe, what the new version doesn't build of that recipe itself."""
    spare: dict[str, float | None] = {
        recipe_id: None if count is None else max(count - _to_build(new, recipe_id), 0.0)
        for recipe_id, count in built.items()
    }
    told = []
    for change in changes:
        old = _taken_from(change)
        standing = spare.get(old) if old is not None else None
        if old is None or standing is None:
            told.append(change)
            continue
        taken = min(standing, abs(change.additional_machine_count))
        spare[old] = standing - taken
        told.append(replace(change, already_built=taken))
    return tuple(told)


def _taken_from(change: ChangeItem) -> str | None:
    """The recipe whose machines `change` takes down or switches over; `None` if it builds."""
    if change.action is ChangeAction.RECONFIGURE:
        return change.from_recipe_id
    return change.recipe_id if change.action is ChangeAction.REMOVE else None


def _to_build(stages: dict[str, _Stage], recipe_id: str) -> float:
    stage = stages.get(recipe_id)
    return stage.machines if stage is not None else 0.0


def _change(action: ChangeAction, stage: _Stage, machines: float) -> ChangeItem:
    return ChangeItem(
        action=action,
        recipe_id=stage.recipe_id,
        additional_machine_count=machines,
        target_node_id=None if action is ChangeAction.ADD else stage.node_id,
        building_id=stage.building_id,
    )


def _stages(graph: ProductionGraph) -> dict[str, _Stage]:
    """Each recipe of `graph` with the machines it has still to be built, in node order -- nodes
    running the same recipe taken together, as the first of them."""
    made: dict[str, str] = {}
    for flow in graph.flows:
        if flow.source_node_id is not None:
            made.setdefault(flow.source_node_id, flow.item_id)
    stages: dict[str, _Stage] = {}
    for node in graph.nodes:
        machines = node.machine_count - node.existing_machine_count
        if machines <= _EPSILON:
            continue
        earlier = stages.get(node.recipe_id)
        if earlier is not None:
            stages[node.recipe_id] = _Stage(
                recipe_id=earlier.recipe_id,
                node_id=earlier.node_id,
                building_id=earlier.building_id,
                item_id=earlier.item_id,
                machines=earlier.machines + machines,
            )
            continue
        stages[node.recipe_id] = _Stage(
            recipe_id=node.recipe_id,
            node_id=node.node_id,
            building_id=node.building_id,
            item_id=made.get(node.node_id),
            machines=machines,
        )
    return stages
