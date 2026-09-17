"""Verification & feedback scoring module (implementation.md Stage 15).

Pure scoring functions over a `ResponseArtifact`: RAG-consistency, distance-from-optimum,
LLM-as-a-judge hooks, and feedback capture — in memory, or kept in JSON-lines files alongside a log
of every answer. Depends only on `pioneer.contracts` and the Stage 4 Verifier (explicitly allowed by
implementation.md — a finished function library, not a live integration); tested against hand-built
fixture artifacts with known-correct expected scores.
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
