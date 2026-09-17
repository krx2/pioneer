"""The Orchestrator's verification step — architecture.md §4.3 step 3 and §6: score a finished
`ResponseArtifact` with the Stage 15 scoring functions, against the same data it was built from.

Kept out of `handle_query` so the caller decides when to pay for it — the LLM-as-a-judge hooks
cost a model call each — and so the routing loop stays about routing. A channel whose scoring
can't run on the data at hand (no knowledge base, a graph on a building the knowledge base doesn't
list) comes back unscored rather than costing the other channels theirs.
"""

from __future__ import annotations

from dataclasses import replace

from pioneer.contracts import ResponseArtifact
from pioneer.orchestrator.orchestrator import (
    OrchestratorContext,
    _context_note,
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
    try:
        return score_graph(graph, context.recipes, context.buildings, raw_item_ids=from_outside)
    except ValueError:
        return None


def _map_score(
    artifact: ResponseArtifact, context: OrchestratorContext, judge: TerrainJudge | None
) -> tuple[MapScore, ...] | None:
    if artifact.map_locations is None:
        return None
    return score_map(
        artifact.map_locations,
        context.resource_nodes,
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
