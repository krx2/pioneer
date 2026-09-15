"""LLM Orchestrator core (implementation.md Stage 16).

The routing loop: hand the player's question and a set of tools -- one per Stage 2-11 module -- to
a local, OpenAI-compatible LLM, execute whichever tools it calls, feed the results back, and repeat
until it answers in plain text. Tool-calling *is* the intent-routing step from architecture.md
§4.3 ("classify what the player is asking for") -- which tool(s) the model reaches for is the
classification, so there's no separate intent-classification call.

**No arithmetic in the LLM path (architecture.md invariant #1).** Every tool handler below calls
straight into a deterministic module (and, where the module needs it, the Verifier) and returns
its *real* structured output. That structured output is accumulated into the final
`ResponseArtifact.graph` / `.map_locations` directly by this module's own code -- never
reconstructed from the LLM's prose -- so every number in a response still traces back to a
specific module call (invariant #6). The LLM only ever sees a compact JSON summary of a tool's
result and is responsible for *phrasing*, not producing, the numbers in it.

Like `qa_engine.engine` and `server_client.client`, this module never imports an HTTP library
itself -- the caller injects a `ToolCallingLLM` transport (see `pioneer.llm_client` for the real
implementation), so the whole routing loop is testable against a scripted fake model with zero
real networking.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pioneer.anomaly_detector import detect_anomalies
from pioneer.contracts import (
    Building,
    Coordinates,
    GameState,
    PlacementRecord,
    ProductionGraph,
    RankedLocation,
    Recipe,
    ResourceNode,
    ResponseArtifact,
)
from pioneer.expansion_advisor import advise_expansion
from pioneer.location_advisor import rank_locations
from pioneer.production_planner import plan_production
from pioneer.qa_engine import ChatCompletion, LLMUnavailable, NoRelevantPassages, Passage, QAAnswer
from pioneer.qa_engine import answer_question as qa_answer_question
from pioneer.verifier import balance, power_balance

_MAX_TOOL_ROUNDS = 6

_SYSTEM_PROMPT = (
    "You are Pioneer, an assistant for the factory-building game Satisfactory. You help the "
    "player plan, expand, locate, and diagnose their factory, and answer game-mechanics questions. "
    "You MUST use the provided tools for every calculation: machine counts, throughput, power "
    "balance, and distances are never something you compute or estimate yourself, only the tools "
    "do that. Call whichever tool(s) match the player's request, then write one clear, concise "
    "natural-language answer summarizing the tool results -- never invent numbers that didn't come "
    "from a tool. If a tool reports an error (e.g. no save data loaded), explain that limitation "
    "to the player plainly instead of guessing or making up factory state."
)


class TransportError(Exception):
    """Raised by a `ToolCallingLLM` implementation when the request couldn't complete at all
    (connection refused, timeout, ...) -- distinct from the model successfully replying, which
    `handle_query` handles itself without needing this exception."""


@dataclass(frozen=True)
class OrchestratorUnavailable:
    """Typed "couldn't produce a response" result -- architecture.md §7.5's graceful-degradation
    invariant applied to the Orchestrator itself: an unreachable LLM or a runaway tool-call loop
    comes back as this, never a raised exception."""

    reason: str


class ToolCallingLLM(Protocol):
    """Sends `messages` (OpenAI chat/completions shape) plus `tools` (OpenAI tool-schema shape) to
    `base_url` for `model`, returning the assistant's reply as `{"content": str | None,
    "tool_calls": [{"id": str, "name": str, "arguments": dict[str, Any]}, ...]}` -- each tool
    call's arguments already parsed from the wire format's JSON string, so this module never
    touches that wire format directly. Raises `TransportError` if the request couldn't complete at
    all."""

    def __call__(
        self,
        base_url: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        api_key: str | None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class OrchestratorContext:
    """Everything the Orchestrator needs beyond the player's question -- plain data, no I/O, the
    same dependency-injection pattern every other module uses. Every field defaults to empty/None
    so a missing data source (no save loaded, no dedicated server reachable, no knowledge base
    wired yet) degrades gracefully (architecture.md invariant #5) instead of crashing a tool call.
    """

    recipes: tuple[Recipe, ...] = ()
    buildings: tuple[Building, ...] = ()
    resource_nodes: tuple[ResourceNode, ...] = ()
    existing_graph: ProductionGraph | None = None
    """The player's current factory state, e.g. from the Save Parser. `None` means no save is
    loaded -- expansion/diagnosis tools report that explicitly rather than fabricating a factory."""
    existing_placements: tuple[PlacementRecord, ...] = ()
    qa_corpus: tuple[Passage, ...] = ()
    game_state: GameState | None = None
    available_power_mw: float | None = None


@dataclass
class _ArtifactAccumulator:
    """Collects the *real* structured output of whichever tool(s) actually ran this turn, so the
    final `ResponseArtifact` is built from module output, never re-derived from the LLM's text."""

    graph: ProductionGraph | None = None
    map_locations: tuple[RankedLocation, ...] | None = None


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]

    def as_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def handle_query(
    tool_calling_llm: ToolCallingLLM,
    qa_chat_completion: ChatCompletion,
    question: str,
    context: OrchestratorContext,
    *,
    llm_base_url: str,
    llm_model: str,
    llm_api_key: str | None = None,
    response_id: str,
    max_tool_rounds: int = _MAX_TOOL_ROUNDS,
) -> ResponseArtifact | OrchestratorUnavailable:
    accumulator = _ArtifactAccumulator()
    tools = _build_tools(
        context, accumulator, qa_chat_completion, llm_base_url, llm_model, llm_api_key
    )
    tool_schemas = [tool.as_schema() for tool in tools]
    tools_by_name = {tool.name: tool for tool in tools}

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    for _ in range(max_tool_rounds):
        try:
            message = tool_calling_llm(llm_base_url, llm_model, messages, tool_schemas, llm_api_key)
        except TransportError as error:
            return OrchestratorUnavailable(reason=f"could not reach LLM endpoint: {error}")

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return ResponseArtifact(
                response_id=response_id,
                chat=message.get("content") or "",
                graph=accumulator.graph,
                map_locations=accumulator.map_locations,
            )

        messages.append(_assistant_message(message, tool_calls))
        for call in tool_calls:
            messages.append(_execute_tool(call, tools_by_name))

    return OrchestratorUnavailable(
        reason=f"exceeded {max_tool_rounds} tool-call rounds without a final answer"
    )


def _assistant_message(message: dict[str, Any], tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": message.get("content"),
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call.get("arguments") or {}),
                },
            }
            for call in tool_calls
        ],
    }


def _execute_tool(call: dict[str, Any], tools_by_name: dict[str, _Tool]) -> dict[str, Any]:
    tool = tools_by_name.get(call["name"])
    if tool is None:
        result: dict[str, Any] = {"error": f"unknown tool {call['name']!r}"}
    else:
        result = tool.handler(call.get("arguments") or {})
    return {
        "role": "tool",
        "tool_call_id": call["id"],
        "name": call["name"],
        "content": json.dumps(result),
    }


def _build_tools(
    context: OrchestratorContext,
    accumulator: _ArtifactAccumulator,
    qa_chat_completion: ChatCompletion,
    llm_base_url: str,
    llm_model: str,
    llm_api_key: str | None,
) -> list[_Tool]:
    return [
        _Tool(
            name="plan_production",
            description=(
                "Plan a brand-new production chain from raw resources up to a target item and "
                "rate. Use for 'I want to produce N/min of X' when nothing needs to be extended."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_item_id": {
                        "type": "string",
                        "description": "Item id, e.g. Desc_IronPlate_C",
                    },
                    "target_rate_per_minute": {"type": "number"},
                },
                "required": ["target_item_id", "target_rate_per_minute"],
            },
            handler=lambda args: _handle_plan_production(args, context, accumulator),
        ),
        _Tool(
            name="expand_existing_factory",
            description=(
                "Given a new target item and rate, compute the minimal change (extend/add) to the "
                "player's *existing* factory instead of planning from scratch. Requires save data."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_item_id": {"type": "string"},
                    "target_rate_per_minute": {"type": "number"},
                },
                "required": ["target_item_id", "target_rate_per_minute"],
            },
            handler=lambda args: _handle_expand_existing_factory(args, context, accumulator),
        ),
        _Tool(
            name="rank_build_locations",
            description=(
                "Rank unclaimed resource deposits of a given item, best first, for where to build "
                "next. Use for 'where should I build/mine X' questions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "count": {
                        "type": "integer",
                        "description": "Max locations to return, default 5",
                    },
                    "reference_x": {
                        "type": "number",
                        "description": "Reference point to measure distance from",
                    },
                    "reference_y": {"type": "number"},
                    "reference_z": {"type": "number"},
                },
                "required": ["item_id"],
            },
            handler=lambda args: _handle_rank_locations(args, context, accumulator),
        ),
        _Tool(
            name="diagnose_factory_problems",
            description=(
                "Scan the player's existing factory for resource deficits/surpluses, power "
                "blackouts, and belt congestion. Requires save data."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda args: _handle_diagnose_factory(args, context),
        ),
        _Tool(
            name="answer_game_question",
            description=(
                "Answer a free-form Satisfactory game-mechanics question via retrieval over the "
                "knowledge base/wiki. Use for general 'how does X work' questions."
            ),
            parameters={
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
            handler=lambda args: _handle_answer_question(
                args, context, qa_chat_completion, llm_base_url, llm_model, llm_api_key
            ),
        ),
    ]


def _handle_plan_production(
    args: dict[str, Any], context: OrchestratorContext, accumulator: _ArtifactAccumulator
) -> dict[str, Any]:
    try:
        graph = plan_production(
            str(args["target_item_id"]), float(args["target_rate_per_minute"]), context.recipes
        )
    except (ValueError, KeyError) as error:
        return {"error": str(error)}

    accumulator.graph = graph
    return _graph_summary(graph, context)


def _handle_expand_existing_factory(
    args: dict[str, Any], context: OrchestratorContext, accumulator: _ArtifactAccumulator
) -> dict[str, Any]:
    if context.existing_graph is None:
        return {
            "error": "no existing factory state available (no save loaded) -- cannot compute an "
            "expansion; offer a from-scratch plan instead, or tell the player to load a save"
        }
    try:
        target_graph = plan_production(
            str(args["target_item_id"]), float(args["target_rate_per_minute"]), context.recipes
        )
    except (ValueError, KeyError) as error:
        return {"error": str(error)}

    change_set = advise_expansion(context.existing_graph, target_graph)
    accumulator.graph = change_set.resulting_graph
    return {
        "changes": [
            {
                "action": change.action.value,
                "recipe_id": change.recipe_id,
                "additional_machine_count": change.additional_machine_count,
                "target_node_id": change.target_node_id,
            }
            for change in change_set.changes
        ]
    }


def _handle_rank_locations(
    args: dict[str, Any], context: OrchestratorContext, accumulator: _ArtifactAccumulator
) -> dict[str, Any]:
    reference = Coordinates(
        x=float(args.get("reference_x", 0.0)),
        y=float(args.get("reference_y", 0.0)),
        z=float(args.get("reference_z", 0.0)),
    )
    count = int(args.get("count", 5))
    ranked = rank_locations(
        str(args["item_id"]), context.resource_nodes, context.existing_placements, reference
    )
    top = ranked[:count]
    accumulator.map_locations = top
    return {
        "locations": [
            {
                "resource_node_id": location.resource_node_id,
                "purity": location.purity.value,
                "distance_to_reference": location.distance_to_reference,
                "score": location.score,
            }
            for location in top
        ]
    }


def _handle_diagnose_factory(args: dict[str, Any], context: OrchestratorContext) -> dict[str, Any]:
    if context.existing_graph is None:
        return {
            "error": "no existing factory state available (no save loaded) -- nothing to diagnose"
        }
    try:
        item_balance = balance(context.existing_graph, context.recipes)
        net_power_draw_mw = (
            power_balance(context.existing_graph, context.buildings) if context.buildings else 0.0
        )
    except ValueError as error:
        return {"error": f"cannot diagnose: {error}"}
    anomalies = detect_anomalies(
        context.existing_graph,
        item_balance,
        net_power_draw_mw,
        available_power_mw=context.available_power_mw,
    )
    return {
        "net_power_draw_mw": net_power_draw_mw,
        "anomalies": [
            {
                "kind": anomaly.kind.value,
                "severity": anomaly.severity.value,
                "description": anomaly.description,
                "item_id": anomaly.item_id,
                "node_id": anomaly.node_id,
            }
            for anomaly in anomalies
        ],
    }


def _handle_answer_question(
    args: dict[str, Any],
    context: OrchestratorContext,
    qa_chat_completion: ChatCompletion,
    llm_base_url: str,
    llm_model: str,
    llm_api_key: str | None,
) -> dict[str, Any]:
    result = qa_answer_question(
        qa_chat_completion,
        str(args["question"]),
        context.qa_corpus,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
    )
    if isinstance(result, QAAnswer):
        return {
            "answer": result.answer,
            "citations": [citation.source for citation in result.citations],
        }
    if isinstance(result, NoRelevantPassages):
        return {"answer": None, "note": "no relevant passages found for this question"}
    assert isinstance(result, LLMUnavailable)
    return {"answer": None, "error": f"LLM unavailable: {result.reason}"}


def _graph_summary(graph: ProductionGraph, context: OrchestratorContext) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "machine_counts": {node.recipe_id: node.machine_count for node in graph.nodes},
    }
    if context.recipes:
        summary["net_item_balance"] = balance(graph, context.recipes)
    if context.buildings:
        summary["net_power_draw_mw"] = power_balance(graph, context.buildings)
    return summary
