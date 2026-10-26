"""The FastAPI app. Everything it serves comes from what `create_app` is handed, so tests drive it
with fakes, no model and no socket.

- The context is asked afresh for every question; each answer keeps the context it was built from.
- The newest `_KEPT_RESPONSES` answers stay in memory for their graph and map pages; with a
  `ConversationStore`, answers and their pages are also kept in their conversation.
- `/api/ask/stream` answers like `/api/ask`, as one JSON line per event ending in the answer. It
  runs on a thread of its own, so an answer lands in its conversation even if the page has gone.
- A conversation's plan is read from the store, never taken from the page.
- Feedback is merged field by field, for any answer this server gave or the response log holds.
- The page's styles and scripts are served under `/static/`; icons, the map picture and its tiles
  under `/icons/`, `/map-image/` and `/map-tiles/`.
  With a `LiveContext`, `/api/status` and `web.sources`' endpoints show and steer its sources.

Each group of routes is a router of its own over `_Server`, what they share.
"""

from __future__ import annotations

import json
import queue
import threading
from collections import OrderedDict
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from pioneer.app import LiveContext
from pioneer.chat_presentation import render_message
from pioneer.common import raw_resource_ids
from pioneer.contracts import Feedback, Plan, ProductionGraph, ResponseArtifact
from pioneer.graph_presentation import render_page as render_graph_page
from pioneer.map_image import WORLD_BOUNDS, MapTiles
from pioneer.map_presentation import MapBackground, MapTileSet
from pioneer.map_presentation import render_page as render_map_page
from pioneer.orchestrator import (
    OrchestratorContext,
    OrchestratorUnavailable,
    ProgressSink,
    display_names,
    progress_of,
)
from pioneer.verification_feedback import (
    FeedbackStore,
    JudgeVerdict,
    MapScore,
    ResponseLog,
    ResponseScore,
)
from pioneer.web.assets import MEDIA_TYPES, STATIC_FILES
from pioneer.web.conversations import ConversationStore, NoConversations, PageKind, PlanChanged
from pioneer.web.page import render_chat_page
from pioneer.web.plans import (
    history_summary,
    marks_from_json,
    plan_from_json,
    plan_summary,
    plan_to_json,
)
from pioneer.web.sources import source_routes

ContextSource = Callable[[], tuple[OrchestratorContext, str]]
"""The context as of now, plus a one-line status saying what it was built from."""
History = Sequence[tuple[str, str]]
"""The conversation before a question, oldest first, as (question, answer) pairs."""


class Answer(Protocol):
    """Answers `question` in `context` after `history`; with `on_event`, says how it's going."""

    def __call__(
        self,
        question: str,
        context: OrchestratorContext,
        history: History,
        on_event: ProgressSink | None = None,
    ) -> ResponseArtifact | OrchestratorUnavailable: ...


Verify = Callable[[ResponseArtifact, OrchestratorContext], ResponseScore]

_CACHED_FOR_A_DAY = {"Cache-Control": "public, max-age=86400"}
_KEPT_RESPONSES = 200
_KEPT_TURNS = 8
"""How much earlier conversation a question may carry — the page sends at most this many turns."""


class Turn(BaseModel):
    question: str = Field(max_length=4000)
    answer: str = Field(max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list, max_length=_KEPT_TURNS)
    """The page's conversation so far, as the model is to see it."""
    conversation_id: str | None = Field(default=None, max_length=64)
    """Which stored conversation the answer goes into; none starts a new one."""
    turn: int | None = Field(default=None, ge=0)
    """Which of its turns the answer is: an earlier one when it's answered again. None: a new one
    at its end."""


class AcceptPlanRequest(BaseModel):
    response_id: str = Field(min_length=1, max_length=64)
    """The answer whose proposed plan becomes the conversation's."""


class MarkRequest(BaseModel):
    recipe_id: str = Field(min_length=1, max_length=200)
    """The plan's stage, by the recipe it runs."""
    built: float = Field(ge=0, le=100_000)
    """How many of its machines the player marked built; 0 clears the mark."""


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
    context: ContextSource | None = None,
    live: LiveContext | None = None,
    answer: Answer,
    verify: Verify | None = None,
    feedback_store: FeedbackStore | None = None,
    response_log: ResponseLog | None = None,
    icon_dir: Path | None = None,
    conversations: ConversationStore | None = None,
    map_image: Path | None = None,
    map_tiles: MapTiles | None = None,
) -> FastAPI:
    """`live`, when given, is where `context` comes from, and the header's panels show and steer
    its sources; without it, `context` alone says what the assistant knows."""
    if context is None:
        if live is None:
            raise ValueError("create_app needs a context source or a LiveContext")
        context = live.current
    # The knowledge base's names and icons: they don't change.
    first = context()[0]
    with_icons = _icon_ids(icon_dir)
    icons = icon_urls(first, with_icons)
    picture = map_image if map_image is not None and map_image.is_file() else None
    tile_set = (
        MapTileSet("/map-tiles", map_tiles.tile_size, map_tiles.overlap, map_tiles.levels)
        if map_tiles is not None
        else None
    )
    background = (
        MapBackground(
            f"/map-image/{picture.name}" if picture else None, *WORLD_BOUNDS, tiles=tile_set
        )
        if picture or tile_set
        else None
    )
    server = _Server(
        context=context,
        live=live,
        answer=answer,
        verify=verify,
        feedback=feedback_store if feedback_store is not None else FeedbackStore(),
        response_log=response_log,
        conversations=conversations if conversations is not None else NoConversations(),
        names=display_names(first),
        icons=icons,
        chat_icons=named_icons(first, icons),
        raw_resources=(
            raw_resource_ids(first.items)
            if first.items
            else None  # no knowledge base: which inputs are mined can't be told
        ),
        products={  # what a graph's stage makes, and how much of it one machine does a minute
            recipe.recipe_id: (recipe.outputs[0].item_id, recipe.outputs[0].amount_per_minute)
            for recipe in first.recipes
            if recipe.outputs
        },
        background=background,
        known_ids=set(response_log.response_ids()) if response_log is not None else set(),
    )

    app = FastAPI(title="Pioneer")
    app.include_router(_page_routes(server))
    app.include_router(
        source_routes(
            live,
            server.status,
            names=server.names,
            icons=icons,
            map_picture=background.href if background else None,
            map_tiles=tile_set.as_json() if tile_set else None,
            world=WORLD_BOUNDS if background else None,
        )
    )
    app.include_router(_ask_routes(server))
    app.include_router(_conversation_routes(server))
    app.include_router(_response_routes(server))
    app.include_router(_file_routes(icon_dir, with_icons, picture, map_tiles))
    return app


@dataclass
class _Server:
    """What the routes share: what `create_app` was handed, what's worked out once from the
    knowledge base, and the answers kept in memory -- those under `lock`, since FastAPI runs the
    sync handlers on a thread pool."""

    context: ContextSource
    live: LiveContext | None
    answer: Answer
    verify: Verify | None
    feedback: FeedbackStore
    response_log: ResponseLog | None
    conversations: ConversationStore | NoConversations
    names: dict[str, str]
    icons: dict[str, str]
    chat_icons: dict[str, str]
    raw_resources: frozenset[str] | None
    products: dict[str, tuple[str, float]]
    background: MapBackground | None
    known_ids: set[str]
    """Every answer feedback may be given on: this server's, and the response log's."""
    answered: OrderedDict[str, AnsweredQuestion] = field(default_factory=OrderedDict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def status(self) -> dict[str, Any]:
        """The one-line status, and with a `LiveContext`, what it's made of, source by source."""
        if self.live is None:
            return {"status": self.context()[1], "sources": None}
        sources = self.live.sources()
        return {"status": sources.summary(), "sources": asdict(sources)}

    def find(self, response_id: str) -> AnsweredQuestion:
        with self.lock:
            found = self.answered.get(response_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown response id")
        return found

    def page_of(self, response_id: str, kind: PageKind) -> AnsweredQuestion | str:
        """The answer still in memory, else its page as its conversation kept it."""
        with self.lock:
            found = self.answered.get(response_id)
            stored = self.conversations.page(response_id, kind) if found is None else None
        return stored if stored is not None else found or self.find(response_id)

    def render_graph(self, graph: ProductionGraph) -> str:
        return render_graph_page(
            graph,
            title="Production graph",
            names=self.names,
            icons=self.icons,
            raw_resources=self.raw_resources,
            products=self.products,
        )

    def render_map(self, artifact: ResponseArtifact, context: OrchestratorContext) -> str:
        return _render_map(artifact, context, self.names, self.icons, self.background)

    def conversation_plan(self, conversation_id: str | None) -> Plan | None:
        """The plan the conversation has, as the model is to work from it."""
        if conversation_id is None:
            return None
        with self.lock:
            accepted = self.conversations.plan(conversation_id)
        return _plan_of(accepted)

    def reserved_elsewhere(self, conversation_id: str | None) -> dict[str, Any]:
        """What the plans of every other conversation have reserved -- the deposits they work and
        what they take of the factories' spare output -- as the context's fields take it: not to
        be offered to a plan in this one."""
        with self.lock:
            accepted = self.conversations.accepted_plans()
        others = [
            plan
            for other, kept in accepted.items()
            if other != conversation_id and (plan := _plan_of(kept)) is not None
        ]
        return {
            "reserved_node_ids": frozenset(
                deposit.resource_node_id
                for plan in others
                if plan.sites is not None
                for deposit in plan.sites.deposits
            ),
            "reserved_supply": tuple(r for plan in others for r in plan.reservations),
        }

    def accepted_summary(self, accepted: dict[str, Any] | None) -> dict[str, Any] | None:
        """What the page shows of a conversation's plan -- with how far the save in use has
        built it, where it's built, and the revisions it went through -- and which answer
        proposed it."""
        plan = _plan_of(accepted)
        if accepted is None or plan is None:
            return None
        current = self.context()[0]
        progress = progress_of(plan, current)
        as_of = self.live.sources().save.saved_at if self.live is not None else None
        worked = (
            {p.resource_node_id for p in current.existing_placements if p.resource_node_id}
            if current.existing_graph is not None
            else None
        )
        summary = plan_summary(
            plan, self.names, progress, as_of=as_of, icons=self.icons, worked_nodes=worked
        )
        return {
            **summary,
            "response_id": accepted.get("response_id"),
            "accepted_at": accepted.get("accepted_at"),
            "history": history_summary(accepted.get("history"), self.names),
        }

    def respond(
        self, request: AskRequest, question: str, on_event: ProgressSink | None = None
    ) -> dict[str, Any]:
        """Answers, verifies, remembers and stores one question: the body both endpoints send."""
        current, status = self.context()
        plan = self.conversation_plan(request.conversation_id)
        current = replace(current, plan=plan, **self.reserved_elsewhere(request.conversation_id))
        history = [(turn.question, turn.answer) for turn in request.history]
        steps: list[str] = []
        thoughts: list[str] = []

        def record(event: dict[str, Any]) -> None:
            """Passes each event on, keeping the tools run and the model's reasoning."""
            if event.get("type") == "tool":
                steps.append(str(event.get("text", "")))
            elif event.get("type") == "thinking":
                thoughts.append(str(event.get("text", "")))
            if on_event is not None:
                on_event(event)

        if on_event is None:
            result = self.answer(question, current, history)
        else:
            result = self.answer(question, current, history, on_event=record)
        if isinstance(result, OrchestratorUnavailable):
            raise HTTPException(status_code=503, detail=result.reason)
        if self.verify is not None and on_event is not None:
            on_event({"type": "status", "text": "Checking the answer"})
        score = self.verify(result, current) if self.verify is not None else None

        base = f"/responses/{result.response_id}"
        body: dict[str, Any] = {
            "response_id": result.response_id,
            "chat": result.chat,
            "chat_html": render_message(result.chat, icons=self.chat_icons),
            "graph_url": f"{base}/graph" if result.graph is not None else None,
            "map_url": f"{base}/map" if _has_map(result) else None,
            "verification": _verification_summary(score),
            # Recipes the player turned off that a plan would need: the page offers them.
            "missing_recipes": list(result.missing_recipes),
            # A plan the player can accept as the conversation's: only where it can be kept.
            "plan_proposal": (
                plan_summary(result.plan_proposal, self.names)
                if result.plan_proposal is not None and self.conversations.keeps
                else None
            ),
            # How it was worked out, for the page to show folded above the answer.
            "process": (
                {"steps": steps, "thinking": "".join(thoughts)} if steps or thoughts else None
            ),
        }
        conversation_id = self._remember(
            AnsweredQuestion(artifact=result, score=score, context=current), request, question, body
        )
        return {**body, "status": status, "conversation_id": conversation_id}

    def _remember(
        self, answered: AnsweredQuestion, request: AskRequest, question: str, body: dict[str, Any]
    ) -> str | None:
        """Keeps an answer in memory, the response log and its conversation, whose id it returns
        (`None` when none are kept)."""
        result = answered.artifact
        # Drawn now, while this answer's context is at hand, for its conversation to keep.
        pages: dict[PageKind, str] = {}
        if self.conversations.keeps and result.graph is not None:
            pages["graph"] = self.render_graph(result.graph)
        if self.conversations.keeps and _has_map(result):
            pages["map"] = self.render_map(result, answered.context)

        with self.lock:
            self.answered[result.response_id] = answered
            self.known_ids.add(result.response_id)
            while len(self.answered) > _KEPT_RESPONSES:
                self.answered.popitem(last=False)
            if self.response_log is not None:
                self.response_log.append(result, answered.score)
            if not self.conversations.keeps:
                return None
            for kind, page in pages.items():
                self.conversations.save_page(result.response_id, kind, page)
            turn = request.turn if request.turn is not None else len(request.history)
            proposal = result.plan_proposal and plan_to_json(result.plan_proposal)
            return self.conversations.save_turn(
                request.conversation_id, turn, question, body, proposal
            )


def _page_routes(server: _Server) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def chat_page() -> str:
        now = server.status()
        return render_chat_page(status=now["status"], sources=now["sources"])

    @router.get("/api/status")
    def api_status() -> dict[str, Any]:
        return server.status()

    return router


def _ask_routes(server: _Server) -> APIRouter:
    router = APIRouter()

    @router.post("/api/ask")
    def ask(request: AskRequest) -> dict[str, Any]:
        return server.respond(request, _question_of(request))

    @router.post("/api/ask/stream")
    def ask_stream(request: AskRequest) -> StreamingResponse:
        question = _question_of(request)
        events: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def work() -> None:
            try:
                events.put({"type": "answer", **server.respond(request, question, events.put)})
            except HTTPException as error:
                events.put({"type": "error", "detail": error.detail})
            except Exception as error:  # the page is waiting on this stream: say so, don't hang
                events.put({"type": "error", "detail": f"{type(error).__name__}: {error}"})
            finally:
                events.put(None)

        def lines() -> Iterator[str]:
            while (event := events.get()) is not None:
                yield json.dumps(event, ensure_ascii=False) + "\n"

        threading.Thread(target=work, daemon=True).start()
        return StreamingResponse(lines(), media_type="application/x-ndjson")

    return router


def _conversation_routes(server: _Server) -> APIRouter:
    router = APIRouter()
    conversations = server.conversations

    @router.get("/api/conversations")
    def list_conversations() -> list[dict[str, Any]]:
        with server.lock:
            return conversations.list()

    @router.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str) -> dict[str, Any]:
        with server.lock:
            found = conversations.get(conversation_id)
            accepted = conversations.plan(conversation_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown conversation")
        return {
            **found,
            # The proposals themselves stay on the server: each answer says what it proposes.
            "turns": [
                {key: value for key, value in turn.items() if key != "plan_proposal"}
                for turn in found["turns"]
            ],
            "plan": server.accepted_summary(accepted),
        }

    @router.get("/api/conversations/{conversation_id}/plan")
    def get_plan(conversation_id: str) -> dict[str, Any]:
        with server.lock:
            found = conversations.get(conversation_id)
            accepted = conversations.plan(conversation_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown conversation")
        return {"plan": server.accepted_summary(accepted)}

    @router.post("/api/conversations/{conversation_id}/plan/marks")
    def mark_plan(conversation_id: str, request: MarkRequest) -> dict[str, Any]:
        with server.lock:
            accepted = conversations.plan(conversation_id)
        plan = _plan_of(accepted)
        if plan is None:
            raise HTTPException(status_code=404, detail="this conversation has no plan")
        if all(node.recipe_id != request.recipe_id for node in plan.graph.nodes):
            raise HTTPException(status_code=422, detail="no stage of the plan runs this recipe")
        with server.lock:
            marked = conversations.mark_plan(conversation_id, request.recipe_id, request.built)
        return {"plan": server.accepted_summary(marked)}

    @router.post("/api/conversations/{conversation_id}/plan")
    def accept_plan(conversation_id: str, request: AcceptPlanRequest) -> dict[str, Any]:
        with server.lock:
            try:
                accepted = conversations.accept_plan(conversation_id, request.response_id)
            except PlanChanged:
                raise HTTPException(
                    status_code=409, detail="the plan has changed since this was proposed"
                ) from None
        if accepted is None:
            raise HTTPException(status_code=404, detail="no plan proposed by this answer")
        return {"plan": server.accepted_summary(accepted)}

    @router.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str) -> dict[str, bool]:
        with server.lock:
            deleted = conversations.delete(conversation_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="unknown conversation")
        return {"deleted": True}

    return router


def _response_routes(server: _Server) -> APIRouter:
    router = APIRouter()

    @router.get("/responses/{response_id}/graph", response_class=HTMLResponse)
    def graph_page(response_id: str) -> str:
        found = server.page_of(response_id, "graph")
        if isinstance(found, str):
            return found
        if found.artifact.graph is None:
            raise HTTPException(status_code=404, detail="this answer has no production graph")
        return server.render_graph(found.artifact.graph)

    @router.get("/responses/{response_id}/map", response_class=HTMLResponse)
    def map_page(response_id: str) -> str:
        found = server.page_of(response_id, "map")
        if isinstance(found, str):
            return found
        if not _has_map(found.artifact):
            raise HTTPException(status_code=404, detail="this answer has no map")
        return server.render_map(found.artifact, found.context)

    @router.post("/api/responses/{response_id}/feedback")
    def record_feedback(response_id: str, request: FeedbackRequest) -> dict[str, Any]:
        with server.lock:
            if response_id not in server.known_ids:
                raise HTTPException(status_code=404, detail="unknown response id")
            merged = replace(
                server.feedback.get(response_id) or Feedback(),
                **request.model_dump(exclude_none=True),
            )
            server.feedback.record(response_id, merged)
        return asdict(merged)

    return router


def _file_routes(
    icon_dir: Path | None,
    with_icons: frozenset[str],
    picture: Path | None,
    map_tiles: MapTiles | None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/static/{name}")
    def static_file(name: str) -> FileResponse:
        path = STATIC_FILES.get(name)  # never a path the request made up
        if path is None:
            raise HTTPException(status_code=404, detail="no such file")
        # The page asks for it by a URL carrying a hash of its content: it never goes stale.
        return FileResponse(
            path,
            media_type=MEDIA_TYPES[path.suffix],
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @router.get("/map-image/{name}")
    def map_picture(name: str) -> FileResponse:
        if picture is None or name != picture.name:
            raise HTTPException(status_code=404, detail="no such map image")
        return FileResponse(picture, headers=_CACHED_FOR_A_DAY)

    @router.get("/map-tiles/{level:int}/{column:int}_{row:int}.webp")
    def map_tile(level: int, column: int, row: int) -> FileResponse:
        tile = map_tiles.path(level, column, row) if map_tiles is not None else None
        if tile is None:
            raise HTTPException(status_code=404, detail="no such map tile")
        return FileResponse(tile, media_type="image/webp", headers=_CACHED_FOR_A_DAY)

    @router.get("/icons/{class_id}.png")
    def icon(class_id: str) -> FileResponse:
        if icon_dir is None or class_id not in with_icons:  # never a path the page made up
            raise HTTPException(status_code=404, detail="no icon for this id")
        return FileResponse(icon_dir / f"{class_id}.png", headers=_CACHED_FOR_A_DAY)

    return router


def _question_of(request: AskRequest) -> str:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="the question is empty")
    return question


def _plan_of(accepted: dict[str, Any] | None) -> Plan | None:
    """A conversation's plan as stored, with what the player marked built; `None` without one,
    or with one that can't be read."""
    if accepted is None:
        return None
    try:
        plan = plan_from_json(accepted.get("plan"))
    except ValueError:
        return None
    return replace(plan, marked_built=marks_from_json(accepted.get("marked")))


def _icon_ids(icon_dir: Path | None) -> frozenset[str]:
    if icon_dir is None or not icon_dir.is_dir():
        return frozenset()
    return frozenset(path.stem for path in icon_dir.glob("*.png"))


def icon_urls(context: OrchestratorContext, with_icons: Collection[str]) -> dict[str, str]:
    """The icon URL of every id that has one, by id: each item, building or belt/pipe tier with
    an icon of its own, and each recipe whose first product has one — the item it's run for."""
    icons = {class_id: f"/icons/{class_id}.png" for class_id in with_icons}
    for recipe in context.recipes:
        if recipe.outputs and recipe.outputs[0].item_id in icons:
            icons.setdefault(recipe.recipe_id, icons[recipe.outputs[0].item_id])
    return icons


def named_icons(context: OrchestratorContext, icons: dict[str, str]) -> dict[str, str]:
    """The icon URL by in-game name, for the chat to put next to the names it mentions. Items,
    buildings and belt/pipe tiers only: a recipe is usually named after its item, which already
    has the icon, and an alternate's name ("Cast Screw") is not the item the player gets."""
    named = [(item.item_id, item.name) for item in context.items]
    named += [(building.building_id, building.name) for building in context.buildings]
    named += [(tier.building_id, tier.name) for tier in context.transport_tiers]
    by_name: dict[str, str] = {}
    for class_id, name in named:
        if class_id in icons:
            by_name.setdefault(name, icons[class_id])
    return by_name


def _has_map(artifact: ResponseArtifact) -> bool:
    return artifact.map_locations is not None or bool(artifact.factory_sites)


def _render_map(
    artifact: ResponseArtifact,
    context: OrchestratorContext,
    names: dict[str, str],
    icons: dict[str, str],
    background: MapBackground | None = None,
) -> str:
    """The recommended sites, every node of the same resource, the player's extractors and the
    factories the answer points at — the rest of a real save's thousands of buildings would bury
    the pins. On the game's map, when there's a picture of it."""
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
        icons=icons,
        factory_sites=artifact.factory_sites or (),
        background=background,
        site_plan=artifact.build_sites,
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
