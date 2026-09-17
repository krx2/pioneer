"""The Orchestrator's verification step — architecture.md §4.3 step 3 and §6: score a finished
`ResponseArtifact` with the Stage 15 scoring functions, against the same data it was built from.

Kept out of `handle_query` so the caller decides when to pay for it — the LLM-as-a-judge hooks
cost a model call each — and so the routing loop stays about routing. A channel whose scoring
can't run on the data at hand (no knowledge base, a graph on a building the knowledge base doesn't
list) comes back unscored rather than costing the other channels theirs.

A graph is scored for what it adds: an expansion's extended nodes count only their new machines.
Those are held against the power the player's grid has to spare (when a save is loaded), and
against the same plan in fractional machines — the optimum a whole-machine plan overshoots, which
is its distance from optimum (architecture.md §6).
"""

from __future__ import annotations

from dataclasses import replace

from pioneer.contracts import ResponseArtifact
from pioneer.orchestrator.orchestrator import (
    OrchestratorContext,
    _context_note,
    _existing_power,
    _raw_item_ids,
    _reference_point,
)
from pioneer.verification_feedback import (
    ChatJudge,
    ChatScore,
    GraphScore,
    MapScore,
    ResponseScore,
    TerrainJudge,
    check_rag_consistency,
    score_graph,
    score_map,
)
from pioneer.verifier import added_machines, minimal_machine_graph


def verify_response(
    artifact: ResponseArtifact,
    context: OrchestratorContext,
    *,
    chat_judge: ChatJudge | None = None,
    terrain_judge: TerrainJudge | None = None,
) -> ResponseScore:
    return ResponseScore(
        chat=_chat_score(artifact, context, chat_judge),
        graph=_graph_score(artifact, context),
        map=_map_score(artifact, context, terrain_judge),
    )


def _chat_score(
    artifact: ResponseArtifact, context: OrchestratorContext, judge: ChatJudge | None
) -> ChatScore | None:
    """Grounding: how much of the answer's wording is in what it was built from."""
    if not artifact.chat:
        return None
    score = check_rag_consistency(artifact.chat, artifact.grounding)
    if artifact.feedback is not None and artifact.feedback.qualitative_score is not None:
        score = replace(score, qualitative_score=artifact.feedback.qualitative_score)
    if judge is not None:
        verdict = judge(artifact.question or "", artifact.chat, _context_note(context))
        score = replace(score, judge_verdict=verdict)
    return score


def _graph_score(artifact: ResponseArtifact, context: OrchestratorContext) -> GraphScore | None:
    """Balance and power, with every material the graph takes in from outside — raw resources,
    and an expansion's draw on the existing factory — not held against it."""
    graph = artifact.graph
    if graph is None or not context.recipes or not context.buildings:
        return None
    from_outside = _raw_item_ids(context) | {
        flow.item_id for flow in graph.flows if flow.source_node_id is None
    }
    added = added_machines(graph, context.existing_graph)
    try:
        return score_graph(
            added,
            context.recipes,
            context.buildings,
            optimal_graph=minimal_machine_graph(added, context.recipes),
            available_power_mw=_spare_power_mw(context),
            raw_item_ids=from_outside,
        )
    except ValueError:
        return None


def _spare_power_mw(context: OrchestratorContext) -> float | None:
    """What the player's grid can still supply: its capacity minus its draw, from the save. `None`
    with no save loaded, or no way to tell the capacity."""
    if context.existing_graph is None:
        return None
    try:
        draw, capacity = _existing_power(context)
    except ValueError:
        return None
    return None if capacity is None else capacity - draw


def _map_score(
    artifact: ResponseArtifact, context: OrchestratorContext, judge: TerrainJudge | None
) -> tuple[MapScore, ...] | None:
    if artifact.map_locations is None:
        return None
    return score_map(
        artifact.map_locations,
        context.resource_nodes,
        reference=artifact.map_reference,
        placements=context.existing_placements,
        judge=judge,
        judge_context=_terrain_context(context),
    )


def _terrain_context(context: OrchestratorContext) -> str:
    placements = context.existing_placements
    if not placements:
        return "Nothing is known about what the player has built (no save loaded)."
    base = _reference_point({}, context)
    return (
        f"The player has {len(placements)} buildings, centred around "
        f"x={base.x:.0f} y={base.y:.0f} z={base.z:.0f} (centimetres)."
    )
