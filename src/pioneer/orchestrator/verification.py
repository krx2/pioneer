"""The Orchestrator's verification step: scores a finished `ResponseArtifact` with
`verification_feedback.score_response`, against the same data it was built from. Kept out of
`handle_query` so the caller decides when to pay for it: the judges cost a model call each.
"""

from __future__ import annotations

from pioneer.common import raw_resource_ids
from pioneer.contracts import ResponseArtifact
from pioneer.orchestrator.base import OrchestratorContext
from pioneer.orchestrator.existing_factory import reference_point, spare_power_mw
from pioneer.orchestrator.orchestrator import context_note
from pioneer.verification_feedback import ChatJudge, ResponseScore, TerrainJudge, score_response


def verify_response(
    artifact: ResponseArtifact,
    context: OrchestratorContext,
    *,
    chat_judge: ChatJudge | None = None,
    terrain_judge: TerrainJudge | None = None,
) -> ResponseScore:
    return score_response(
        artifact,
        cited_passages=artifact.grounding,
        recipes=context.recipes,
        buildings=context.buildings,
        available_power_mw=spare_power_mw(context),
        raw_item_ids=raw_resource_ids(context.items),
        resource_nodes=context.resource_nodes,
        placements=context.existing_placements,
        chat_judge=chat_judge,
        terrain_judge=terrain_judge,
        chat_context=context_note(context),
        terrain_context=_terrain_context(context),
    )


def _terrain_context(context: OrchestratorContext) -> str:
    """What a suggested build site is judged in: how much the player has built, and where."""
    placements = context.existing_placements
    if not placements:
        return "Nothing is known about what the player has built (no save loaded)."
    base = reference_point({}, context)
    return (
        f"The player has {len(placements)} buildings, centred around "
        f"x={base.x:.0f} y={base.y:.0f} z={base.z:.0f} (centimetres)."
    )
