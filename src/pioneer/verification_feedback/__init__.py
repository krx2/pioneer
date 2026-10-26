"""Verification & feedback: scoring a `ResponseArtifact` (grounding, distance from optimum,
LLM-as-a-judge hooks), and feedback capture — in memory, or in JSON-lines files beside a log of
every answer.
"""

from pioneer.verification_feedback.feedback import FeedbackStore, JsonlFeedbackStore, ResponseLog
from pioneer.verification_feedback.scoring import (
    ChatJudge,
    ChatScore,
    GraphScore,
    JudgeVerdict,
    MapScore,
    ResponseScore,
    TerrainJudge,
    check_rag_consistency,
    score_graph,
    score_map,
    score_response,
)

__all__ = [
    "ChatJudge",
    "ChatScore",
    "FeedbackStore",
    "GraphScore",
    "JsonlFeedbackStore",
    "JudgeVerdict",
    "MapScore",
    "ResponseLog",
    "ResponseScore",
    "TerrainJudge",
    "check_rag_consistency",
    "score_graph",
    "score_map",
    "score_response",
]
