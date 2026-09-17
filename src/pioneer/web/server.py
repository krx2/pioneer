"""The FastAPI app. Everything it serves comes from what `create_app` is handed — `context` says
what the assistant knows right now, `answer` runs the Orchestrator on it, `verify` scores the
result against the same context, the stores keep feedback and a response log — so tests drive it
with fakes, no model and no socket. `context` is asked afresh for every question and status, so a
newer save shows up without a restart (see `app.LiveContext`); each answer keeps the context it
was built from, and its map is drawn from that.

Answers are kept in memory (the most recent `_KEPT_RESPONSES`) so their graph and map pages can be
served after the fact; feedback goes to the feedback store, merged field by field, since the page
sends each button press on its own. Feedback is taken for any answer this server gave or the
response log holds — also once its pages have been dropped from memory, or after a restart.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from pioneer.chat_presentation import render_message
from pioneer.contracts import Feedback, ResponseArtifact
from pioneer.graph_presentation import render_page as render_graph_page
from pioneer.map_presentation import render_page as render_map_page
from pioneer.orchestrator import OrchestratorContext, OrchestratorUnavailable, display_names
from pioneer.verification_feedback import (
    FeedbackStore,
    JudgeVerdict,
    MapScore,
    ResponseLog,
    ResponseScore,
)
from pioneer.web.page import render_chat_page

ContextSource = Callable[[], tuple[OrchestratorContext, str]]
"""The context as of now, plus a one-line status saying what it was built from."""
History = Sequence[tuple[str, str]]
"""The conversation before a question, oldest first, as (question, answer) pairs."""
Answer = Callable[[str, OrchestratorContext, History], ResponseArtifact | OrchestratorUnavailable]
Verify = Callable[[ResponseArtifact, OrchestratorContext], ResponseScore]

_KEPT_RESPONSES = 200
_KEPT_TURNS = 8
"""How much earlier conversation a question may carry — the page sends at most this many turns."""


class Turn(BaseModel):
    question: str = Field(max_length=4000)
    answer: str = Field(max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list, max_length=_KEPT_TURNS)
    """The page's conversation so far. The server keeps none: each tab is its own conversation."""


class FeedbackRequest(BaseModel):
    thumbs_up: bool | None = None
    applied_plan: bool | None = None
    built_at_location: bool | None = None
    qualitative_score: int | None = Field(default=None, ge=1, le=5)


@dataclass(frozen=True)
class AnsweredQuestion:
    artifact: ResponseArtifact
    score: ResponseScore | None
    context: OrchestratorContext


def create_app(
    *,
    context: ContextSource,
    answer: Answer,
    verify: Verify | None = None,
    feedback_store: FeedbackStore | None = None,
    response_log: ResponseLog | None = None,
) -> FastAPI:
    app = FastAPI(title="Pioneer")
    feedback = feedback_store if feedback_store is not None else FeedbackStore()
    answered: OrderedDict[str, AnsweredQuestion] = OrderedDict()
    lock = threading.Lock()  # FastAPI runs these sync handlers on a thread pool
    names = display_names(context()[0])  # the knowledge base's names: they don't change
    known_ids = set(response_log.response_ids()) if response_log is not None else set()

    def find(response_id: str) -> AnsweredQuestion:
        with lock:
            found = answered.get(response_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown response id")
        return found

    @app.get("/", response_class=HTMLResponse)
    def chat_page() -> str:
        return render_chat_page(status=context()[1])

    @app.get("/api/status")
    def api_status() -> dict[str, str]:
        return {"status": context()[1]}

    @app.post("/api/ask")
    def ask(request: AskRequest) -> dict[str, Any]:
        question = request.question.strip()
        if not question:
            raise HTTPException(status_code=422, detail="the question is empty")
        current, status = context()
        history = [(turn.question, turn.answer) for turn in request.history]
        result = answer(question, current, history)
        if isinstance(result, OrchestratorUnavailable):
            raise HTTPException(status_code=503, detail=result.reason)
        score = verify(result, current) if verify is not None else None

        with lock:
            answered[result.response_id] = AnsweredQuestion(
                artifact=result, score=score, context=current
            )
            known_ids.add(result.response_id)
            while len(answered) > _KEPT_RESPONSES:
                answered.popitem(last=False)
            if response_log is not None:
                response_log.append(result, score)

        base = f"/responses/{result.response_id}"
        return {
            "response_id": result.response_id,
            "chat": result.chat,
            "chat_html": render_message(result.chat),
            "graph_url": f"{base}/graph" if result.graph is not None else None,
            "map_url": f"{base}/map" if _has_map(result) else None,
            "verification": _verification_summary(score),
            "status": status,
        }

    @app.get("/responses/{response_id}/graph", response_class=HTMLResponse)
    def graph_page(response_id: str) -> str:
        graph = find(response_id).artifact.graph
        if graph is None:
            raise HTTPException(status_code=404, detail="this answer has no production graph")
        return render_graph_page(graph, title="Production graph", names=names)

    @app.get("/responses/{response_id}/map", response_class=HTMLResponse)
    def map_page(response_id: str) -> str:
        found = find(response_id)
        if not _has_map(found.artifact):
            raise HTTPException(status_code=404, detail="this answer has no map")
        return _render_map(found.artifact, found.context, names)

    @app.post("/api/responses/{response_id}/feedback")
    def record_feedback(response_id: str, request: FeedbackRequest) -> dict[str, Any]:
        with lock:
            if response_id not in known_ids:
                raise HTTPException(status_code=404, detail="unknown response id")
            merged = replace(
                feedback.get(response_id) or Feedback(), **request.model_dump(exclude_none=True)
            )
            feedback.record(response_id, merged)
        return asdict(merged)

    return app


def _has_map(artifact: ResponseArtifact) -> bool:
    return artifact.map_locations is not None or bool(artifact.factory_sites)


def _render_map(
    artifact: ResponseArtifact, context: OrchestratorContext, names: dict[str, str]
) -> str:
    """The recommended sites, every node of the same resource, the player's extractors and the
    factories the answer points at — the rest of a real save's thousands of buildings would bury
    the pins."""
    ranked = artifact.map_locations or ()
    nodes_by_id = {node.node_id: node for node in context.resource_nodes}
    resources = {
        nodes_by_id[location.resource_node_id].item_id
        for location in ranked
        if location.resource_node_id in nodes_by_id
    }
    nodes = tuple(node for node in context.resource_nodes if node.item_id in resources)
    extractors = tuple(p for p in context.existing_placements if p.resource_node_id is not None)
    return render_map_page(
        nodes,
        extractors,
        ranked,
        title="Factory map",
        names=names,
        factory_sites=artifact.factory_sites or (),
    )


def _verification_summary(score: ResponseScore | None) -> dict[str, Any] | None:
    if score is None:
        return None
    summary: dict[str, Any] = {}
    if score.chat is not None:
        summary["chat"] = {
            "grounded_fraction": round(score.chat.grounded_fraction, 2),
            "consistent": score.chat.consistent,
            "ungrounded_numbers": list(score.chat.ungrounded_numbers),
            "judge": _verdict(score.chat.judge_verdict),
        }
    if score.graph is not None:
        graph = score.graph
        deviation = graph.deviation_from_optimum_pct
        summary["graph"] = {
            "passed": graph.passed,
            "balanced": graph.balanced,
            "power_ok": graph.power_ok,
            "power_draw_mw": round(graph.power_draw_mw, 1),
            "spare_power_mw": (
                round(graph.available_power_mw, 1) if graph.available_power_mw is not None else None
            ),
            "over_optimum_pct": round(deviation, 1) if deviation is not None else None,
        }
    if score.map is not None:
        summary["map"] = {
            "passed": all(site.passed for site in score.map),
            "checked": len(score.map),
            "problems": _site_problems(score.map),
            "judge": [_verdict(site.judge_verdict) for site in score.map],
        }
    return summary


def _site_problems(sites: tuple[MapScore, ...]) -> list[str]:
    """Which checks some suggested site failed, in words for the page's badge."""
    failed = {
        "not where the node is": any(not site.position_ok for site in sites),
        "wrong purity": any(not site.purity_ok for site in sites),
        "wrong distance": any(site.distance_ok is False for site in sites),
        "already taken": any(not site.still_free for site in sites),
    }
    return [problem for problem, found in failed.items() if found]


def _verdict(verdict: JudgeVerdict | None) -> dict[str, Any] | None:
    return asdict(verdict) if verdict is not None else None
