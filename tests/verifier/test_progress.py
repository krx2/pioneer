"""Tests for how far the save has built the player's plan, against hand-built plans and
placements."""

from dataclasses import replace

from pioneer.contracts import (
    Coordinates,
    PlacementRecord,
    Plan,
    PlanTarget,
    ProductionGraph,
    ProductionNode,
    StageProgress,
)
from pioneer.verifier import buildings_by_recipe, built_by_recipe, plan_progress

_HERE = Coordinates(x=0, y=0)


def _placed(recipe_id: str | None, count: int, **fields) -> tuple[PlacementRecord, ...]:
    return tuple(
        PlacementRecord(
            building_id="Build_Whatever_C", position=_HERE, recipe_id=recipe_id, **fields
        )
        for _ in range(count)
    )


def _stage(recipe_id: str, machines: float, standing: float = 0, node_id: str | None = None):
    return ProductionNode(
        node_id=node_id or f"node_{recipe_id}",
        recipe_id=recipe_id,
        building_id="Build_Whatever_C",
        machine_count=machines,
        is_existing=standing > 0,
        existing_machine_count=standing,
    )


def _plan(*stages: ProductionNode, **baseline: int) -> Plan:
    return Plan(
        targets=(PlanTarget("Desc_IronPlate_C", 20),),
        graph=ProductionGraph(nodes=stages, flows=()),
        baseline_buildings=tuple(baseline.items()),
    )


def test_buildings_are_counted_whatever_their_clock_or_pause() -> None:
    placements = (
        *_placed("Recipe_IronPlate_C", 2, clock_speed=0.5),
        *_placed("Recipe_IronPlate_C", 1, is_paused=True),
        *_placed("Recipe_IngotIron_C", 1),
        *_placed(None, 4),  # belts, storage, power
    )

    assert buildings_by_recipe(placements) == {"Recipe_IronPlate_C": 3, "Recipe_IngotIron_C": 1}


def test_what_stands_beyond_the_baseline_is_built_up_to_what_the_stage_builds() -> None:
    plan = _plan(
        _stage("Recipe_IronPlate_C", 3),
        _stage("Recipe_IngotIron_C", 4),
        Recipe_IronPlate_C=5,
        Recipe_IngotIron_C=2,
    )
    placements = (*_placed("Recipe_IronPlate_C", 7), *_placed("Recipe_IngotIron_C", 9))

    assert plan_progress(plan, placements) == (
        StageProgress(
            "node_Recipe_IronPlate_C", "Recipe_IronPlate_C", "Build_Whatever_C", 3, 2, in_save=2
        ),
        StageProgress(
            "node_Recipe_IngotIron_C", "Recipe_IngotIron_C", "Build_Whatever_C", 4, 4, in_save=4
        ),
    )


def test_fewer_than_the_baseline_is_nothing_built() -> None:
    plan = _plan(_stage("Recipe_IronPlate_C", 3), Recipe_IronPlate_C=5)

    (stage,) = plan_progress(plan, _placed("Recipe_IronPlate_C", 4))

    assert stage.built == 0


def test_a_stage_extending_the_factory_builds_only_what_it_adds() -> None:
    plan = _plan(_stage("Recipe_IronPlate_C", 12, standing=10), Recipe_IronPlate_C=10)

    (stage,) = plan_progress(plan, _placed("Recipe_IronPlate_C", 11))

    assert (stage.to_build, stage.built) == (2, 1)


def test_stages_running_one_recipe_share_what_stands_in_order() -> None:
    plan = _plan(
        _stage("Recipe_IronPlate_C", 2, node_id="first"),
        _stage("Recipe_IronPlate_C", 2, node_id="second"),
        Recipe_IronPlate_C=0,
    )

    progress = plan_progress(plan, _placed("Recipe_IronPlate_C", 3))

    assert [(stage.node_id, stage.built) for stage in progress] == [("first", 2), ("second", 1)]
    assert built_by_recipe(progress) == {"Recipe_IronPlate_C": 3}


def test_without_a_save_or_a_baseline_progress_cannot_be_told() -> None:
    plan = _plan(
        _stage("Recipe_IronPlate_C", 3),
        _stage("Recipe_IngotIron_C", 4),
        Recipe_IronPlate_C=0,  # the ingot stage came in with no save to count from
    )
    placements = (*_placed("Recipe_IronPlate_C", 1), *_placed("Recipe_IngotIron_C", 1))

    assert [stage.built for stage in plan_progress(plan, placements)] == [1, None]
    assert [stage.built for stage in plan_progress(plan, None)] == [None, None]
    assert built_by_recipe(plan_progress(plan, placements)) == {
        "Recipe_IronPlate_C": 1,
        "Recipe_IngotIron_C": None,
    }


def test_what_the_player_marked_counts_where_it_is_more_than_the_save() -> None:
    """2 of 3 plate constructors marked, 1 standing: 2 built. 1 of 4 smelters marked, 3 standing:
    3 -- the same machines, counted once."""
    plan = replace(
        _plan(
            _stage("Recipe_IronPlate_C", 3),
            _stage("Recipe_IngotIron_C", 4),
            Recipe_IronPlate_C=0,
            Recipe_IngotIron_C=0,
        ),
        marked_built=(("Recipe_IronPlate_C", 2), ("Recipe_IngotIron_C", 1)),
    )
    placements = (*_placed("Recipe_IronPlate_C", 1), *_placed("Recipe_IngotIron_C", 3))

    assert [(s.built, s.in_save, s.marked) for s in plan_progress(plan, placements)] == [
        (2, 1, 2),
        (3, 3, 1),
    ]


def test_without_a_save_only_the_marks_can_tell() -> None:
    plan = replace(
        _plan(_stage("Recipe_IronPlate_C", 3), _stage("Recipe_IngotIron_C", 4)),
        marked_built=(("Recipe_IronPlate_C", 5),),  # more than the stage builds
    )

    assert [(s.built, s.in_save, s.marked) for s in plan_progress(plan, None)] == [
        (3, None, 3),
        (None, None, 0),
    ]
