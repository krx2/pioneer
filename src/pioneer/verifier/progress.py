"""How far the player has built their plan, by the save: what stands of each stage's recipe beyond
what stood when the recipe came into the plan.

Progress counts buildings, whatever their clock, paused ones too: underclocking the machines a plan
rounded up still builds every one of them. What stands is shared out among the stages running the
recipe in order, each taking no more than it builds. What the player marked built is shared out the
same way, and a stage counts whichever is more, the save's or the marks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pioneer.contracts import PlacementRecord, Plan, StageProgress

_TOLERANCE = 1e-9


def buildings_by_recipe(placements: Sequence[PlacementRecord]) -> dict[str, int]:
    """How many placed buildings run each recipe."""
    counts: dict[str, int] = {}
    for placement in placements:
        if placement.recipe_id is not None:
            counts[placement.recipe_id] = counts.get(placement.recipe_id, 0) + 1
    return counts


def plan_progress(
    plan: Plan, placements: Sequence[PlacementRecord] | None
) -> tuple[StageProgress, ...]:
    """Every stage of `plan` that builds anything, in its graph's order, with how much of it
    `placements` -- the save's buildings -- has built, and the player has marked. `placements`
    `None`, no save: only the marks can tell."""
    baseline = dict(plan.baseline_buildings)
    standing = buildings_by_recipe(placements) if placements is not None else {}
    beyond: dict[str, float] = {
        recipe_id: float(max(standing.get(recipe_id, 0) - count, 0))
        for recipe_id, count in baseline.items()
    }
    marks = {recipe_id: max(count, 0.0) for recipe_id, count in plan.marked_built}
    stages = []
    for node in plan.graph.nodes:
        to_build = node.machine_count - node.existing_machine_count
        if to_build <= _TOLERANCE:
            continue
        in_save: float | None = None
        if placements is not None and node.recipe_id in beyond:
            in_save = min(beyond[node.recipe_id], to_build)
            beyond[node.recipe_id] -= in_save
        marked = min(marks.get(node.recipe_id, 0.0), to_build)
        if marked:
            marks[node.recipe_id] -= marked
        stages.append(
            StageProgress(
                node_id=node.node_id,
                recipe_id=node.recipe_id,
                building_id=node.building_id,
                to_build=to_build,
                built=None if in_save is None and not marked else max(in_save or 0.0, marked),
                in_save=in_save,
                marked=marked,
            )
        )
    return tuple(stages)


def built_by_recipe(progress: Sequence[StageProgress]) -> Mapping[str, float | None]:
    """What `progress` has built of each recipe, its stages taken together; `None` where it can't
    be told."""
    built: dict[str, float | None] = {}
    for stage in progress:
        earlier = built.get(stage.recipe_id, 0.0)
        built[stage.recipe_id] = (
            None if earlier is None or stage.built is None else earlier + stage.built
        )
    return built
