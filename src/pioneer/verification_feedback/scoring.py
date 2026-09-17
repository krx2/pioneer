"""Scoring functions from architecture.md §6, over a `ResponseArtifact`'s three channels
(implementation.md Stage 15).

Each channel mixes deterministic checks with, where architecture.md calls for it, an injectable
"judge" hook — the same transport-injection pattern `qa_engine.engine` and `server_client.client`
use for their own external calls: the caller supplies a plain callable, so the real LLM-as-a-judge
call (Stage 16's job) never needs to exist for these functions to be fully unit-tested.

- Chat: `check_rag_consistency` is fully deterministic (grounding is a fact-check, not a matter of
  taste, so it doesn't need a judge) — architecture.md's own three-way split reserves LLM-as-a-judge
  for *subjective* fit, never arithmetic-or-fact correctness.
- Graph: `score_graph` calls the real Stage 4 Verifier directly — implementation.md Stage 15
  explicitly allows this ("just calling a finished function library, not a live integration").
- Map: `score_map` deterministically checks distance/purity against the real resource data, plus an
  optional `TerrainJudge` hook for the subjective "does this fit the terrain/existing infra" call.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from pioneer.contracts import (
    Building,
    ProductionGraph,
    RankedLocation,
    Recipe,
    ResourceNode,
    ResponseArtifact,
)
from pioneer.verifier import balance, distance, power_balance

# --- Shared ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeVerdict:
    """An LLM-as-a-judge's call on whether an answer fits its context — advisory, never gating."""

    fits_context: bool
    rationale: str


# --- Chat channel --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    """
    a an and are as at be by can do for from how i in into is it made of on or than that the
    their there this to what when where which who why with you your
    """.split()
)


def _significant_tokens(text: str) -> Counter[str]:
    tokens = (t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS)
    return Counter(tokens)


@dataclass(frozen=True)
class ChatScore:
    grounded_fraction: float
    """Fraction (0-1) of the chat's distinct significant words that appear in at least one cited
    passage. `1.0` for an empty/all-stopword chat — there's nothing ungrounded to flag."""
    consistent: bool
    qualitative_score: int | None = None
    """Player feedback's 1-5 rating (`Feedback.qualitative_score`), carried through unchanged —
    this function never invents one."""
    judge_verdict: JudgeVerdict | None = None
    """architecture.md §6's Chat-channel LLM-as-a-judge call: does the answer suit the player's
    question, context and game phase. `None` when no judge ran, or it gave no usable verdict."""


class ChatJudge(Protocol):
    """Rates whether `answer` suits the player's `question` and situation (`context`) —
    architecture.md's Chat-channel LLM-as-a-judge step (the real one is
    `llm_client.judges.chat_judge`). `None` means it gave no usable verdict."""

    def __call__(self, question: str, answer: str, context: str) -> JudgeVerdict | None: ...


def check_rag_consistency(
    chat_text: str, cited_passages: Sequence[str], *, threshold: float = 0.6
) -> ChatScore:
    chat_words = set(_significant_tokens(chat_text))
    if not chat_words:
        return ChatScore(grounded_fraction=1.0, consistent=True)

    passage_words: set[str] = set()
    for passage in cited_passages:
        passage_words.update(_significant_tokens(passage))

    grounded = chat_words & passage_words
    fraction = len(grounded) / len(chat_words)
    return ChatScore(grounded_fraction=fraction, consistent=fraction >= threshold)


# --- Graph channel -------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphScore:
    balanced: bool
    """No item (other than a declared raw input) is consumed faster than it's produced."""
    power_ok: bool
    """`True` when no power budget was given, or the graph's net draw fits within it."""
    deviation_from_optimum_pct: float | None
    """`sum(|actual - optimal| machine count per recipe) / optimal total * 100`; `None` when no
    optimal graph was supplied to compare against."""
    passed: bool


def _machines_by_recipe(graph: ProductionGraph) -> dict[str, float]:
    totals: dict[str, float] = {}
    for node in graph.nodes:
        totals[node.recipe_id] = totals.get(node.recipe_id, 0.0) + node.machine_count
    return totals


def _deviation_from_optimum(graph: ProductionGraph, optimal_graph: ProductionGraph) -> float:
    actual = _machines_by_recipe(graph)
    optimal = _machines_by_recipe(optimal_graph)
    optimal_total = sum(optimal.values())
    if optimal_total <= 0:
        return 0.0
    all_recipes = actual.keys() | optimal.keys()
    deviation = sum(abs(actual.get(r, 0.0) - optimal.get(r, 0.0)) for r in all_recipes)
    return deviation / optimal_total * 100.0


def score_graph(
    graph: ProductionGraph,
    recipes: tuple[Recipe, ...],
    buildings: tuple[Building, ...],
    *,
    optimal_graph: ProductionGraph | None = None,
    available_power_mw: float | None = None,
    raw_item_ids: Collection[str] = (),
    rate_tolerance: float = 1e-6,
) -> GraphScore:
    net = balance(graph, recipes)
    deficits = {
        item: rate
        for item, rate in net.items()
        if rate < -rate_tolerance and item not in raw_item_ids
    }
    balanced = not deficits

    power_draw = power_balance(graph, buildings)
    power_ok = available_power_mw is None or power_draw <= available_power_mw + rate_tolerance

    deviation_pct = (
        _deviation_from_optimum(graph, optimal_graph) if optimal_graph is not None else None
    )

    return GraphScore(
        balanced=balanced,
        power_ok=power_ok,
        deviation_from_optimum_pct=deviation_pct,
        passed=balanced and power_ok,
    )


# --- Map channel ---------------------------------------------------------------------------


class TerrainJudge(Protocol):
    """Scores whether `location` fits the player's terrain/logistics/existing-infra context —
    architecture.md's Map-channel LLM-as-a-judge step (the real one is
    `llm_client.judges.terrain_judge`). `None` means it gave no usable verdict."""

    def __call__(self, location: RankedLocation, context: str) -> JudgeVerdict | None: ...


@dataclass(frozen=True)
class MapScore:
    resource_node_id: str
    distance_ok: bool
    """`True` when the location's claimed distance matches the real resource node's position,
    within `distance_tolerance`."""
    purity_ok: bool
    """`True` when the location's claimed purity matches the real resource node's."""
    judge_verdict: JudgeVerdict | None
    passed: bool
    """`distance_ok and purity_ok` — the judge's verdict is advisory/qualitative, never gating."""


def score_map(
    locations: Sequence[RankedLocation],
    resource_nodes: Sequence[ResourceNode],
    *,
    distance_tolerance: float = 1.0,
    judge: TerrainJudge | None = None,
    judge_context: str = "",
) -> tuple[MapScore, ...]:
    nodes_by_id = {node.node_id: node for node in resource_nodes}
    scores = []
    for location in locations:
        node = nodes_by_id.get(location.resource_node_id)
        if node is None:
            scores.append(
                MapScore(
                    resource_node_id=location.resource_node_id,
                    distance_ok=False,
                    purity_ok=False,
                    judge_verdict=None,
                    passed=False,
                )
            )
            continue

        distance_ok = distance(location.position, node.position) <= distance_tolerance
        purity_ok = location.purity == node.purity
        verdict = judge(location, judge_context) if judge is not None else None
        scores.append(
            MapScore(
                resource_node_id=location.resource_node_id,
                distance_ok=distance_ok,
                purity_ok=purity_ok,
                judge_verdict=verdict,
                passed=distance_ok and purity_ok,
            )
        )
    return tuple(scores)


# --- Combined response score ----------------------------------------------------------------


@dataclass(frozen=True)
class ResponseScore:
    chat: ChatScore | None = None
    graph: GraphScore | None = None
    map: tuple[MapScore, ...] | None = None


def score_response(
    artifact: ResponseArtifact,
    *,
    cited_passages: Sequence[str] = (),
    recipes: tuple[Recipe, ...] = (),
    buildings: tuple[Building, ...] = (),
    optimal_graph: ProductionGraph | None = None,
    available_power_mw: float | None = None,
    raw_item_ids: Collection[str] = (),
    resource_nodes: Sequence[ResourceNode] = (),
    distance_tolerance: float = 1.0,
    terrain_judge: TerrainJudge | None = None,
    chat_judge: ChatJudge | None = None,
    judge_context: str = "",
) -> ResponseScore:
    """Scores whichever channels `artifact` actually populates — per architecture.md §4.4, not
    every response needs all three."""
    chat_score = None
    if artifact.chat is not None:
        chat_score = check_rag_consistency(artifact.chat, cited_passages)
        if artifact.feedback is not None and artifact.feedback.qualitative_score is not None:
            chat_score = replace(chat_score, qualitative_score=artifact.feedback.qualitative_score)
        if chat_judge is not None:
            verdict = chat_judge(artifact.question or "", artifact.chat, judge_context)
            chat_score = replace(chat_score, judge_verdict=verdict)

    graph_score = None
    if artifact.graph is not None:
        graph_score = score_graph(
            artifact.graph,
            recipes,
            buildings,
            optimal_graph=optimal_graph,
            available_power_mw=available_power_mw,
            raw_item_ids=raw_item_ids,
        )

    map_score = None
    if artifact.map_locations is not None:
        map_score = score_map(
            artifact.map_locations,
            resource_nodes,
            distance_tolerance=distance_tolerance,
            judge=terrain_judge,
            judge_context=judge_context,
        )

    return ResponseScore(chat=chat_score, graph=graph_score, map=map_score)
