"""Tool handlers for the player's plan, offered only in a conversation that has one: drawing it, and
revising it.

**A revision is planned again, not anew.** `revise_plan` takes only what the player wants changed
-- a target's rate, a target added or dropped, another recipe for an item -- and plans the result
from everything else the plan was made from: its other targets, the recipe every other stage runs
(pinned, see `contracts.plan`), and for a plan extending the factory, the spare output it drew on
then. The planner being deterministic, what the player didn't touch comes out as it was, and
`expansion_advisor.plan_changes` says what did change: machines to build, take down or switch to
another recipe.

**What's built already.** How far the save has built the plan (see `verifier.progress`) comes with
it: `show_plan` says it stage by stage, and a revision says, of the machines it takes down or
switches over, how many already stand in the game.

**Extending the factory as it stood.** A plan extending the factory extends what stood when its
recipes came into it (`Plan.baseline_machines`), not what stands now: the machines the player has
built for the plan since are part of it, not more factory to extend.

**Where it's built stays.** The plan keeps its deposits and sites (`Plan.sites`): `show_plan`
shows them as they were accepted, with how many already have an extractor standing on them, and a
revision keeps them — more deposits for more, fewer for less, the ones built on kept first — and
keeps taking what it reserved of the factories' spare output from the same factories.

The revision is a proposal, like a new plan: it's the player who accepts it, on the page.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pioneer.contracts import (
    Plan,
    PlanTarget,
    ProductionGraph,
    ProductionNode,
    SitePlan,
    SupplyReservation,
)
from pioneer.expansion_advisor import advise_expansion, plan_changes
from pioneer.orchestrator.base import OrchestratorContext, _ArtifactAccumulator, _ToolError
from pioneer.orchestrator.existing_factory import _existing_item_balance, _with_spare_power
from pioneer.orchestrator.items import _resolve_item_id, raw_resource_ids
from pioneer.orchestrator.planning_tools import (
    _needs_unlocking,
    _plan_targets,
    _PlanningBlocked,
    _recipe_choices,
    _target_rate,
)
from pioneer.orchestrator.plans import (
    machines_by_recipe,
    progress_of,
    recipe_choices_of,
    standing_buildings,
)
from pioneer.orchestrator.reservations import factory_of, reserve, unreserved
from pioneer.orchestrator.sites import _build_sites, _shown
from pioneer.orchestrator.summaries import _graph_summary, _per_item
from pioneer.verifier import built_by_recipe


def _handle_show_plan(
    context: OrchestratorContext, accumulator: _ArtifactAccumulator
) -> dict[str, Any]:
    plan = _the_plan(context)
    accumulator.graph = plan.graph
    progress = progress_of(plan, context)
    summary = _plan_summary(plan.graph, context)
    by_node = {stage.node_id: stage for stage in progress}
    for stage, node in zip(summary["stages"], plan.graph.nodes, strict=True):
        told = by_node.get(node.node_id)
        if told is not None and told.built is not None:
            stage["built"] = told.built
        if told is not None and told.marked:
            stage["marked_built_by_player"] = told.marked
    known = [stage for stage in progress if stage.built is not None]
    if plan.sites is not None:
        sites = _shown(plan.sites, context, accumulator, kept=plan.sites)
        built = {p.resource_node_id for p in context.existing_placements if p.resource_node_id}
        deposits = plan.sites.deposits
        sites["build_sites"]["deposits_with_an_extractor"] = (
            f"{sum(d.resource_node_id in built for d in deposits)} of {len(deposits)}"
        )
    elif not plan.extends_factory:  # a plan from before plans kept where they're built
        sites, _ = _build_sites(
            _per_item(f for f in plan.graph.flows if f.source_node_id is None),
            context,
            accumulator,
            plan.graph,
        )
    else:
        sites = {}
    if plan.reservations:
        sites["reserved_from_factories"] = _reserved(plan, context)
    return {
        "revision": plan.revision,
        "targets_per_minute": _targets(plan.targets),
        "extends_existing_factory": plan.extends_factory,
        "built_so_far": (
            {
                "machines_built": sum(stage.built or 0.0 for stage in known),
                "machines_to_build": sum(stage.to_build for stage in known),
            }
            if known
            else "unknown -- no save to count the player's buildings from, now or from when "
            "the plan was made"
        ),
        **summary,
        **_needs_unlocking(plan.graph, context),
        **sites,
    }


def _handle_revise_plan(
    args: dict[str, Any], context: OrchestratorContext, accumulator: _ArtifactAccumulator
) -> dict[str, Any]:
    plan = _the_plan(context)
    targets = {target.item_id: target.rate_per_minute for target in plan.targets}
    set_targets = args.get("targets")
    for item, rate in set_targets.items() if isinstance(set_targets, dict) else ():
        targets[_resolve_item_id(item, context)] = _target_rate({"target_rate_per_minute": rate})
    dropped = args.get("remove_targets")
    for item in dropped if isinstance(dropped, list) else ():
        item_id = _resolve_item_id(item, context)
        if item_id not in targets:
            raise _ToolError(f"{item_id} is not a target of the plan", targets=list(targets))
        del targets[item_id]
    if not targets:
        raise _ToolError(
            "a plan needs at least one target -- for a different plan altogether, the player "
            "starts a new conversation"
        )
    choices = dict(plan.recipe_choices) | (_recipe_choices(args, context) or {})
    supply = dict(plan.available_supply) if plan.extends_factory else None
    try:
        graph = _plan_targets(context, targets, choices, available_supply=supply)
    except _PlanningBlocked as blocked:
        return blocked.result(context, accumulator)
    except (ValueError, KeyError) as error:
        return {"error": str(error)}

    baseline = dict(plan.baseline_machines)
    standing = machines_by_recipe(context.existing_graph)
    baseline_buildings = dict(plan.baseline_buildings)
    buildings_now = standing_buildings(context)
    for node in graph.nodes:  # a recipe new to the plan counts from what stands of it now
        baseline.setdefault(node.recipe_id, standing.get(node.recipe_id, 0.0))
        if buildings_now is not None:
            baseline_buildings.setdefault(node.recipe_id, buildings_now.get(node.recipe_id, 0))
    additions = graph
    if plan.extends_factory:
        graph = advise_expansion(_as_it_stood(graph, baseline, context), graph).resulting_graph
    sites_found, sites, reservations = _kept_where(plan, additions, context, accumulator)

    changes = plan_changes(plan.graph, graph, built_by_recipe(progress_of(plan, context)))
    unchanged = not changes and targets == _targets(plan.targets)
    revision = plan.revision if unchanged else plan.revision + 1
    summary = _plan_summary(graph, context)  # fails on a building the knowledge base doesn't list
    accumulator.graph = graph
    if not unchanged:
        accumulator.plan_proposal = Plan(
            targets=tuple(PlanTarget(item_id, rate) for item_id, rate in targets.items()),
            graph=graph,
            recipe_choices=recipe_choices_of(graph),
            extends_factory=plan.extends_factory,
            available_supply=plan.available_supply,
            baseline_machines=tuple(baseline.items()),
            baseline_buildings=tuple(baseline_buildings.items()),
            revision=revision,
            changes=changes,
            sites=sites,
            reservations=reservations,
        )
    result: dict[str, Any] = {
        "revision": revision,
        "targets_per_minute": targets,
        "changes": [
            {
                "action": change.action.value,
                "recipe_id": change.recipe_id,
                "building_id": change.building_id,
                "machines": change.additional_machine_count,
                **({"from_recipe_id": change.from_recipe_id} if change.from_recipe_id else {}),
                # Standing already: to take down or switch over in the game.
                **(
                    {"already_built": change.already_built}
                    if change.already_built is not None
                    else {}
                ),
            }
            for change in changes
        ],
        **summary,
        **_needs_unlocking(graph, context),
    }
    if unchanged:
        result["note"] = "nothing changes: the plan stays as it is"
    result.update(sites_found)
    if reservations:
        result["reserved_from_factories"] = [
            {
                "site_id": reservation.site_id,
                "item_id": reservation.item_id,
                "per_minute": round(reservation.rate_per_minute, 1),
            }
            for reservation in reservations
        ]
    return _with_spare_power(result, context)


def _kept_where(
    plan: Plan,
    additions: ProductionGraph,
    context: OrchestratorContext,
    accumulator: _ArtifactAccumulator,
) -> tuple[dict[str, Any], SitePlan | None, tuple[SupplyReservation, ...]]:
    """Where the revised plan — `additions`, before it's laid over the factory — is built: the
    plan's own deposits and sites revised, and for a plan extending the factory, what it reserves
    of the factories' spare output, from the same factories first. What it reserved before is
    its own, spare or not now that the player may have built what uses it."""
    raw_inputs = _per_item(f for f in additions.flows if f.source_node_id is None)
    if not plan.extends_factory:
        found, sites = _build_sites(raw_inputs, context, accumulator, additions, kept=plan.sites)
        return found, sites, ()
    raw_item_ids = raw_resource_ids(context)
    whole = unreserved(_existing_item_balance(context), context)
    for reservation in plan.reservations:
        own = sum(r.rate_per_minute for r in plan.reservations if r.item_id == reservation.item_id)
        whole[reservation.item_id] = max(whole.get(reservation.item_id, 0.0), own)
    supplied = dict(plan.available_supply)
    draws = {item: rate for item, rate in raw_inputs.items() if item in supplied}
    mined_spare = {
        item: min(rate, whole[item])
        for item, rate in raw_inputs.items()
        if item in raw_item_ids and whole.get(item, 0.0) > 0
    }
    near = plan.sites.sites[0].position if plan.sites is not None and plan.sites.sites else None
    reservations = reserve(draws | mined_spare, context, whole, near=near, kept=plan.reservations)
    uncovered = {
        item: rate - mined_spare.get(item, 0.0)
        for item, rate in raw_inputs.items()
        if item in raw_item_ids and rate > mined_spare.get(item, 0.0) + 1e-6
    }
    found, sites = _build_sites(uncovered, context, accumulator, reference=near, kept=plan.sites)
    return found, sites, reservations


def _reserved(plan: Plan, context: OrchestratorContext) -> list[dict[str, Any]]:
    """The plan's reservations as the model is told them: from which factory — as the save now
    names it — how much of what."""
    shown = []
    for reservation in plan.reservations:
        site = factory_of(reservation, context.factory_sites)
        shown.append(
            {
                "site_id": site.site_id if site is not None else reservation.site_id,
                "item_id": reservation.item_id,
                "per_minute": round(reservation.rate_per_minute, 1),
            }
        )
    return shown


def _the_plan(context: OrchestratorContext) -> Plan:
    if context.plan is None:
        raise _ToolError("this conversation has no plan -- plan_production makes one to propose")
    return context.plan


def _targets(targets: tuple[PlanTarget, ...]) -> dict[str, float]:
    return {target.item_id: target.rate_per_minute for target in targets}


def _plan_summary(graph: ProductionGraph, context: OrchestratorContext) -> dict[str, Any]:
    """`_graph_summary` of a plan, each stage extending the factory saying how many already
    stand."""
    summary = _graph_summary(graph, context)
    for stage, node in zip(summary["stages"], graph.nodes, strict=True):
        if node.existing_machine_count > 0:
            stage["already_standing"] = node.existing_machine_count
    return summary


def _as_it_stood(
    additions: ProductionGraph, baseline: dict[str, float], context: OrchestratorContext
) -> ProductionGraph:
    """The existing factory's stages for the recipes in `additions`, with as many machines as
    stood when each came into the plan -- what the plan extends. Taken from the save's own node
    where it still has one, for its id and Somersloops."""
    in_save = {
        node.recipe_id: node
        for node in (context.existing_graph.nodes if context.existing_graph is not None else ())
    }
    stood = []
    for node in additions.nodes:
        machines = baseline.get(node.recipe_id, 0.0)
        if machines <= 0:
            continue
        saved = in_save.get(node.recipe_id)
        stood.append(
            replace(saved, machine_count=machines)
            if saved is not None
            else ProductionNode(
                node_id=f"save_{node.recipe_id}",
                recipe_id=node.recipe_id,
                building_id=node.building_id,
                machine_count=machines,
                is_existing=True,
            )
        )
    return ProductionGraph(nodes=tuple(stood), flows=())
