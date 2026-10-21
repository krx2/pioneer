"""Tests for the player's plan in the Orchestrator, against a scripted fake model: what an answer
offers to be accepted, what the model is told about a conversation's plan, and how a revision
keeps everything the player didn't ask to change."""

import json
from dataclasses import replace
from typing import Any

from pioneer.contracts import (
    ChangeAction,
    ChangeItem,
    Coordinates,
    Item,
    ItemAmount,
    PlacementRecord,
    Plan,
    PlanTarget,
    ProductionGraph,
    ProductionNode,
    Recipe,
)
from pioneer.orchestrator.base import OrchestratorContext
from pioneer.orchestrator.orchestrator import handle_query

_IRON_INGOT = Recipe(
    recipe_id="Recipe_IngotIron_C",
    name="Iron Ingot",
    building_ids=("Build_SmelterMk1_C",),
    inputs=(ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
)
_HOT_IRON_INGOT = Recipe(  # made up: an alternate in the same building
    recipe_id="Recipe_Alternate_HotIngot_C",
    name="Alternate: Hot Iron Ingot",
    building_ids=("Build_SmelterMk1_C",),
    inputs=(ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=40),),
    outputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=60),),
    is_alternate=True,
)
_PURE_IRON_INGOT = Recipe(
    recipe_id="Recipe_Alternate_PureIronIngot_C",
    name="Alternate: Pure Iron Ingot",
    building_ids=("Build_OilRefinery_C",),
    inputs=(
        ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=35),
        ItemAmount(item_id="Desc_Water_C", amount_per_minute=20),
    ),
    outputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=65),),
    is_alternate=True,
)
_IRON_PLATE = Recipe(
    recipe_id="Recipe_IronPlate_C",
    name="Iron Plate",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronPlate_C", amount_per_minute=20),),
)
_IRON_ROD = Recipe(
    recipe_id="Recipe_IronRod_C",
    name="Iron Rod",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=15),),
    outputs=(ItemAmount(item_id="Desc_IronRod_C", amount_per_minute=15),),
)
_RECIPES = (_IRON_INGOT, _HOT_IRON_INGOT, _PURE_IRON_INGOT, _IRON_PLATE, _IRON_ROD)
_ITEMS = (
    Item(item_id="Desc_IronPlate_C", name="Iron Plate"),
    Item(item_id="Desc_IronRod_C", name="Iron Rod"),
    Item(item_id="Desc_IronIngot_C", name="Iron Ingot"),
    Item(item_id="Desc_OreIron_C", name="Iron Ore", is_raw_resource=True),
    Item(item_id="Desc_Water_C", name="Water", is_raw_resource=True),
)
_CONTEXT = OrchestratorContext(recipes=_RECIPES, items=_ITEMS)


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"call_{name}", "name": name, "arguments": arguments}


def _run(context: OrchestratorContext, *calls: dict[str, Any]):
    """One round running `calls`, then the answer "done": the result, each tool's result as the
    model saw it, and every request the model got."""
    requests: list[dict[str, Any]] = []
    replies = [{"content": None, "tool_calls": list(calls)}, {"content": "done", "tool_calls": []}]

    def llm(base_url, model, messages, tools, api_key):
        requests.append(
            {"messages": list(messages), "tools": [t["function"]["name"] for t in tools]}
        )
        return replies[len(requests) - 1]

    result = handle_query(
        llm,
        lambda *args: "",
        "question",
        context,
        llm_base_url="http://localhost",
        llm_model="test",
        response_id="resp",
    )
    tool_results = [
        json.loads(message["content"])
        for message in requests[-1]["messages"]
        if message["role"] == "tool"
    ]
    return result, tool_results, requests


def _existing(*, smelters: float, constructors: float) -> ProductionGraph:
    return ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="save_Recipe_IngotIron_C",
                recipe_id="Recipe_IngotIron_C",
                building_id="Build_SmelterMk1_C",
                machine_count=smelters,
                is_existing=True,
            ),
            ProductionNode(
                node_id="save_Recipe_IronPlate_C",
                recipe_id="Recipe_IronPlate_C",
                building_id="Build_ConstructorMk1_C",
                machine_count=constructors,
                is_existing=True,
            ),
        ),
        flows=(),
    )


def _proposed(context: OrchestratorContext, name: str, arguments: dict[str, Any]) -> Plan:
    result, _, _ = _run(context, _call(name, arguments))
    assert result.plan_proposal is not None
    return result.plan_proposal


def _plates(rate: float, **recipe_choices: str) -> Plan:
    """The plan for `rate` plates a minute, as `plan_production` offers it."""
    arguments: dict[str, Any] = {"target_item_id": "Iron Plate", "target_rate_per_minute": rate}
    if recipe_choices:
        arguments["recipe_choices"] = recipe_choices
    return _proposed(_CONTEXT, "plan_production", arguments)


def _revise(plan: Plan, context: OrchestratorContext = _CONTEXT, **arguments: Any):
    result, (tool_result,), _ = _run(replace(context, plan=plan), _call("revise_plan", arguments))
    return result, tool_result


def test_a_new_plan_is_offered_with_what_it_was_planned_from() -> None:
    context = replace(_CONTEXT, existing_graph=_existing(smelters=2, constructors=0))

    result, _, _ = _run(
        context,
        _call("plan_production", {"target_item_id": "Iron Plate", "target_rate_per_minute": 40}),
    )

    plan = result.plan_proposal
    assert plan is not None
    assert plan.graph is result.graph
    assert plan.targets == (PlanTarget("Desc_IronPlate_C", 40),)
    assert dict(plan.recipe_choices) == {
        "Desc_IronPlate_C": "Recipe_IronPlate_C",
        "Desc_IronIngot_C": "Recipe_IngotIron_C",
    }
    assert dict(plan.baseline_machines) == {"Recipe_IronPlate_C": 0, "Recipe_IngotIron_C": 2}
    assert (plan.extends_factory, plan.available_supply, plan.revision) == (False, (), 1)


def test_an_expansion_is_offered_as_a_plan_extending_the_factory() -> None:
    """2 smelters and 1 constructor leave 30 ingots and 20 plates spare: the plan draws on them."""
    context = replace(_CONTEXT, existing_graph=_existing(smelters=2, constructors=1))

    plan = _proposed(
        context,
        "expand_existing_factory",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 40},
    )

    assert plan.extends_factory is True
    assert dict(plan.available_supply) == {"Desc_IronIngot_C": 30, "Desc_IronPlate_C": 20}
    assert dict(plan.baseline_machines) == {"Recipe_IronPlate_C": 1}


def test_the_model_is_told_the_plan_and_given_its_tools_only_in_a_conversation_with_one() -> None:
    plan = _plates(40)

    _, _, without = _run(_CONTEXT, _call("find_item", {"query": "plate"}))
    result, _, with_plan = _run(replace(_CONTEXT, plan=plan), _call("find_item", {"query": "x"}))

    assert "show_plan" not in without[0]["tools"]
    assert "player's plan" not in without[0]["messages"][0]["content"]
    assert with_plan[0]["tools"][-2:] == ["show_plan", "revise_plan"]
    system = with_plan[0]["messages"][0]["content"]
    assert "revision 1" in system
    assert "Makes: 40/min Iron Plate" in system
    assert "2 Build_ConstructorMk1_C running Iron Plate [Recipe_IronPlate_C]" in system
    assert any("Makes: 40/min Iron Plate" in text for text in result.grounding)


def test_with_a_plan_a_new_one_is_planned_apart_and_not_offered() -> None:
    result, (tool_result,), _ = _run(
        replace(_CONTEXT, plan=_plates(40)),
        _call("plan_production", {"target_item_id": "Iron Rod", "target_rate_per_minute": 15}),
    )

    assert result.graph is not None
    assert result.plan_proposal is None
    assert "revise_plan" in tool_result["note"]


def test_a_new_rate_keeps_every_stage_on_its_recipe() -> None:
    """Planned on Pure Iron Ingot, the plan stays on it at the new rate -- left to itself, the
    planner would take the standard smelter recipe."""
    plan = _plates(40, **{"Iron Ingot": "Recipe_Alternate_PureIronIngot_C"})

    result, tool_result = _revise(plan, targets={"Iron Plate": 80})

    revised = result.plan_proposal
    assert revised is not None and revised.graph is result.graph
    assert revised.revision == 2
    assert revised.targets == (PlanTarget("Desc_IronPlate_C", 80),)
    assert {n.recipe_id: n.machine_count for n in revised.graph.nodes} == {
        "Recipe_IronPlate_C": 4,
        "Recipe_Alternate_PureIronIngot_C": 2,
    }
    assert revised.changes == (
        ChangeItem(
            ChangeAction.EXTEND,
            "Recipe_IronPlate_C",
            2,
            "node_Desc_IronPlate_C",
            building_id="Build_ConstructorMk1_C",
        ),
        ChangeItem(
            ChangeAction.EXTEND,
            "Recipe_Alternate_PureIronIngot_C",
            1,
            "node_Desc_IronIngot_C",
            building_id="Build_OilRefinery_C",
        ),
    )
    assert tool_result["revision"] == 2
    assert tool_result["changes"][0] == {
        "action": "extend",
        "recipe_id": "Recipe_IronPlate_C",
        "building_id": "Build_ConstructorMk1_C",
        "machines": 2,
    }


def test_another_target_joins_the_plan_and_one_can_be_dropped_again() -> None:
    result, _ = _revise(_plates(40), targets={"Iron Rod": 15})

    both = result.plan_proposal
    assert both.targets == (PlanTarget("Desc_IronPlate_C", 40), PlanTarget("Desc_IronRod_C", 15))
    assert [(c.action, c.recipe_id) for c in both.changes] == [
        (ChangeAction.ADD, "Recipe_IronRod_C"),
        (ChangeAction.EXTEND, "Recipe_IngotIron_C"),
    ]

    result, _ = _revise(both, remove_targets=["Iron Plate"])

    assert result.plan_proposal.targets == (PlanTarget("Desc_IronRod_C", 15),)
    assert [(c.action, c.recipe_id) for c in result.plan_proposal.changes] == [
        (ChangeAction.REMOVE, "Recipe_IngotIron_C"),
        (ChangeAction.REMOVE, "Recipe_IronPlate_C"),
    ]


def test_dropping_what_is_no_target_or_every_target_is_refused() -> None:
    plan = _plates(40)

    result, not_a_target = _revise(plan, remove_targets=["Iron Rod"])
    _, last_target = _revise(plan, remove_targets=["Iron Plate"])

    assert "not a target" in not_a_target["error"]
    assert "at least one target" in last_target["error"]
    assert result.plan_proposal is None


def test_another_recipe_in_the_same_building_reconfigures_its_machines() -> None:
    """2 smelters on Iron Ingot make the 60 ingots; Hot Iron Ingot makes them in one."""
    result, tool_result = _revise(
        _plates(40), recipe_choices={"Iron Ingot": "Recipe_Alternate_HotIngot_C"}
    )

    assert [
        (c.action, c.recipe_id, c.additional_machine_count) for c in result.plan_proposal.changes
    ] == [
        (ChangeAction.RECONFIGURE, "Recipe_Alternate_HotIngot_C", 1),
        (ChangeAction.REMOVE, "Recipe_IngotIron_C", -1),
    ]
    assert tool_result["changes"][0]["from_recipe_id"] == "Recipe_IngotIron_C"
    assert dict(result.plan_proposal.recipe_choices)["Desc_IronIngot_C"] == (
        "Recipe_Alternate_HotIngot_C"
    )


def test_another_recipe_in_another_building_takes_the_old_machines_down() -> None:
    result, _ = _revise(
        _plates(40), recipe_choices={"Iron Ingot": "Recipe_Alternate_PureIronIngot_C"}
    )

    assert [(c.action, c.recipe_id) for c in result.plan_proposal.changes] == [
        (ChangeAction.REMOVE, "Recipe_IngotIron_C"),
        (ChangeAction.ADD, "Recipe_Alternate_PureIronIngot_C"),
    ]


def test_a_revision_that_changes_nothing_is_drawn_but_not_offered() -> None:
    plan = _plates(40)

    result, tool_result = _revise(plan, targets={"Iron Plate": 40})

    assert result.graph == plan.graph
    assert result.plan_proposal is None
    assert (tool_result["revision"], tool_result["changes"]) == (1, [])
    assert "nothing changes" in tool_result["note"]


def test_show_plan_draws_the_plan_as_it_stands() -> None:
    plan = _plates(40)

    result, (tool_result,), _ = _run(replace(_CONTEXT, plan=plan), _call("show_plan", {}))

    assert result.graph is plan.graph
    assert result.plan_proposal is None
    assert tool_result["targets_per_minute"] == {"Desc_IronPlate_C": 40}
    assert [stage["recipe_id"] for stage in tool_result["stages"]] == [
        "Recipe_IronPlate_C",
        "Recipe_IngotIron_C",
    ]


def test_a_plan_extending_the_factory_extends_it_as_it_stood() -> None:
    """The plan adds a constructor to the one standing; the player has built it since. Revised,
    the plan still extends the one that stood -- the one they built is the plan's own."""
    before = replace(_CONTEXT, existing_graph=_existing(smelters=2, constructors=1))
    plan = _proposed(
        before,
        "expand_existing_factory",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 40},
    )
    built = replace(_CONTEXT, existing_graph=_existing(smelters=2, constructors=2))

    same, _ = _revise(plan, built, targets={"Iron Plate": 40})
    more, tool_result = _revise(plan, built, targets={"Iron Plate": 60})

    assert same.plan_proposal is None
    constructors = next(n for n in more.graph.nodes if n.recipe_id == "Recipe_IronPlate_C")
    assert (constructors.existing_machine_count, constructors.machine_count) == (1, 3)
    assert (ChangeAction.EXTEND, "Recipe_IronPlate_C", 1) in [
        (c.action, c.recipe_id, c.additional_machine_count) for c in more.plan_proposal.changes
    ]
    assert dict(more.plan_proposal.baseline_machines) == {
        "Recipe_IronPlate_C": 1,
        "Recipe_IngotIron_C": 2,  # new to the plan: counted from what stands now
    }
    assert tool_result["stages"][0]["already_standing"] == 1


def test_a_plan_is_offered_only_with_its_own_graph() -> None:
    context = replace(_CONTEXT, existing_graph=_existing(smelters=2, constructors=1))

    result, _, _ = _run(
        context,
        _call("plan_production", {"target_item_id": "Iron Plate", "target_rate_per_minute": 40}),
        _call("show_existing_factory", {}),
    )

    assert result.graph is not None
    assert result.plan_proposal is None


def _built(**counts: int) -> tuple[PlacementRecord, ...]:
    """The save's buildings: `counts` of them per recipe id."""
    return tuple(
        PlacementRecord(building_id="Build_Whatever_C", position=Coordinates(0, 0), recipe_id=r)
        for r, count in counts.items()
        for _ in range(count)
    )


def _saved(*, smelters: int, constructors: int) -> OrchestratorContext:
    """A save with `smelters` and `constructors` standing, at full clock."""
    return replace(
        _CONTEXT,
        existing_graph=_existing(smelters=smelters, constructors=constructors),
        existing_placements=_built(Recipe_IngotIron_C=smelters, Recipe_IronPlate_C=constructors),
    )


def _planned_on_a_save() -> Plan:
    """40 plates a minute -- 2 constructors and 2 smelters -- with 2 smelters standing already."""
    return _proposed(
        _saved(smelters=2, constructors=0),
        "plan_production",
        {"target_item_id": "Iron Plate", "target_rate_per_minute": 40},
    )


def test_progress_counts_from_the_buildings_standing_when_the_plan_was_made() -> None:
    assert dict(_planned_on_a_save().baseline_buildings) == {
        "Recipe_IronPlate_C": 0,
        "Recipe_IngotIron_C": 2,
    }
    assert _plates(40).baseline_buildings == ()  # no save: nothing to count from


def test_the_model_is_told_how_far_the_plan_is_built() -> None:
    plan = _planned_on_a_save()
    now = replace(_saved(smelters=3, constructors=1), plan=plan)

    _, (shown,), requests = _run(now, _call("show_plan", {}))
    _, _, without_save = _run(replace(_CONTEXT, plan=plan), _call("find_item", {"query": "x"}))

    system = requests[0]["messages"][0]["content"]
    assert "running Iron Plate [Recipe_IronPlate_C] -- 1 built" in system
    assert "Built so far: 2 of 4 machines, by the latest save and what the player" in system
    assert [stage.get("built") for stage in shown["stages"]] == [1, 1]
    assert shown["built_so_far"] == {"machines_built": 2, "machines_to_build": 4}
    assert "Built so far: can't be told" in without_save[0]["messages"][0]["content"]


def test_a_revision_says_how_many_of_what_it_takes_down_already_stand() -> None:
    """Both smelters the plan builds stand; switched to Pure Iron Ingot in refineries, they come
    down -- and the refineries count from the none that stand now."""
    plan = _planned_on_a_save()

    result, tool_result = _revise(
        plan,
        _saved(smelters=4, constructors=0),
        recipe_choices={"Iron Ingot": "Recipe_Alternate_PureIronIngot_C"},
    )

    removed = next(c for c in result.plan_proposal.changes if c.action is ChangeAction.REMOVE)
    assert (removed.recipe_id, removed.already_built) == ("Recipe_IngotIron_C", 2)
    assert tool_result["changes"][0]["already_built"] == 2
    assert dict(result.plan_proposal.baseline_buildings)["Recipe_Alternate_PureIronIngot_C"] == 0


def test_what_the_player_marked_built_is_told_to_the_model() -> None:
    """No save, but the player ticked one of the plate constructors built."""
    plan = replace(_plates(40), marked_built=(("Recipe_IronPlate_C", 1),))

    _, (shown,), requests = _run(replace(_CONTEXT, plan=plan), _call("show_plan", {}))

    system = requests[0]["messages"][0]["content"]
    assert "[Recipe_IronPlate_C] -- 1 built (1 marked built by the player)" in system
    assert "1 of 2 machines, by the latest save and what the player marked built (for the" in system
    assert shown["stages"][0]["marked_built_by_player"] == 1
    assert shown["built_so_far"] == {"machines_built": 1, "machines_to_build": 2}
