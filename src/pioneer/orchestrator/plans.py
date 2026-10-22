"""The player's plan, as the Orchestrator makes and shows it: a proposal built from what a planning
tool just planned, how far the save has built it, and the note that tells the model about the plan
a conversation has.

A proposal pins what it was planned from, so a revision can plan it again the same way (see
`contracts.plan`): the recipe each stage runs, read off the graph itself, and what of each of those
recipes already stood in the save -- machines, clock-scaled, for what a plan extending the factory
extends, and buildings, for what the player builds for the plan to count from. With no save loaded
there's nothing to count from: the plan's progress then can't be told.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pioneer.contracts import (
    Plan,
    PlanTarget,
    ProductionGraph,
    SitePlan,
    StageProgress,
    SupplyReservation,
)
from pioneer.orchestrator.base import OrchestratorContext
from pioneer.verifier import buildings_by_recipe, plan_progress


def propose_plan(
    context: OrchestratorContext,
    targets: Mapping[str, float],
    graph: ProductionGraph,
    *,
    available_supply: Mapping[str, float] | None = None,
    sites: SitePlan | None = None,
    reservations: Sequence[SupplyReservation] = (),
) -> Plan:
    """The plan `graph` is, offered to the player: from scratch, or -- given the existing factory's
    `available_supply` it was planned on -- extending their factory; built where `sites` says,
    taking what `reservations` say of their factories' spare output."""
    recipe_ids = dict.fromkeys(node.recipe_id for node in graph.nodes)
    machines = machines_by_recipe(context.existing_graph)
    buildings = standing_buildings(context)
    return Plan(
        targets=tuple(PlanTarget(item_id, rate) for item_id, rate in targets.items()),
        graph=graph,
        recipe_choices=recipe_choices_of(graph),
        extends_factory=available_supply is not None,
        available_supply=tuple((available_supply or {}).items()),
        baseline_machines=tuple((r, machines.get(r, 0.0)) for r in recipe_ids),
        baseline_buildings=(
            tuple((r, buildings.get(r, 0)) for r in recipe_ids) if buildings is not None else ()
        ),
        sites=sites,
        reservations=tuple(reservations),
    )


def recipe_choices_of(graph: ProductionGraph) -> tuple[tuple[str, str], ...]:
    """Item id -> recipe id for every stage of `graph`, the item being what flows out of its
    node."""
    recipe_of = {node.node_id: node.recipe_id for node in graph.nodes}
    choices: dict[str, str] = {}
    for flow in graph.flows:
        if flow.source_node_id in recipe_of:
            choices.setdefault(flow.item_id, recipe_of[flow.source_node_id])
    return tuple(choices.items())


def machines_by_recipe(graph: ProductionGraph | None) -> dict[str, float]:
    """How many machines run each recipe in `graph` -- the save's factory, say -- all its nodes
    running one taken together."""
    machines: dict[str, float] = {}
    for node in graph.nodes if graph is not None else ():
        machines[node.recipe_id] = machines.get(node.recipe_id, 0.0) + node.machine_count
    return machines


def standing_buildings(context: OrchestratorContext) -> dict[str, int] | None:
    """How many of the save's buildings run each recipe; `None` with no save loaded."""
    if context.existing_graph is None:
        return None
    return buildings_by_recipe(context.existing_placements)


def progress_of(plan: Plan, context: OrchestratorContext) -> tuple[StageProgress, ...]:
    """How far the save in `context` has built `plan`, stage by stage."""
    return plan_progress(
        plan, context.existing_placements if context.existing_graph is not None else None
    )


def deposits_built(plan: Plan, context: OrchestratorContext) -> tuple[int, int] | None:
    """How many of the plan's deposits have an extractor standing on them in the save, of how
    many -- `None` for a plan keeping no deposits, or with no save to tell."""
    if plan.sites is None or not plan.sites.deposits or context.existing_graph is None:
        return None
    built = {p.resource_node_id for p in context.existing_placements if p.resource_node_id}
    deposits = plan.sites.deposits
    return sum(d.resource_node_id in built for d in deposits), len(deposits)


def plan_note(
    plan: Plan,
    names: Mapping[str, str],
    progress: Sequence[StageProgress],
    deposits: tuple[int, int] | None = None,
) -> str:
    """The player's plan as the model is shown it, next to what data it has: what the plan makes,
    every stage and how much of it is built, in the game's names -- with each recipe's id, which
    revise_plan takes -- and where it's built: its sites and deposits (`deposits`: how many have
    an extractor already, of how many), and what it reserves of the factories' spare output."""
    targets = ", ".join(
        f"{target.rate_per_minute:g}/min {names.get(target.item_id, target.item_id)}"
        for target in plan.targets
    )
    progress_of_node = {stage.node_id: stage for stage in progress}
    stages = []
    for node in plan.graph.nodes:
        to_build = node.machine_count - node.existing_machine_count
        stage = (
            f"{to_build:g} {names.get(node.building_id, node.building_id)} running "
            f"{names.get(node.recipe_id, node.recipe_id)} [{node.recipe_id}]"
        )
        if node.existing_machine_count > 0:
            stage += f" (next to {node.existing_machine_count:g} already standing)"
        told = progress_of_node.get(node.node_id)
        if told is not None and told.built is not None:
            stage += f" -- {told.built:g} built"
            if told.marked:
                stage += f" ({told.marked:g} marked built by the player)"
        stages.append(stage)
    basis = (
        "it extends their existing factory" if plan.extends_factory else "it's planned from scratch"
    )
    where = _where(plan, names, deposits)
    return (
        f"The player's plan for this conversation -- revision {plan.revision}, accepted by the "
        f"player; {basis}:\n"
        f"- Makes: {targets}\n"
        f"- Stages to build: {'; '.join(stages) or 'none'}\n"
        f"- Built so far: {_built_so_far(progress)}\n"
        f"{where}"
        "Questions in this conversation are about this plan. To change it -- a target's rate, "
        "another target, a stage's recipe -- call revise_plan, never plan_production or "
        "expand_existing_factory: those make a separate plan, not a change to this one. To draw "
        "it, call show_plan. A revision becomes the plan only once the player accepts it on "
        "the page: say what it would change, never that the plan has changed."
    )


def _where(plan: Plan, names: Mapping[str, str], deposits: tuple[int, int] | None) -> str:
    """The note's lines on where the plan is built, each ending in a newline -- none when it
    keeps nothing of it."""
    lines = []
    if plan.sites is not None and plan.sites.sites:
        fed = f"{len(plan.sites.deposits)} deposits"
        if deposits is not None:
            fed += f", {deposits[0]} of them with an extractor already"
        sites = ", ".join(site.site_id for site in plan.sites.sites)
        lines.append(f"- Built at: {sites}; fed from {fed} (show_plan says which)\n")
    if plan.reservations:
        reserved = "; ".join(
            f"{r.rate_per_minute:g}/min {names.get(r.item_id, r.item_id)} from {r.site_id}"
            for r in plan.reservations
        )
        lines.append(f"- Reserves of the factories' spare output: {reserved}\n")
    return "".join(lines)


def _built_so_far(progress: Sequence[StageProgress]) -> str:
    known = [stage for stage in progress if stage.built is not None]
    if not known:
        return (
            "can't be told -- no save to count the player's buildings from, now or from when "
            "the plan was made"
        )
    built = sum(stage.built or 0.0 for stage in known)
    note = (
        f"{built:g} of {sum(stage.to_build for stage in known):g} machines, by the latest save and "
        "what the player marked built"
    )
    if len(known) < len(progress):
        note += " (for the stages it can be told of)"
    return note
