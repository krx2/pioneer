"""Scoring functions from architecture.md §6, over a `ResponseArtifact`'s three channels
(implementation.md Stage 15).

Each channel mixes deterministic checks with, where architecture.md calls for it, an injectable
"judge" hook — the same transport-injection pattern `qa_engine.engine` and `server_client.client`
use for their own external calls: the caller supplies a plain callable, so the real LLM-as-a-judge
call (Stage 16's job) never needs to exist for these functions to be fully unit-tested.

The Chat channel measures word overlap with the same notion of a significant word that retrieval
ranks passages by (`qa_engine.significant_words`) — the answer is being compared against retrieved
text, so the two have to agree on what a word worth matching is.

- Chat: `check_rag_consistency` is fully deterministic (grounding is a fact-check, not a matter of
  taste, so it doesn't need a judge) — architecture.md's own three-way split reserves LLM-as-a-judge
  for *subjective* fit, never arithmetic-or-fact correctness. The numbers decide consistency:
  every number the answer states has to be one its grounding holds (architecture.md invariant #1 —
  the model phrases numbers, it never makes them), allowing for the answer rounding it. Word
  overlap is reported alongside, but an answer in natural sentences shares too few words with a
  JSON tool result for that to judge anything.
- Graph: `score_graph` calls the real Stage 4 Verifier directly — implementation.md Stage 15
  explicitly allows this ("just calling a finished function library, not a live integration").
- Map: `score_map` deterministically checks position, purity and distance against the real
  resource data, and that the save shows nothing extracting from the node yet, plus an optional
  `TerrainJudge` hook for the subjective "does this fit the terrain/existing infra" call.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from pioneer.contracts import (
    Building,
    Coordinates,
    PlacementRecord,
    ProductionGraph,
    RankedLocation,
    Recipe,
    ResourceNode,
    ResponseArtifact,
)
from pioneer.qa_engine import significant_words
from pioneer.verifier import (
    added_machines,
    balance,
    distance,
    minimal_machine_graph,
    power_balance,
)

# --- Shared ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeVerdict:
    """An LLM-as-a-judge's call on whether an answer fits its context — advisory, never gating."""

    fits_context: bool
    rationale: str


# --- Chat channel --------------------------------------------------------------------------

_LIST_MARKER_RE = re.compile(r"^\s*[0-9]+[.)]\s+", re.MULTILINE)
_NUMBER_RE = re.compile(r"(?<![\w.,#])[0-9]+(?:[.,][0-9]+)*(?:[eE][+-]?[0-9]+)?")
"""A number as written: "20", "7.5", "1,620", "7,5", "1.62e+03". Not one glued to a word or a dot
("Mk.2", "BP_ResourceNode103") or a rank ("#1") — those are names, not quantities."""
_THOUSANDS_RE = re.compile(r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?")
_DECIMAL_COMMA_RE = re.compile(r"[0-9]+,[0-9]+")
_RELATIVE_TOLERANCE = 0.01
"""How far a stated number may sit from a grounded one beyond its own rounding: "about 6,500 MW"
for 6518 MW is fine."""

Reading = tuple[float, int]
"""A number's value and how many decimal places it was written with."""


def _stated_numbers(text: str) -> list[tuple[str, tuple[Reading, ...]]]:
    """Every number in `text` as written, with each way it can be read — "1,500" is 1500 in
    English and 1.5 in Polish. Numbered-list markers ("1. Build ...") are skipped."""
    stated: list[tuple[str, tuple[Reading, ...]]] = []
    for token in _NUMBER_RE.findall(_LIST_MARKER_RE.sub("", text)):
        if "," not in token:
            stated.append((token, _reading(token)))
            continue
        readings: tuple[Reading, ...] = ()
        if _THOUSANDS_RE.fullmatch(token):
            readings += _reading(token.replace(",", ""))
        if _DECIMAL_COMMA_RE.fullmatch(token):
            readings += _reading(token.replace(",", "."))
        if readings:
            stated.append((token, readings))
        else:  # "1,2,3": several numbers, not one
            stated += [(part, _reading(part)) for part in token.split(",")]
    return [(token, readings) for token, readings in stated if readings]


def _reading(text: str) -> tuple[Reading, ...]:
    try:
        value = float(text)
    except ValueError:
        return ()
    mantissa = text.lower().split("e")[0]
    return ((value, len(mantissa.partition(".")[2])),)


def _is_grounded(readings: Sequence[Reading], known: Sequence[float]) -> bool:
    """Some reading matches a known number, rounded to the decimal places it was written with or
    within `_RELATIVE_TOLERANCE`."""
    return any(
        abs(value - number) <= max(0.5 * 10**-decimals, _RELATIVE_TOLERANCE * number) + 1e-9
        for value, decimals in readings
        for number in known
    )


@dataclass(frozen=True)
class ChatScore:
    grounded_fraction: float
    """Fraction (0-1) of the chat's distinct significant words that appear in at least one cited
    passage. `1.0` for an empty/all-stopword chat. Informational only: an answer in natural
    sentences shares few words with the structured tool output it's built from."""
    consistent: bool
    """No number the chat states is missing from its grounding — `ungrounded_numbers` is empty."""
    qualitative_score: int | None = None
    """Player feedback's 1-5 rating (`Feedback.qualitative_score`), carried through unchanged —
    this function never invents one."""
    judge_verdict: JudgeVerdict | None = None
    """architecture.md §6's Chat-channel LLM-as-a-judge call: does the answer suit the player's
    question, context and game phase. `None` when no judge ran, or it gave no usable verdict."""
    ungrounded_numbers: tuple[str, ...] = ()
    """Numbers the chat states, as written, that nothing in its grounding matches — even rounded
    to the precision written, or 1% either way. Numbers that are part of a name ("Mk.2") and
    numbered-list markers aren't claims, so they aren't checked."""


class ChatJudge(Protocol):
    """Rates whether `answer` suits the player's `question` and situation (`context`) —
    architecture.md's Chat-channel LLM-as-a-judge step (the real one is
    `llm_client.judges.chat_judge`). `None` means it gave no usable verdict."""

    def __call__(self, question: str, answer: str, context: str) -> JudgeVerdict | None: ...


def check_rag_consistency(chat_text: str, cited_passages: Sequence[str]) -> ChatScore:
    known = [
        abs(value)
        for passage in cited_passages
        for _, readings in _stated_numbers(passage)
        for value, _ in readings
    ]
    ungrounded = [
        token for token, readings in _stated_numbers(chat_text) if not _is_grounded(readings, known)
    ]

    chat_words = set(significant_words(chat_text))
    passage_words: set[str] = set()
    for passage in cited_passages:
        passage_words.update(significant_words(passage))
    fraction = len(chat_words & passage_words) / len(chat_words) if chat_words else 1.0

    return ChatScore(
        grounded_fraction=fraction,
        consistent=not ungrounded,
        ungrounded_numbers=tuple(dict.fromkeys(ungrounded)),
    )


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
    power_draw_mw: float = 0.0
    """The graph's net power draw (`verifier.power_balance`)."""
    available_power_mw: float | None = None
    """The budget `power_ok` was judged against, if one was given."""


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
        power_draw_mw=power_draw,
        available_power_mw=available_power_mw,
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
    position_ok: bool
    """The location sits where the real resource node is, within `distance_tolerance`."""
    purity_ok: bool
    """The location's claimed purity matches the real resource node's."""
    distance_ok: bool | None
    """The location's `distance_to_reference` matches the real node's distance from the reference
    point, within `distance_tolerance`. `None` when no reference point was given to check by."""
    still_free: bool
    """No placed extractor in the save extracts from the node: the site is still there to take."""
    judge_verdict: JudgeVerdict | None
    passed: bool
    """Every deterministic check holds — an unchecked distance doesn't count against it. The
    judge's verdict is advisory/qualitative, never gating."""


def score_map(
    locations: Sequence[RankedLocation],
    resource_nodes: Sequence[ResourceNode],
    *,
    reference: Coordinates | None = None,
    placements: Sequence[PlacementRecord] = (),
    distance_tolerance: float = 1.0,
    judge: TerrainJudge | None = None,
    judge_context: str = "",
) -> tuple[MapScore, ...]:
    """A location on a node `resource_nodes` doesn't have fails every check, unjudged."""
    nodes_by_id = {node.node_id: node for node in resource_nodes}
    claimed = {p.resource_node_id for p in placements if p.resource_node_id is not None}
    scores = []
    for location in locations:
        node = nodes_by_id.get(location.resource_node_id)
        if node is None:
            scores.append(
                MapScore(
                    resource_node_id=location.resource_node_id,
                    position_ok=False,
                    purity_ok=False,
                    distance_ok=False,
                    still_free=False,
                    judge_verdict=None,
                    passed=False,
                )
            )
            continue

        position_ok = distance(location.position, node.position) <= distance_tolerance
        purity_ok = location.purity == node.purity
        distance_ok = (
            abs(distance(node.position, reference) - location.distance_to_reference)
            <= distance_tolerance
            if reference is not None
            else None
        )
        still_free = node.node_id not in claimed
        verdict = judge(location, judge_context) if judge is not None else None
        scores.append(
            MapScore(
                resource_node_id=location.resource_node_id,
                position_ok=position_ok,
                purity_ok=purity_ok,
                distance_ok=distance_ok,
                still_free=still_free,
                judge_verdict=verdict,
                passed=position_ok and purity_ok and distance_ok is not False and still_free,
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
    placements: Sequence[PlacementRecord] = (),
    distance_tolerance: float = 1.0,
    terrain_judge: TerrainJudge | None = None,
    chat_judge: ChatJudge | None = None,
    chat_context: str = "",
    terrain_context: str = "",
) -> ResponseScore:
    """Scores whichever channels `artifact` actually populates — per architecture.md §4.4, not
    every response needs all three — and only as far as the data allows: a channel that can't be
    scored (no knowledge base, a graph on a building the knowledge base doesn't list) comes back
    unscored rather than costing the others theirs.

    The graph channel is scored for what the answer *adds*: an expansion's extended nodes count
    only their new machines, every material the graph takes in from outside is not held against
    it, and, unless a better `optimal_graph` is given, the optimum it's measured against is the
    same plan in fractional machines. `chat_context` and `terrain_context` are what the two judges
    are told about the player's situation."""
    return ResponseScore(
        chat=_chat_channel(artifact, cited_passages, chat_judge, chat_context),
        graph=_graph_channel(
            artifact, recipes, buildings, optimal_graph, available_power_mw, raw_item_ids
        ),
        map=_map_channel(
            artifact, resource_nodes, placements, distance_tolerance, terrain_judge, terrain_context
        ),
    )


def _chat_channel(
    artifact: ResponseArtifact,
    cited_passages: Sequence[str],
    judge: ChatJudge | None,
    judge_context: str,
) -> ChatScore | None:
    if not artifact.chat:
        return None
    score = check_rag_consistency(artifact.chat, cited_passages)
    if artifact.feedback is not None and artifact.feedback.qualitative_score is not None:
        score = replace(score, qualitative_score=artifact.feedback.qualitative_score)
    if judge is not None:
        score = replace(
            score, judge_verdict=judge(artifact.question or "", artifact.chat, judge_context)
        )
    return score


def _graph_channel(
    artifact: ResponseArtifact,
    recipes: tuple[Recipe, ...],
    buildings: tuple[Building, ...],
    optimal_graph: ProductionGraph | None,
    available_power_mw: float | None,
    raw_item_ids: Collection[str],
) -> GraphScore | None:
    graph = artifact.graph
    if graph is None or not recipes or not buildings:
        return None
    added = added_machines(graph)
    if not added.nodes:  # nothing to build -- e.g. a drawing of the factory as it stands
        return None
    from_outside = set(raw_item_ids) | {
        flow.item_id for flow in graph.flows if flow.source_node_id is None
    }
    try:
        return score_graph(
            added,
            recipes,
            buildings,
            optimal_graph=(
                optimal_graph
                if optimal_graph is not None
                else minimal_machine_graph(added, recipes)
            ),
            available_power_mw=available_power_mw,
            raw_item_ids=from_outside,
        )
    except ValueError:
        return None


def _map_channel(
    artifact: ResponseArtifact,
    resource_nodes: Sequence[ResourceNode],
    placements: Sequence[PlacementRecord],
    distance_tolerance: float,
    judge: TerrainJudge | None,
    judge_context: str,
) -> tuple[MapScore, ...] | None:
    if artifact.map_locations is None:
        return None
    return score_map(
        artifact.map_locations,
        resource_nodes,
        reference=artifact.map_reference,
        placements=placements,
        distance_tolerance=distance_tolerance,
        judge=judge,
        judge_context=judge_context,
    )
