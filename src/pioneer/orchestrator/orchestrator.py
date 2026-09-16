"""LLM Orchestrator core (implementation.md Stage 16).

The routing loop: hand the player's question and a set of tools -- one per Stage 2-11 module, plus
item/recipe lookups over the Knowledge Base -- to a local, OpenAI-compatible LLM, execute whichever
tools it calls, feed the results back, and repeat until it answers in plain text. Tool-calling *is*
the intent-routing step from architecture.md §4.3 ("classify what the player is asking for") --
which tool(s) the model reaches for is the classification, so there's no separate
intent-classification call.

**No arithmetic in the LLM path (architecture.md invariant #1).** Every tool handler below calls
straight into a deterministic module (and, where the module needs it, the Verifier) and returns
its *real* structured output. That structured output is accumulated into the final
`ResponseArtifact.graph` / `.map_locations` directly by this module's own code -- never
reconstructed from the LLM's prose -- so every number in a response still traces back to a
specific module call (invariant #6). The LLM only ever sees a compact JSON summary of a tool's
result and is responsible for *phrasing*, not producing, the numbers in it.

**Nothing a tool does escapes as an exception (invariant #5).** Expected failures (unknown item,
no save loaded) and unexpected ones (a module choking on data it didn't anticipate) alike come back
to the model as an `{"error": ...}` tool result it can explain to the player -- see `_run_tool`.

**Items by name.** Tools accept an item's in-game name ("Reinforced Iron Plate") as well as its id
(`Desc_IronPlateReinforced_C`): a local model can't be expected to know the export's class names.
An unrecognized item comes back as an error listing the closest matches, so the model can correct
itself on the next round.

**The existing factory, as the Verifier sees it.** Expansion and diagnosis both start from the
factory's net per-item balance: what its recipes make, minus what its recipes and its generators
consume. Expansion then plans only what that surplus doesn't cover (see expansion_advisor), and
diagnosis judges power against every placed building -- extractors and generators included, which
the save's recipe graph never contains.

Like `qa_engine.engine` and `server_client.client`, this module never imports an HTTP library
itself -- the caller injects a `ToolCallingLLM` transport (see `pioneer.llm_client` for the real
implementation), so the whole routing loop is testable against a scripted fake model with zero
real networking.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from pioneer.anomaly_detector import detect_anomalies
from pioneer.contracts import (
    Building,
    Coordinates,
    GameState,
    Item,
    MaterialFlow,
    PlacementRecord,
    ProductionGraph,
    RankedLocation,
    Recipe,
    ResourceNode,
    ResponseArtifact,
)
from pioneer.expansion_advisor import advise_expansion
from pioneer.knowledge_base import find_items
from pioneer.location_advisor import rank_locations
from pioneer.production_planner.planner import plan_production, recipes_for_output
from pioneer.qa_engine import ChatCompletion, LLMUnavailable, NoRelevantPassages, Passage, QAAnswer
from pioneer.qa_engine import answer_question as qa_answer_question
from pioneer.verifier import (
    balance,
    generator_fuel_demand,
    placed_generation_capacity_mw,
    placed_power_consumption_mw,
    power_balance,
)

_MAX_TOOL_ROUNDS = 6

_SYSTEM_PROMPT = (
    "You are Pioneer, an assistant for the factory-building game Satisfactory. You help the "
    "player plan, expand, locate, and diagnose their factory, and answer game-mechanics questions. "
    "You MUST use the provided tools for every calculation: machine counts, throughput, power "
    "balance, and distances are never something you compute or estimate yourself, only the tools "
    "do that. Tools accept items by id (e.g. Desc_IronPlate_C) or by in-game name (e.g. 'Iron "
    "Plate'); call find_item first if you're unsure which item the player means. Call whichever "
    "tool(s) match the player's request, then write one clear, concise natural-language answer "
    "summarizing the tool results -- never invent numbers that didn't come from a tool. If a tool "
    "reports an error (e.g. no save data loaded), explain that limitation to the player plainly "
    "instead of guessing or making up factory state."
)

_ITEM_DESCRIPTION = "Item id (e.g. Desc_IronPlate_C) or in-game name (e.g. 'Iron Plate')"


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
    items: tuple[Item, ...] = ()
    """Every item the Knowledge Base knows: resolves the in-game names the model passes to tools
    into ids, marks the raw resources planning must stop at, and carries the fuels' energy values.
    Empty (no knowledge base) means item ids are passed through to the modules unchecked."""
    resource_nodes: tuple[ResourceNode, ...] = ()
    existing_graph: ProductionGraph | None = None
    """The player's current factory state, e.g. from the Save Parser. `None` means no save is
    loaded -- expansion/diagnosis tools report that explicitly rather than fabricating a factory."""
    existing_placements: tuple[PlacementRecord, ...] = ()
    qa_corpus: tuple[Passage, ...] = ()
    game_state: GameState | None = None
    available_power_mw: float | None = None
    """Overrides the grid capacity diagnosis would otherwise derive from the placed generators."""


@dataclass
class _ArtifactAccumulator:
    """Collects the *real* structured output of whichever tool(s) actually ran this turn, so the
    final `ResponseArtifact` is built from module output, never re-derived from the LLM's text."""

    graph: ProductionGraph | None = None
    map_locations: tuple[RankedLocation, ...] | None = None


class _ToolError(Exception):
    """A tool's own, expected failure (unknown item, no knowledge base, ...), reported back to the
    model as `{"error": message, **details}` -- see `_run_tool`."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


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
        result = _run_tool(tool, call.get("arguments") or {})
    return {
        "role": "tool",
        "tool_call_id": call["id"],
        "name": call["name"],
        "content": json.dumps(result),
    }


def _run_tool(tool: _Tool, arguments: dict[str, Any]) -> dict[str, Any]:
    """Runs one tool, turning any failure into an error result for the model rather than letting
    it escape `handle_query`. Deliberately catches everything: a bug in a module, or real data it
    didn't anticipate, should cost the player one tool call's worth of answer -- explained to them
    by the model -- not the whole response."""
    try:
        return tool.handler(arguments)
    except _ToolError as error:
        return {"error": str(error), **error.details}
    except Exception as error:
        return {"error": f"{tool.name} failed: {type(error).__name__}: {error}"}


def _build_tools(
    context: OrchestratorContext,
    accumulator: _ArtifactAccumulator,
    qa_chat_completion: ChatCompletion,
    llm_base_url: str,
    llm_model: str,
    llm_api_key: str | None,
) -> list[_Tool]:
    recipe_choices_parameter = {
        "type": "object",
        "description": (
            "Optional: item -> recipe id, to use a specific (e.g. alternate) recipe for that item "
            "instead of the default standard one"
        ),
        "additionalProperties": {"type": "string"},
    }
    return [
        _Tool(
            name="find_item",
            description=(
                "Look up items by in-game name or id, e.g. 'reinforced plate' -> "
                "Desc_IronPlateReinforced_C. Use when unsure which item the player means."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            handler=lambda args: _handle_find_item(args, context),
        ),
        _Tool(
            name="list_recipes_for_item",
            description=(
                "List every recipe producing an item -- the standard one and any alternates -- "
                "with its building and per-machine rates. Use for 'how is X made' or 'which "
                "alternate recipe should I use for X' questions."
            ),
            parameters={
                "type": "object",
                "properties": {"item": {"type": "string", "description": _ITEM_DESCRIPTION}},
                "required": ["item"],
            },
            handler=lambda args: _handle_list_recipes(args, context),
        ),
        _Tool(
            name="plan_production",
            description=(
                "Plan a brand-new production chain from raw resources up to a target item and "
                "rate. Use for 'I want to produce N/min of X' when nothing needs to be extended."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_item_id": {"type": "string", "description": _ITEM_DESCRIPTION},
                    "target_rate_per_minute": {"type": "number"},
                    "recipe_choices": recipe_choices_parameter,
                },
                "required": ["target_item_id", "target_rate_per_minute"],
            },
            handler=lambda args: _handle_plan_production(args, context, accumulator),
        ),
        _Tool(
            name="expand_existing_factory",
            description=(
                "Given a new target item and rate, compute the minimal change (extend/add) to the "
                "player's *existing* factory instead of planning from scratch -- the existing "
                "factory's spare output is used first. Requires save data."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_item_id": {"type": "string", "description": _ITEM_DESCRIPTION},
                    "target_rate_per_minute": {"type": "number"},
                    "recipe_choices": recipe_choices_parameter,
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
                    "item_id": {"type": "string", "description": _ITEM_DESCRIPTION},
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


def _handle_find_item(args: dict[str, Any], context: OrchestratorContext) -> dict[str, Any]:
    if not context.items:
        raise _ToolError("no knowledge base loaded -- item lookup is unavailable")
    matches = find_items(context.items, str(args["query"]), limit=8)
    return {"items": [_item_summary(item) for item in matches]}


def _handle_list_recipes(args: dict[str, Any], context: OrchestratorContext) -> dict[str, Any]:
    item_id = _resolve_item_id(args["item"], context)
    return {
        "item_id": item_id,
        "raw_resource": item_id in _raw_item_ids(context),
        "recipes": [
            {
                "recipe_id": recipe.recipe_id,
                "name": recipe.name,
                "alternate": recipe.is_alternate,
                "building_id": recipe.building_ids[0] if recipe.building_ids else None,
                "inputs_per_machine_per_minute": {
                    ingredient.item_id: ingredient.amount_per_minute for ingredient in recipe.inputs
                },
                "outputs_per_machine_per_minute": {
                    product.item_id: product.amount_per_minute for product in recipe.outputs
                },
            }
            for recipe in recipes_for_output(item_id, context.recipes)
        ],
    }


def _handle_plan_production(
    args: dict[str, Any], context: OrchestratorContext, accumulator: _ArtifactAccumulator
) -> dict[str, Any]:
    target_item_id = _resolve_item_id(args["target_item_id"], context)
    try:
        graph = plan_production(
            target_item_id,
            float(args["target_rate_per_minute"]),
            context.recipes,
            _recipe_choices(args, context),
            raw_item_ids=_raw_item_ids(context),
        )
    except (ValueError, KeyError) as error:
        return {"error": str(error)}

    summary = _graph_summary(graph, context)  # before publishing: it can fail on unknown buildings
    accumulator.graph = graph
    return summary


def _handle_expand_existing_factory(
    args: dict[str, Any], context: OrchestratorContext, accumulator: _ArtifactAccumulator
) -> dict[str, Any]:
    if context.existing_graph is None:
        return {
            "error": "no existing factory state available (no save loaded) -- cannot compute an "
            "expansion; offer a from-scratch plan instead, or tell the player to load a save"
        }
    target_item_id = _resolve_item_id(args["target_item_id"], context)
    raw_item_ids = _raw_item_ids(context)
    try:
        existing_balance = _existing_item_balance(context)
        surplus = {
            item_id: rate
            for item_id, rate in existing_balance.items()
            if rate > 0 and item_id not in raw_item_ids
        }
        additions = plan_production(
            target_item_id,
            float(args["target_rate_per_minute"]),
            context.recipes,
            _recipe_choices(args, context),
            raw_item_ids=raw_item_ids,
            available_supply=surplus,
        )
    except (ValueError, KeyError) as error:
        return {"error": str(error)}

    change_set = advise_expansion(context.existing_graph, additions)
    accumulator.graph = change_set.resulting_graph
    items_in_plan = {flow.item_id for flow in additions.flows}
    return {
        "changes": [
            {
                "action": change.action.value,
                "recipe_id": change.recipe_id,
                "additional_machine_count": change.additional_machine_count,
                "target_node_id": change.target_node_id,
            }
            for change in change_set.changes
        ],
        "drawn_from_existing_surplus_per_minute": _per_item(
            flow
            for flow in additions.flows
            if flow.source_node_id is None and flow.item_id in surplus
        ),
        "existing_shortfalls_per_minute": {
            item_id: -rate
            for item_id, rate in existing_balance.items()
            if rate < 0 and item_id in items_in_plan and item_id not in raw_item_ids
        },
    }


def _handle_rank_locations(
    args: dict[str, Any], context: OrchestratorContext, accumulator: _ArtifactAccumulator
) -> dict[str, Any]:
    item_id = _resolve_item_id(args["item_id"], context)
    reference = Coordinates(
        x=float(args.get("reference_x", 0.0)),
        y=float(args.get("reference_y", 0.0)),
        z=float(args.get("reference_z", 0.0)),
    )
    count = int(args.get("count", 5))
    ranked = rank_locations(item_id, context.resource_nodes, context.existing_placements, reference)
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
        item_balance = _existing_item_balance(context)
        power_draw_mw, power_capacity_mw = _existing_power(context)
    except ValueError as error:
        return {"error": f"cannot diagnose: {error}"}
    anomalies = detect_anomalies(
        context.existing_graph,
        item_balance,
        power_draw_mw,
        raw_item_ids=_raw_item_ids(context) | _uncraftable_item_ids(context, item_balance),
        available_power_mw=power_capacity_mw,
    )
    return {
        "power_draw_mw": power_draw_mw,
        "power_capacity_mw": power_capacity_mw,
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


def _existing_item_balance(context: OrchestratorContext) -> dict[str, float]:
    """The player's factory's net per-item rate: what its recipes make, minus what its recipes and
    its generators consume -- a factory burning its own Fuel isn't overproducing it."""
    assert context.existing_graph is not None
    net = balance(context.existing_graph, context.recipes)
    burned = generator_fuel_demand(context.existing_placements, context.buildings, context.items)
    for item_id, rate in burned.items():
        net[item_id] = net.get(item_id, 0.0) - rate
    return net


def _existing_power(context: OrchestratorContext) -> tuple[float, float | None]:
    """(draw, capacity) in MW. Taken from every placed building when the save's placements are
    loaded -- extractors, pumps and generators included -- otherwise just the recipe graph's own
    draw, against whatever capacity the context was given (`None`: unknown, so never a blackout).
    """
    if context.existing_placements and context.buildings:
        draw = placed_power_consumption_mw(context.existing_placements, context.buildings)
        capacity = (
            context.available_power_mw
            if context.available_power_mw is not None
            else placed_generation_capacity_mw(context.existing_placements, context.buildings)
        )
        return draw, capacity

    assert context.existing_graph is not None
    draw = power_balance(context.existing_graph, context.buildings) if context.buildings else 0.0
    return draw, context.available_power_mw


def _raw_item_ids(context: OrchestratorContext) -> frozenset[str]:
    return frozenset(item.item_id for item in context.items if item.is_raw_resource)


def _uncraftable_item_ids(context: OrchestratorContext, item_ids: Iterable[str]) -> frozenset[str]:
    """Of `item_ids`, those no factory recipe makes -- Wood, Mycelia, creature remains: gathered by
    hand, so like raw resources they come into the factory from outside rather than running short
    inside it."""
    craftable = {product.item_id for recipe in context.recipes for product in recipe.outputs}
    return frozenset(item_id for item_id in item_ids if item_id not in craftable)


def _resolve_item_id(raw: object, context: OrchestratorContext) -> str:
    """The item id `raw` names: the id itself, or the item's in-game name (case-insensitive).
    Anything else raises a `_ToolError` carrying the closest matches. Passed through unchecked when
    no knowledge base is loaded -- there's nothing to check it against."""
    query = str(raw).strip()
    if not context.items:
        return query

    matches = find_items(context.items, query)
    folded = query.casefold()
    for item in matches:
        if folded in (item.item_id.casefold(), item.name.casefold()):
            return item.item_id
    if any(product.item_id == query for recipe in context.recipes for product in recipe.outputs):
        return query  # a real recipe product the export just has no item descriptor for
    raise _ToolError(
        f"unknown item {query!r}", did_you_mean=[_item_summary(item) for item in matches]
    )


def _recipe_choices(args: dict[str, Any], context: OrchestratorContext) -> dict[str, str] | None:
    raw = args.get("recipe_choices")
    if not isinstance(raw, dict) or not raw:
        return None
    return {_resolve_item_id(item, context): str(recipe_id) for item, recipe_id in raw.items()}


def _item_summary(item: Item) -> dict[str, Any]:
    return {"item_id": item.item_id, "name": item.name, "raw_resource": item.is_raw_resource}


def _per_item(flows: Iterable[MaterialFlow]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for flow in flows:
        totals[flow.item_id] = totals.get(flow.item_id, 0.0) + flow.amount_per_minute
    return totals


def _graph_summary(graph: ProductionGraph, context: OrchestratorContext) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "machine_counts": {node.recipe_id: node.machine_count for node in graph.nodes},
    }
    if context.recipes:
        summary["net_item_balance"] = balance(graph, context.recipes)
    if context.buildings:
        summary["net_power_draw_mw"] = power_balance(graph, context.buildings)
    return summary
