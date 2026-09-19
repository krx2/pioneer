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

**Tool calls written as text.** A local model sometimes puts a tool call in its answer instead of
the API's `tool_calls` field. One naming a real tool is executed like any other, rather than shown
to the player as a line of JSON -- see `_tool_calls_written_as_text`.

**Nothing a tool does escapes as an exception (invariant #5).** Expected failures (unknown item,
no save loaded) and unexpected ones (a module choking on data it didn't anticipate) alike come back
to the model as an `{"error": ...}` tool result it can explain to the player -- see `_run_tool`.

**Grounding.** Every tool result carries a `names` map for the ids in it, so the model can
answer in the names a player knows. Every result it saw, and every knowledge passage it
retrieved, is kept on the artifact as `grounding`: what the
answer is checked against (see verification.py) and what its claims trace back to.

**Conversations.** `history` is the earlier turns of the same conversation, oldest first, as
(question, answer) pairs: the model sees them before the new question, so "and how much power is
that?" makes sense. They're grounding too — a follow-up may repeat a number an earlier answer gave,
which was checked against that turn's own tools when it was given. Only the newest turns that fit
`_HISTORY_BUDGET_CHARS` are passed on (see `recent_history`): a local model's context window is
small, and what overflows it is cut from the front — the system prompt and the tools.

**Items by name.** Tools accept an item's in-game name ("Reinforced Iron Plate") as well as its id
(`Desc_IronPlateReinforced_C`): a local model can't be expected to know the export's class names.
Plurals and a name only one item fits are taken too ("Screw", "reinforced plates"); a name that
fits several items, or none, comes back as an error listing the closest matches, so the model can
correct itself on the next round.

**What the player has unlocked.** With a save loaded, planning uses the recipes the player has
unlocked first; only when those can't make the item does it plan with every recipe, and then it
says which stages need what unlocked. `plan_unlocks` turns that into an unlock order.

**The existing factory, as the Verifier sees it.** Expansion and diagnosis both start from the
factory's net per-item balance: what its recipes make, its extractors mine and its generators leave
behind (nuclear waste), minus what its recipes and its generators consume — fuel, and the water
coal and nuclear plants need besides. Expansion then plans only what that surplus doesn't cover (see
expansion_advisor) and reports the raw resources the plan needs against spare extraction;
diagnosis judges power against every placed building -- extractors and generators included, which
the save's recipe graph never contains -- and ore supply too, once resource node data is loaded.

Like `qa_engine.engine` and `server_client.client`, this module never imports an HTTP library
itself -- the caller injects a `ToolCallingLLM` transport (see `pioneer.llm_client` for the real
implementation), so the whole routing loop is testable against a scripted fake model with zero
real networking.
"""

from __future__ import annotations

import difflib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from pioneer.anomaly_detector import (
    detect_anomalies,
    detect_belt_overloads,
    detect_wiring_problems,
)
from pioneer.contracts import (
    AnomalyKind,
    AnomalyRecord,
    AnomalySeverity,
    Building,
    ChangeAction,
    Coordinates,
    FactorySite,
    GameState,
    Item,
    MaterialFlow,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
    RankedLocation,
    Recipe,
    ResourceNode,
    ResponseArtifact,
    Technology,
    TransportError,
    TransportLink,
    TransportTier,
)
from pioneer.expansion_advisor import advise_expansion
from pioneer.knowledge_base import (
    easiest_unlock,
    find_items,
    recipe_is_unlocked,
    resolve_item,
    unlock_order,
)
from pioneer.location_advisor import rank_locations
from pioneer.production_planner.planner import plan_production, recipes_for_output
from pioneer.qa_engine import ChatCompletion, LLMUnavailable, NoRelevantPassages, Passage, QAAnswer
from pioneer.qa_engine import answer_question as qa_answer_question
from pioneer.verifier import (
    added_machines,
    allocate_supply,
    balance,
    belt_loads,
    consumption,
    distance,
    extraction_rates,
    extractors_needed,
    generator_byproducts,
    generator_fuel_demand,
    generator_supplemental_demand,
    implied_flows,
    placed_generation_capacity_mw,
    placed_power_consumption_mw,
    power_balance,
    power_plants,
    transport_needs,
)

_MAX_TOOL_ROUNDS = 6

_SYSTEM_PROMPT = (
    "You are Pioneer, an assistant for the factory-building game Satisfactory. You help the "
    "player plan, expand, locate, and diagnose their factory, and answer game-mechanics questions. "
    "You MUST use the provided tools for every calculation: machine counts, throughput, power "
    "balance, and distances are never something you compute or estimate yourself, only the tools "
    "do that. The same goes for facts about the game -- rates, capacities, recipes, unlock costs: "
    "call answer_game_question instead of recalling them, because what you remember about "
    "Satisfactory is out of date. Never write a building, recipe, or item name, id, machine count, "
    "or rate that a tool result didn't give you this conversation -- if you can't point to which "
    "tool call it came from, it's a hallucination and it will get flagged as an unverified number. "
    "This includes machine/building names: 'Wire Stripper MK2', 'Press MK2' and similar are not "
    "real Satisfactory buildings unless list_recipes_for_item, plan_production, or "
    "expand_existing_factory actually returned them -- do not invent plausible-sounding ones. "
    "Tools accept items by id (e.g. Desc_IronPlate_C) or by in-game "
    "name (e.g. 'Iron Plate'); call find_item first if you're unsure which item the player means. "
    "Tool results carry a `names` map from ids to in-game names: always answer with those names, "
    "and never show the player a raw id like Desc_IronPlate_C or Build_SmelterMk1_C -- if an id in "
    "a tool result has no entry in `names`, describe it in plain words instead of pasting the id. "
    "Call whichever tool(s) match the player's request, using their exact registered name and "
    "parameter names, then write one clear, concise answer summarizing the tool results -- never "
    "invent numbers that didn't come from a tool. If a tool "
    "reports an error (e.g. no save data loaded), explain that limitation to the player plainly "
    "instead of guessing or making up factory state.\n\n"
    "Graph and map panels shown to the player come only from a tool call made in THIS turn: "
    "plan_production, expand_existing_factory, and compare_recipes draw the production graph; "
    "show_existing_factory draws what the player has already built; "
    "rank_build_locations draws the map. Numbers you already gave in an earlier turn don't carry "
    "a panel with them. So whenever the player asks to see, draw, or visualize a graph or map -- "
    "even as a follow-up to a plan you already described in text -- call the matching tool again "
    "this turn (with the same target/rate as before if that's what they mean); answering from the "
    "earlier text alone leaves the player with no panel at all. The page draws those panels "
    "itself, next to your answer: never write an image, a link or a placeholder for a graph or "
    "map, and never say one is shown unless you called its tool this turn."
)

_HISTORY_BUDGET_CHARS = 12_000
"""How much of the conversation so far the model is shown, newest turns first: about 3k tokens.
The system prompt and tool schemas already take about 2k, and each tool result its own share of a
local model's small context window -- a conversation that outgrew it would push the prompt and the
tools out of its front, and the model would answer with neither."""

_ITEM_DESCRIPTION = "Item id (e.g. Desc_IronPlate_C) or in-game name (e.g. 'Iron Plate')"
_ITEM_HINT = "(item id or in-game name)"
_DEFAULT_COMPARISON_RATE = 10.0
_WATER_ID = "Desc_Water_C"
_CM_PER_M = 100.0
"""Save coordinates are centimetres; tool results speak metres, so the model never converts."""
_GEYSER_ID = "Desc_Geyser_C"
"""The resource database's id for geysers -- made up by the community data, since no item
backs a geyser (see resource_db/loader.py)."""


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
    touches that wire format directly. Raises `contracts.TransportError` if the request couldn't
    complete at all."""

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
    existing_links: tuple[TransportLink, ...] = ()
    """Which of `existing_placements` the save's belts and pipes join. Empty when unknown: then a
    drawing's flows are shared out by the recipes alone, and wiring isn't diagnosed."""
    qa_corpus: tuple[Passage, ...] = ()
    game_state: GameState | None = None
    available_power_mw: float | None = None
    """Overrides the grid capacity diagnosis would otherwise derive from the placed generators."""
    technologies: tuple[Technology, ...] = ()
    transport_tiers: tuple[TransportTier, ...] = ()
    factory_sites: tuple[FactorySite, ...] = ()
    """Where the player's factories stand (see `location_advisor.find_factory_sites`)."""
    unlocked_technology_ids: frozenset[str] | None = None
    """Every technology the player has unlocked, from the save. `None`: unknown, so every recipe is
    treated as available."""


@dataclass
class _ArtifactAccumulator:
    """Collects the *real* structured output of whichever tool(s) actually ran this turn, so the
    final `ResponseArtifact` is built from module output, never re-derived from the LLM's text."""

    graph: ProductionGraph | None = None
    map_locations: tuple[RankedLocation, ...] | None = None
    map_reference: Coordinates | None = None
    factory_sites: tuple[FactorySite, ...] | None = None
    grounding: list[str] = field(default_factory=list)


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
    history: Sequence[tuple[str, str]] = (),
) -> ResponseArtifact | OrchestratorUnavailable:
    history = recent_history(history)  # what the model sees is also what the answer is held to
    accumulator = _ArtifactAccumulator(
        grounding=[question, *(text for turn in history for text in turn)]
    )
    names = display_names(context)
    tools = _build_tools(
        context, accumulator, qa_chat_completion, llm_base_url, llm_model, llm_api_key
    )
    tool_schemas = [tool.as_schema() for tool in tools]
    tools_by_name = {tool.name: tool for tool in tools}

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": f"{_SYSTEM_PROMPT}\n\n{context_note(context)}"},
    ]
    for earlier_question, earlier_answer in history:
        messages.append({"role": "user", "content": earlier_question})
        messages.append({"role": "assistant", "content": earlier_answer})
    messages.append({"role": "user", "content": question})

    for _ in range(max_tool_rounds):
        try:
            message = tool_calling_llm(llm_base_url, llm_model, messages, tool_schemas, llm_api_key)
        except TransportError as error:
            return OrchestratorUnavailable(reason=f"could not reach LLM endpoint: {error}")

        tool_calls = message.get("tool_calls") or _tool_calls_written_as_text(
            message.get("content"), tools_by_name
        )
        if not tool_calls:
            return ResponseArtifact(
                response_id=response_id,
                chat=message.get("content") or "",
                graph=accumulator.graph,
                map_locations=accumulator.map_locations,
                map_reference=accumulator.map_reference,
                factory_sites=accumulator.factory_sites,
                question=question,
                grounding=tuple(accumulator.grounding),
            )

        messages.append(_assistant_message(message, tool_calls))
        for call in tool_calls:
            result_message = _execute_tool(call, tools_by_name, names)
            messages.append(result_message)
            accumulator.grounding.append(result_message["content"])

    return OrchestratorUnavailable(
        reason=f"exceeded {max_tool_rounds} tool-call rounds without a final answer"
    )


def recent_history(
    history: Sequence[tuple[str, str]], budget: int = _HISTORY_BUDGET_CHARS
) -> list[tuple[str, str]]:
    """The newest turns of `history` whose questions and answers fit `budget` characters, oldest
    first. The newest turn always stays -- its answer cut short if that alone is over."""
    kept: list[tuple[str, str]] = []
    used = 0
    for question, answer in reversed(history):
        size = len(question) + len(answer)
        if used + size > budget:
            if not kept:
                kept.append((question, answer[: max(budget - len(question), 0)] + " …"))
            break
        kept.append((question, answer))
        used += size
    return kept[::-1]


def context_note(context: OrchestratorContext) -> str:
    """What this conversation has to go on, so the model can say what it doesn't know instead of
    assuming it (architecture.md invariant #5)."""
    lines = [
        f"Knowledge base: {len(context.recipes)} factory recipes."
        if context.recipes
        else "Knowledge base: not loaded -- no recipe data.",
        f"Player's factory (latest save): {len(context.existing_placements)} buildings running "
        f"{len(context.existing_graph.nodes)} recipes in {len(context.factory_sites)} factories."
        if context.existing_graph is not None
        else "Player's factory: no save loaded -- nothing is known about what they have built.",
        f"Resource node data: {len(context.resource_nodes)} nodes."
        if context.resource_nodes
        else "Resource node data: not loaded -- build locations can't be ranked.",
        _unlocks_note(context),
    ]
    state = context.game_state
    lines.append(
        f"Live server: session {state.session_name or 'unnamed'}, game phase {state.phase}, "
        f"tech tier {state.tech_tier}."
        if state is not None
        else "Live server state: unavailable."
    )
    return "Data available in this conversation:\n" + "\n".join(f"- {line}" for line in lines)


def _unlocks_note(context: OrchestratorContext) -> str:
    unlocked = context.unlocked_technology_ids
    if unlocked is None:
        return "Unlocked technologies: unknown -- plans may use recipes the player doesn't have."
    tiers = [
        t.tier
        for t in context.technologies
        if t.kind == "milestone" and t.technology_id in unlocked
    ]
    highest = f", milestones up to tier {max(tiers)}" if tiers else ""
    return (
        f"Unlocked technologies: {len(unlocked)}{highest}. Plans prefer unlocked recipes and "
        "say what else a stage needs."
    )


_JSON_START = re.compile(r"[\[{]")


_NAME_MATCH_CUTOFF = 0.6
_ARGUMENT_MATCH_CUTOFF = 0.5


def _resolve_tool_name(name: object, tools_by_name: Mapping[str, _Tool]) -> str | None:
    """`name` if it's a real tool, else the closest real tool name it's a near-miss of --
    'expand_factory' for `expand_existing_factory`, say. A model that gets the call itself right
    but fumbles the exact registered spelling still shouldn't lose the whole tool call."""
    if not isinstance(name, str):
        return None
    if name in tools_by_name:
        return name
    matches = difflib.get_close_matches(name, tools_by_name.keys(), n=1, cutoff=_NAME_MATCH_CUTOFF)
    return matches[0] if matches else None


def _normalize_arguments(arguments: dict[str, Any], tool: _Tool) -> dict[str, Any]:
    """Remaps an argument key a model wrote under a plausible-but-wrong name ('item_id' for
    `target_item_id`, 'target_amount_per_minute' for `target_rate_per_minute', ...) onto the
    tool's actual parameter name, so a close-but-not-exact call still runs instead of failing on a
    missing required key. A key close to none of them is passed through unchanged -- the handler,
    not this heuristic, is what should reject it."""
    declared = tool.parameters.get("properties", {})
    if not declared:
        return arguments
    normalized: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in declared:
            normalized[key] = value
            continue
        match = difflib.get_close_matches(key, declared.keys(), n=1, cutoff=_ARGUMENT_MATCH_CUTOFF)
        normalized[match[0] if match else key] = value
    return normalized


def _tool_calls_written_as_text(
    content: str | None, tools_by_name: Mapping[str, _Tool]
) -> list[dict[str, Any]]:
    """Tool calls a model wrote into its answer instead of the API's `tool_calls` field -- local
    models do that often enough to be worth reading, rather than handing the player a line of JSON
    as their answer. Only a call naming (or near-naming, see `_resolve_tool_name`) a real tool
    counts; anything else is just an answer that happens to contain braces.

    Every JSON value in the text is read, one after another, whatever stands between them: prose,
    code fences, Qwen's `<tool_call>` tags, or the stray tokens it sometimes writes in place of
    the opening tag -- which is also what keeps the backend from recognizing the call itself."""
    written = [
        entry
        for value in _json_values(content or "")
        for entry in (value if isinstance(value, list) else [value])
    ]
    calls = []
    for call in written:
        if not isinstance(call, dict):
            continue
        call = call.get("function", call)
        resolved_name = _resolve_tool_name(call.get("name"), tools_by_name)
        arguments = call.get("arguments", call.get("parameters", {}))
        if resolved_name is not None and isinstance(arguments, dict):
            calls.append(
                {"id": f"text_call_{len(calls)}", "name": resolved_name, "arguments": arguments}
            )
    return calls


def _json_values(text: str) -> list[Any]:
    """Every JSON object or list in `text`, left to right."""
    decoder = json.JSONDecoder()
    values: list[Any] = []
    index = 0
    while (start := _JSON_START.search(text, index)) is not None:
        try:
            value, index = decoder.raw_decode(text, start.start())
        except json.JSONDecodeError:
            index = start.start() + 1
            continue
        values.append(value)
    return values


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


def _execute_tool(
    call: dict[str, Any], tools_by_name: dict[str, _Tool], names: Mapping[str, str]
) -> dict[str, Any]:
    """Runs one call and packages its result, with a `names` map for its ids, for the model. The
    name is resolved leniently (see `_resolve_tool_name`) because even the API's own `tool_calls`
    field isn't always the exact registered name with every local backend."""
    tool_name = _resolve_tool_name(call["name"], tools_by_name)
    tool = tools_by_name.get(tool_name) if tool_name else None
    if tool is None:
        result: dict[str, Any] = {"error": f"unknown tool {call['name']!r}"}
    else:
        result = _run_tool(tool, _normalize_arguments(call.get("arguments") or {}, tool))
    mentioned = _names_mentioned(json.dumps(result), names)
    if mentioned:
        result = {**result, "names": mentioned}
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
                "rate: each stage's recipe, building, machine count and power. Use for 'I want "
                "to produce N/min of X' when nothing needs to be extended."
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
                "factory's spare output is used first. Reports the buildings to add, the power "
                "they draw and what the grid has to spare. Requires save data."
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
            name="show_existing_factory",
            description=(
                "Draw a production graph of what the player has ALREADY BUILT, from the latest "
                "save. When they ask about an item ('how does my factory make Computers?'), pass "
                "item_id: the stages making it, across all their factories. Pass site_id only "
                "when they name one of their factories, as an earlier result listed it. With "
                "neither: the whole factory, or a list of its factories if too big to draw. "
                "Flows are what the recipes imply, not traced belts. Requires save data."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site_id": {"type": "string"},
                    "item_id": {"type": "string", "description": _ITEM_DESCRIPTION},
                },
            },
            handler=lambda args: _handle_show_existing_factory(args, context, accumulator),
        ),
        _Tool(
            name="compare_recipes",
            description=(
                "Compare every recipe for an item -- the standard one and each alternate -- as a "
                "whole production chain at the given rate: machines, power, the resources it "
                "takes in, byproducts, and whether the player has it unlocked. Use for 'which "
                "recipe/alternate is best for X' questions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": _ITEM_DESCRIPTION},
                    "target_rate_per_minute": {
                        "type": "number",
                        "description": "Rate to compare at, default 10",
                    },
                },
                "required": ["item"],
            },
            handler=lambda args: _handle_compare_recipes(args, context),
        ),
        _Tool(
            name="plan_power",
            description=(
                "Ways to generate a given amount of power: for each generator and fuel, how many "
                "generators, the fuel and water they need at full load, the water extractors for "
                "that, and waste. Name a fuel to also get the production chain that makes it. Use "
                "for 'how do I get N MW' questions; diagnose_factory_problems tells the current "
                "draw and capacity."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_mw": {"type": "number"},
                    "fuel": {
                        "type": "string",
                        "description": "Optional: only plants burning this fuel " + _ITEM_HINT,
                    },
                },
                "required": ["target_mw"],
            },
            handler=lambda args: _handle_plan_power(args, context),
        ),
        _Tool(
            name="plan_unlocks",
            description=(
                "What the player still has to unlock to make an item: the technologies its "
                "production chain needs, with their prerequisites, in the order to get them, and "
                "what each costs. Use for 'what do I need to research/unlock for X' questions."
            ),
            parameters={
                "type": "object",
                "properties": {"item": {"type": "string", "description": _ITEM_DESCRIPTION}},
                "required": ["item"],
            },
            handler=lambda args: _handle_plan_unlocks(args, context),
        ),
        _Tool(
            name="rank_build_locations",
            description=(
                "Rank unclaimed resource deposits of a given item (or 'geyser'), best first, for "
                "where to build next. Use for 'where should I build/mine X' questions. Distances "
                "are measured from the given reference point, else from the middle of the "
                "player's existing buildings."
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
                "blackouts, and its wiring: machines no belt or pipe feeds, products with "
                "nowhere to go, belts carrying more than their tier can. Requires save data."
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
                args, context, accumulator, qa_chat_completion, llm_base_url, llm_model, llm_api_key
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
        "raw_resource": item_id in raw_resource_ids(context),
        "recipes": [
            {
                "recipe_id": recipe.recipe_id,
                "name": recipe.name,
                "alternate": recipe.is_alternate,
                "unlocked": recipe_is_unlocked(recipe, context.unlocked_technology_ids),
                "unlocked_by": list(recipe.unlockable_by),
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
    target_rate = _target_rate(args)
    try:
        graph = _plan(context, target_item_id, target_rate, _recipe_choices(args, context))
    except (ValueError, KeyError) as error:
        return {"error": str(error)}

    summary = {  # built before publishing: it fails on a building the knowledge base doesn't list
        "target_item_id": target_item_id,
        "target_rate_per_minute": target_rate,
        **_graph_summary(graph, context),
        **_needs_unlocking(graph, context),
    }
    accumulator.graph = graph
    raw_inputs = _per_item(f for f in graph.flows if f.source_node_id is None)
    summary.update(_suggest_sites(raw_inputs, context, accumulator))
    return _with_spare_power(summary, context)


def _handle_expand_existing_factory(
    args: dict[str, Any], context: OrchestratorContext, accumulator: _ArtifactAccumulator
) -> dict[str, Any]:
    if context.existing_graph is None:
        return {
            "error": "no existing factory state available (no save loaded) -- cannot compute an "
            "expansion; offer a from-scratch plan instead, or tell the player to load a save"
        }
    target_item_id = _resolve_item_id(args["target_item_id"], context)
    target_rate = _target_rate(args)
    raw_item_ids = raw_resource_ids(context)
    try:
        existing_balance = _existing_item_balance(context)
        surplus = {
            item_id: rate
            for item_id, rate in existing_balance.items()
            if rate > 0 and item_id not in raw_item_ids
        }
        additions = _plan(
            context,
            target_item_id,
            target_rate,
            _recipe_choices(args, context),
            available_supply=surplus,
        )
    except (ValueError, KeyError) as error:
        return {"error": str(error)}

    change_set = advise_expansion(context.existing_graph, additions)
    buildings = {node.recipe_id: node.building_id for node in change_set.resulting_graph.nodes}
    at_site = {
        change.recipe_id: site
        for change in change_set.changes
        if change.action is ChangeAction.EXTEND
        and (site := _main_site(change.recipe_id, context)) is not None
    }
    changes = [
        {
            "action": change.action.value,
            "recipe_id": change.recipe_id,
            "building_id": buildings[change.recipe_id],
            "additional_machine_count": change.additional_machine_count,
            **_stage_power(buildings[change.recipe_id], change.additional_machine_count, context),
            "target_node_id": change.target_node_id,
            **(
                {"at_site": at_site[change.recipe_id].site_id}
                if change.recipe_id in at_site
                else {}
            ),
        }
        for change in change_set.changes
    ]
    extended_sites = tuple({site.site_id: site for site in at_site.values()}.values())
    if extended_sites:
        accumulator.factory_sites = extended_sites
    if change_set.resulting_graph.nodes:  # nothing to build is nothing to draw
        accumulator.graph = change_set.resulting_graph
    items_in_plan = {flow.item_id for flow in additions.flows}
    raw_needed = _per_item(
        flow
        for flow in additions.flows
        if flow.source_node_id is None and flow.item_id in raw_item_ids
    )
    result: dict[str, Any] = {
        "target_item_id": target_item_id,
        "target_rate_per_minute": target_rate,
        "changes": changes,
        "drawn_from_existing_surplus_per_minute": _per_item(
            flow
            for flow in additions.flows
            if flow.source_node_id is None and flow.item_id in surplus
        ),
        "raw_resources_needed_per_minute": raw_needed,
        "spare_extraction_per_minute": {
            item_id: existing_balance[item_id]
            for item_id in raw_needed
            if existing_balance.get(item_id, 0.0) > 0
        },
        "existing_shortfalls_per_minute": {
            item_id: -rate
            for item_id, rate in existing_balance.items()
            if rate < 0 and item_id in items_in_plan and item_id not in raw_item_ids
        },
    }
    result.update(_needs_unlocking(additions, context))
    if extended_sites:
        result["sites"] = {site.site_id: _site_summary(site, context) for site in extended_sites}
    uncovered = {
        item_id: rate - max(existing_balance.get(item_id, 0.0), 0.0)
        for item_id, rate in raw_needed.items()
        if rate > max(existing_balance.get(item_id, 0.0), 0.0)
    }
    result.update(_suggest_sites(uncovered, context, accumulator))
    if all("power_mw" in change for change in changes):
        added = added_machines(change_set.resulting_graph)
        result["added_power_draw_mw"] = power_balance(added, context.buildings)
    return _with_spare_power(result, context)


def _handle_show_existing_factory(
    args: dict[str, Any], context: OrchestratorContext, accumulator: _ArtifactAccumulator
) -> dict[str, Any]:
    """Draws what the save says is built: one site, the chain behind one item, or all of it. The
    flows are `verifier.implied_flows` -- what the stages' recipes make at their clock speeds,
    shared out along the save's belts and pipes where it has them."""
    if context.existing_graph is None:
        return {"error": "no save loaded -- nothing is known about what the player has built"}
    if not context.recipes:
        return {"error": "no knowledge base loaded -- the stages' recipes are unknown"}
    site_id, item = args.get("site_id"), args.get("item_id")
    site = None
    if site_id:
        site = _find_site(str(site_id), context)
        graph, drawn = _site_graph(site), site.site_id
    elif item:
        item_id = _resolve_item_id(item, context)
        graph, drawn = _chain_graph(context.existing_graph, item_id, context.recipes), item_id
        if not graph.nodes:
            raise _ToolError(f"nothing in the player's factory makes {item_id}")
    else:
        graph, drawn = context.existing_graph, "whole factory"
        if len(graph.nodes) > _MAX_DRAWN_STAGES:
            return {
                "not_drawn": f"the whole factory has {len(graph.nodes)} stages, too many to draw "
                "at once -- call again with one site_id, or the item_id the player cares about",
                "factories": [_site_entry(s, context) for s in context.factory_sites],
            }

    known = {recipe.recipe_id for recipe in context.recipes}
    graph = replace(graph, nodes=tuple(node for node in graph.nodes if node.recipe_id in known))
    members = site.placements if site is not None else context.existing_placements
    links = _node_links(graph, members, context)
    graph = implied_flows(graph, context.recipes, links)
    accumulator.graph = graph
    if site is not None:
        accumulator.factory_sites = (site,)
    raw = raw_resource_ids(context)
    recipe_of = {recipe.recipe_id: recipe for recipe in context.recipes}
    result: dict[str, Any] = {
        "drawn": drawn,
        "flows_follow": "the save's belts and pipes" if links is not None else "the recipes alone",
        "stages": [
            {
                "recipe_id": node.recipe_id,
                "building_id": node.building_id,
                "effective_machines": round(node.machine_count, 2),
                **_stage_power(node.building_id, node.machine_count, context),
                **_stage_rates(node, recipe_of[node.recipe_id]),
            }
            for node in graph.nodes
        ],
        "raw_resources_in_per_minute": _per_item(
            f for f in graph.flows if f.source_node_id is None and f.item_id in raw
        ),
        "parts_brought_in_per_minute": _per_item(
            f for f in graph.flows if f.source_node_id is None and f.item_id not in raw
        ),
        "goes_out_per_minute": _per_item(f for f in graph.flows if f.target_node_id is None),
    }
    if site is not None:
        result["site"] = _site_entry(site, context)
    return result


def _stage_rates(node: ProductionNode, recipe: Recipe) -> dict[str, dict[str, float]]:
    """What one stage makes and uses a minute, so the model can say it of that stage rather than
    guess it from the whole drawing's totals."""
    return {
        "makes_per_minute": {
            product.item_id: round(
                product.amount_per_minute * node.machine_count * node.production_boost, 2
            )
            for product in recipe.outputs
        },
        "uses_per_minute": {
            ingredient.item_id: round(ingredient.amount_per_minute * node.machine_count, 2)
            for ingredient in recipe.inputs
        },
    }


_MAX_DRAWN_STAGES = 25
"""The most stages `show_existing_factory` draws of the whole factory at once: past that the
force-directed graph is a tangle of labels, so the model is handed the factories to pick from."""


def _find_site(site_id: str, context: OrchestratorContext) -> FactorySite:
    """The site `site_id` names -- also as "2" or "Site 2", the way a player might say it."""
    wanted = site_id.strip().casefold().replace(" ", "_")
    wanted = f"site_{wanted}" if wanted.isdigit() else wanted
    for site in context.factory_sites:
        if site.site_id.casefold() == wanted:
            return site
    raise _ToolError(
        f"no factory {site_id!r}",
        factories=[_site_entry(site, context) for site in context.factory_sites],
    )


def _site_entry(site: FactorySite, context: OrchestratorContext) -> dict[str, Any]:
    summary = _site_summary(site, context)
    return {
        "site_id": site.site_id,
        "buildings": summary["buildings"],
        "main_recipes": summary["main_recipes"],
        "distance_from_base_m": round(summary["distance_from_base_m"]),
    }


def _site_graph(site: FactorySite) -> ProductionGraph:
    """One factory's buildings as a graph, by the save parser's rules: a node per recipe, its
    machines its buildings' clock speeds summed, its boost theirs averaged by clock speed."""
    machines: dict[str, float] = defaultdict(float)
    boosted: dict[str, float] = defaultdict(float)
    building_of: dict[str, str] = {}
    for placement in site.placements:
        if placement.recipe_id is None or placement.is_paused:
            continue
        machines[placement.recipe_id] += placement.clock_speed
        boosted[placement.recipe_id] += placement.clock_speed * placement.production_boost
        building_of.setdefault(placement.recipe_id, placement.building_id)
    return ProductionGraph(
        nodes=tuple(
            ProductionNode(
                node_id=f"save_{recipe_id}",
                recipe_id=recipe_id,
                building_id=building_of[recipe_id],
                machine_count=count,
                is_existing=True,
                existing_machine_count=count,
                production_boost=boosted[recipe_id] / count if count > 0 else 1.0,
            )
            for recipe_id, count in machines.items()
        ),
        flows=(),
    )


def _chain_graph(
    graph: ProductionGraph, item_id: str, recipes: tuple[Recipe, ...]
) -> ProductionGraph:
    """The part of `graph` that makes `item_id`: the stages making it, the stages making their
    inputs, and so on down to what comes from outside the factory."""
    recipe_of = {recipe.recipe_id: recipe for recipe in recipes}
    makers: dict[str, list[ProductionNode]] = defaultdict(list)
    for node in graph.nodes:
        for product in recipe_of[node.recipe_id].outputs if node.recipe_id in recipe_of else ():
            makers[product.item_id].append(node)
    kept: dict[str, ProductionNode] = {}
    wanted, seen = [item_id], set()
    while wanted:
        current = wanted.pop()
        if current in seen:
            continue
        seen.add(current)
        for node in makers.get(current, ()):
            if node.node_id not in kept:
                kept[node.node_id] = node
                wanted.extend(i.item_id for i in recipe_of[node.recipe_id].inputs)
    return ProductionGraph(nodes=tuple(kept.values()), flows=())


def _handle_compare_recipes(args: dict[str, Any], context: OrchestratorContext) -> dict[str, Any]:
    item_id = _resolve_item_id(args["item"], context)
    rate = _target_rate(
        {"target_rate_per_minute": args.get("target_rate_per_minute", _DEFAULT_COMPARISON_RATE)}
    )
    candidates = recipes_for_output(item_id, context.recipes)
    if not candidates:
        raise _ToolError(f"no recipe makes {item_id} -- nothing to compare")
    primary = [r for r in candidates if r.outputs[0].item_id == item_id] or list(candidates)

    options: list[dict[str, Any]] = []
    for recipe in primary:
        try:
            graph = _plan(context, item_id, rate, {item_id: recipe.recipe_id})
        except ValueError as error:
            options.append({"recipe_id": recipe.recipe_id, "error": str(error)})
            continue
        net = balance(graph, context.recipes)
        option: dict[str, Any] = {
            "recipe_id": recipe.recipe_id,
            "alternate": recipe.is_alternate,
            "unlocked": recipe_is_unlocked(recipe, context.unlocked_technology_ids),
            "stages": len(graph.nodes),
            "machines": sum(node.machine_count for node in graph.nodes),
            "inputs_per_minute": {i: -rate for i, rate in net.items() if rate < -_RATE_EPSILON},
            "byproducts_per_minute": {
                i: rate for i, rate in net.items() if rate > _RATE_EPSILON and i != item_id
            },
            **_needs_unlocking(graph, context),
        }
        if all(_stage_power(n.building_id, 1, context) for n in graph.nodes):
            option["power_mw"] = power_balance(graph, context.buildings)
        options.append(option)

    planned = [option for option in options if "error" not in option]

    def best(key: Callable[[dict[str, Any]], float]) -> str | None:
        return min(planned, key=key)["recipe_id"] if planned else None

    result: dict[str, Any] = {
        "item_id": item_id,
        "target_rate_per_minute": rate,
        "options": options,
        "fewest_machines": best(lambda o: o["machines"]),
        "least_input": best(lambda o: sum(o["inputs_per_minute"].values())),
    }
    if planned and all("power_mw" in o for o in planned):
        result["least_power"] = best(lambda o: o["power_mw"])
    return result


def _handle_plan_power(args: dict[str, Any], context: OrchestratorContext) -> dict[str, Any]:
    if not context.buildings or not context.items:
        raise _ToolError("no knowledge base loaded -- power can't be planned")
    target_mw = float(args["target_mw"])
    if not target_mw > 0:
        raise _ToolError(f"target_mw must be positive, got {target_mw:g}")
    fuel_id = _resolve_item_id(args["fuel"], context) if args.get("fuel") else None
    plants = [
        p
        for p in power_plants(target_mw, context.buildings, context.items)
        if fuel_id is None or p.fuel_item_id == fuel_id
    ]
    if not plants:
        raise _ToolError(f"no generator burns {fuel_id}" if fuel_id else "no generator data")
    extractors = {b.fixed_resource_id: b for b in context.buildings if b.fixed_resource_id}

    options = []
    for plant in plants:
        option: dict[str, Any] = {
            "generator_id": plant.generator_id,
            "generators": plant.generators,
            "capacity_mw": plant.capacity_mw,
            "fuel_id": plant.fuel_item_id,
            "fuel_per_minute": plant.fuel_per_minute,
        }
        supplemental = plant.supplemental_item_id
        if supplemental is not None:
            option["supplemental_id"] = supplemental
            option["supplemental_per_minute"] = plant.supplemental_per_minute
            extractor = extractors.get(supplemental)
            if extractor is not None:
                count = extractors_needed(plant.supplemental_per_minute, extractor)
                option["extractors"] = {
                    "building_id": extractor.building_id,
                    "count": count,
                    **_stage_power(extractor.building_id, count, context),
                }
        if plant.byproduct_item_id is not None:
            option["waste_id"] = plant.byproduct_item_id
            option["waste_per_minute"] = plant.byproduct_per_minute
        options.append(option)

    result: dict[str, Any] = {"target_mw": target_mw, "options": options}
    if fuel_id is not None:
        result["fuel_supply"] = _fuel_supply(fuel_id, plants[0].fuel_per_minute, context)
    return _with_spare_power(result, context)


def _fuel_supply(fuel_id: str, per_minute: float, context: OrchestratorContext) -> dict[str, Any]:
    """How the named fuel gets made: mined, or the production chain for it."""
    if fuel_id in raw_resource_ids(context) or not recipes_for_output(fuel_id, context.recipes):
        return {"mined": True, "per_minute": per_minute}
    try:
        graph = _plan(context, fuel_id, per_minute, None)
    except ValueError as error:
        return {"error": str(error)}
    return {"per_minute": per_minute, **_graph_summary(graph, context)}


def _handle_plan_unlocks(args: dict[str, Any], context: OrchestratorContext) -> dict[str, Any]:
    if not context.technologies:
        raise _ToolError("no technology data loaded -- unlocks can't be planned")
    item_id = _resolve_item_id(args["item"], context)
    if item_id in raw_resource_ids(context):
        raise _ToolError(f"{item_id} is a raw resource: it's mined, not unlocked")
    graph = _plan(context, item_id, 1.0, None)
    recipes = {recipe.recipe_id: recipe for recipe in context.recipes}
    unlocked = context.unlocked_technology_ids
    wanted = []
    for node in graph.nodes:
        recipe = recipes[node.recipe_id]
        if unlocked is not None and recipe_is_unlocked(recipe, unlocked):
            continue  # with nothing known about unlocks, every stage's technology is listed
        technology = easiest_unlock(recipe.unlockable_by, context.technologies)
        if technology is not None:
            wanted.append(technology.technology_id)
    order = unlock_order(wanted, context.technologies, unlocked or ())
    unlocks_recipes: dict[str, list[str]] = {}
    for node in graph.nodes:
        for technology_id in recipes[node.recipe_id].unlockable_by:
            unlocks_recipes.setdefault(technology_id, []).append(node.recipe_id)
    return {
        "item_id": item_id,
        "unlocked_known": unlocked is not None,
        "already_unlocked": unlocked is not None and not order,
        "unlock_order": [
            {
                "technology_id": technology.technology_id,
                "kind": technology.kind,
                "tier": technology.tier,
                "cost": {cost.item_id: cost.amount for cost in technology.cost},
                "unlocks_recipes": unlocks_recipes.get(technology.technology_id, []),
            }
            for technology in order
        ],
    }


def _handle_rank_locations(
    args: dict[str, Any], context: OrchestratorContext, accumulator: _ArtifactAccumulator
) -> dict[str, Any]:
    if not context.resource_nodes:
        raise _ToolError(
            "no resource node data loaded (docs/resource_nodes.json is missing) -- build "
            "locations can't be ranked"
        )
    item_id = _resolve_item_id(args["item_id"], context)
    reference = reference_point(args, context)
    count = int(args.get("count", 5))
    if count < 1:
        raise _ToolError(f"count must be at least 1, got {count}")
    ranked = rank_locations(item_id, context.resource_nodes, context.existing_placements, reference)
    top = ranked[:count]
    accumulator.map_locations = top
    accumulator.map_reference = reference
    return {
        "reference": {"x": reference.x, "y": reference.y, "z": reference.z},
        "locations": [
            {
                "resource_node_id": location.resource_node_id,
                "purity": location.purity.value,
                "distance_m": location.distance_to_reference / _CM_PER_M,
                "score": location.score,
            }
            for location in top
        ],
    }


def _handle_diagnose_factory(args: dict[str, Any], context: OrchestratorContext) -> dict[str, Any]:
    if context.existing_graph is None:
        return {
            "error": "no existing factory state available (no save loaded) -- nothing to diagnose"
        }
    try:
        item_balance = _existing_item_balance(context)
        power_draw_mw, power_capacity_mw = _existing_power(context)
        burned = _generator_consumption(context)
        demand = consumption(context.existing_graph, context.recipes)
    except ValueError as error:
        return {"error": f"cannot diagnose: {error}"}
    _add_rates(demand, burned)
    anomalies = detect_anomalies(
        context.existing_graph,
        item_balance,
        power_draw_mw,
        raw_item_ids=_inputs_from_outside(context, item_balance),
        item_demand=demand,
        item_consumers=_consuming_nodes(context, shared=burned.keys()),
        available_power_mw=power_capacity_mw,
    )
    return {
        **_wiring_report(context),
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


def _node_links(
    graph: ProductionGraph, members: Iterable[PlacementRecord], context: OrchestratorContext
) -> set[tuple[str, str]] | None:
    """Which of `graph`'s nodes the save's belts and pipes join: a link from one of `members` to
    another joins the nodes of the recipes they run. `None` without link data -- the drawing then
    shares its flows out by the recipes alone."""
    if not context.existing_links:
        return None
    node_ids = {node.node_id for node in graph.nodes}
    node_of = {
        placement.object_id: f"save_{placement.recipe_id}"
        for placement in members
        if placement.object_id and placement.recipe_id and not placement.is_paused
    }
    return {
        (node_of[link.source_id], node_of[link.target_id])
        for link in context.existing_links
        if node_of.get(link.source_id) in node_ids and node_of.get(link.target_id) in node_ids
    }


_MAX_WIRING_GROUPS = 5
"""The most groups of each kind of wiring problem a diagnosis lists, the worst and biggest first --
a big save can have hundreds of problems, and the rest are only counted."""
_SEVERITY_RANK = {AnomalySeverity.HIGH: 0, AnomalySeverity.MEDIUM: 1, AnomalySeverity.LOW: 2}


def _wiring_report(context: OrchestratorContext) -> dict[str, Any]:
    """What the save's belts and pipes leave undone -- machines nothing feeds, products with
    nowhere to go, belts over their tier -- each located by building, factory and position.
    Empty when the save's links aren't known."""
    links = context.existing_links
    if not links or not context.recipes:
        return {}
    placed = {p.object_id: p for p in context.existing_placements if p.object_id}
    roles = _building_roles(placed, context)
    recipes = {recipe.recipe_id: recipe for recipe in context.recipes}
    machines = [
        (object_id, recipes[p.recipe_id])
        for object_id, p in placed.items()
        if p.recipe_id in recipes and not p.is_paused
    ]
    problems = detect_wiring_problems(
        machines,
        links,
        supplies=roles.supplies,
        accepts=roles.accepts,
        fluid_item_ids={item.item_id for item in context.items if item.is_fluid},
    )
    open_ends = {link.target_id for link in links if roles.accepts.get(link.target_id) is None}
    flows = allocate_supply(
        roles.supply_rates,
        roles.demand_rates,
        {(link.source_id, link.target_id) for link in links},
        open_ends=open_ends,
    )
    loads = belt_loads({(link.source_id, link.target_id): link.via for link in links}, flows)
    tiers = {tier.building_id: tier.capacity_per_minute for tier in context.transport_tiers}
    belt_ids = {belt: placed[belt].building_id for belt in loads if belt in placed}
    capacities = {
        belt: tiers[tier]
        for belt, building_id in belt_ids.items()
        if (tier := building_id.replace("ConveyorLift", "ConveyorBelt")) in tiers
    }
    found = [*problems, *detect_belt_overloads(loads, capacities, belt_ids=belt_ids)]
    site_of = {
        placement.object_id: site.site_id
        for site in context.factory_sites
        for placement in site.placements
        if placement.object_id
    }

    # One entry per kind of trouble in a factory: its machines, or the segments of one belt line,
    # all of which the same few words describe.
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    members: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    for anomaly in found:
        entry = _located(anomaly, placed, site_of, context)
        placement = placed[anomaly.node_id or ""]
        if anomaly.kind is AnomalyKind.CONGESTION:  # a belt or lift, named by its tier
            belt = anomaly.node_id or ""
            load = round(loads[belt])
            tier = placement.building_id.replace("ConveyorLift", "ConveyorBelt")
            key = (anomaly.kind, entry.get("factory"), tier, load)
            entry.update(
                building_id=tier, carries_per_minute=load, rated_per_minute=capacities[belt]
            )
            count_as = "belt_segments"
        else:
            key = (anomaly.kind, entry.get("factory"), placement.recipe_id)
            entry.update(recipe_id=placement.recipe_id, items=[])
            count_as = "machines"
        group = groups.setdefault(key, {**entry, count_as: 0})
        members[key].add(anomaly.node_id or "")
        group[count_as] = len(members[key])
        if "items" in group and anomaly.item_id not in group["items"]:
            group["items"].append(anomaly.item_id)
        group.pop("item_id", None)
        group.pop("description", None)

    ordered = sorted(
        groups.values(),
        key=lambda g: (
            _SEVERITY_RANK[AnomalySeverity(g["severity"])],
            -g.get("machines", g.get("belt_segments", 0)),
        ),
    )
    shown: Counter[str] = Counter()
    listed = []
    for group in ordered:
        if shown[group["kind"]] < _MAX_WIRING_GROUPS:
            shown[group["kind"]] += 1
            listed.append(group)
    return {
        "wiring_problems_found": dict(Counter(anomaly.kind.value for anomaly in found)),
        "wiring_problems": listed,
    }


@dataclass
class _Roles:
    """Per building, by object id: which items it may put out and take in (`None`: any), and at
    what rates it makes and uses them while running."""

    supplies: dict[str, set[str] | None] = field(default_factory=dict)
    accepts: dict[str, set[str] | None] = field(default_factory=dict)
    supply_rates: dict[str, dict[str, float]] = field(default_factory=dict)
    demand_rates: dict[str, dict[str, float]] = field(default_factory=dict)


def _building_roles(placed: Mapping[str, PlacementRecord], context: OrchestratorContext) -> _Roles:
    """A machine makes and uses its recipe's items, and one with no recipe set takes nothing; an
    extractor puts out its resource; a generator takes its fuels and the water they need, and
    puts out their waste. Anything else -- a container, a station, a sink -- is left out: it may
    carry anything."""
    recipes = {recipe.recipe_id: recipe for recipe in context.recipes}
    buildings = {building.building_id: building for building in context.buildings}
    manufacturers = {building for recipe in context.recipes for building in recipe.building_ids}
    resource_of = {node.node_id: node.item_id for node in context.resource_nodes}
    roles = _Roles()
    for object_id, p in placed.items():
        recipe, building = recipes.get(p.recipe_id or ""), buildings.get(p.building_id)
        if recipe is not None:
            roles.supplies[object_id] = {product.item_id for product in recipe.outputs}
            roles.accepts[object_id] = {ingredient.item_id for ingredient in recipe.inputs}
            if not p.is_paused:
                roles.supply_rates[object_id] = {
                    product.item_id: product.amount_per_minute * p.clock_speed * p.production_boost
                    for product in recipe.outputs
                }
                roles.demand_rates[object_id] = {
                    ingredient.item_id: ingredient.amount_per_minute * p.clock_speed
                    for ingredient in recipe.inputs
                }
        elif p.building_id in manufacturers:
            roles.supplies[object_id], roles.accepts[object_id] = set(), set()
        elif building is not None and building.extraction_rate_per_minute > 0:
            resource = building.fixed_resource_id or resource_of.get(p.resource_node_id or "")
            roles.supplies[object_id] = {resource} if resource else None
            roles.accepts[object_id] = set()
            if not p.is_paused:
                roles.supply_rates[object_id] = extraction_rates(
                    (p,), context.buildings, context.resource_nodes
                )
        elif building is not None and building.fuels:
            fuels = building.fuels
            roles.accepts[object_id] = {fuel.fuel_item_id for fuel in fuels} | {
                fuel.supplemental_item_id for fuel in fuels if fuel.supplemental_item_id
            }
            roles.supplies[object_id] = {
                fuel.byproduct_item_id for fuel in fuels if fuel.byproduct_item_id
            }
            burned = generator_fuel_demand((p,), context.buildings, context.items)
            _add_rates(burned, generator_supplemental_demand((p,), context.buildings))
            roles.demand_rates[object_id] = burned
            roles.supply_rates[object_id] = generator_byproducts(
                (p,), context.buildings, context.items
            )
    return roles


def _located(
    anomaly: AnomalyRecord,
    placed: Mapping[str, PlacementRecord],
    site_of: Mapping[str, str],
    context: OrchestratorContext,
) -> dict[str, Any]:
    """An anomaly as the model sees it: its building, the factory it's in (or the nearest one),
    and where it stands in metres -- never the save's object id, which means nothing to a
    player."""
    entry: dict[str, Any] = {
        "kind": anomaly.kind.value,
        "severity": anomaly.severity.value,
        "description": anomaly.description,
        "item_id": anomaly.item_id,
    }
    placement = placed.get(anomaly.node_id or "")
    if placement is None:
        return entry
    entry["building_id"] = placement.building_id
    nearest = min(
        context.factory_sites,
        key=lambda site: distance(site.position, placement.position),
        default=None,
    )
    factory = site_of.get(placement.object_id or "") or (nearest.site_id if nearest else None)
    if factory is not None:
        entry["factory"] = factory
    entry["x_m"] = round(placement.position.x / _CM_PER_M)
    entry["y_m"] = round(placement.position.y / _CM_PER_M)
    return entry


def _handle_answer_question(
    args: dict[str, Any],
    context: OrchestratorContext,
    accumulator: _ArtifactAccumulator,
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
        passages = {passage.passage_id: passage.text for passage in context.qa_corpus}
        accumulator.grounding.extend(
            passages[citation.passage_id]
            for citation in result.citations
            if citation.passage_id in passages
        )
        return {
            "answer": result.answer,
            "citations": [citation.source for citation in result.citations],
        }
    if isinstance(result, NoRelevantPassages):
        return {"answer": None, "note": "no relevant passages found for this question"}
    assert isinstance(result, LLMUnavailable)
    return {"answer": None, "error": f"LLM unavailable: {result.reason}"}


def _existing_item_balance(context: OrchestratorContext) -> dict[str, float]:
    """The player's factory's net per-item rate: what its recipes make, its extractors pull out of
    the ground and its generators leave behind, minus what its recipes and its generators consume
    -- a factory burning its own Fuel isn't overproducing it, and the water its coal generators
    drink isn't spare."""
    assert context.existing_graph is not None
    placements, buildings = context.existing_placements, context.buildings
    net = balance(context.existing_graph, context.recipes)
    _add_rates(net, extraction_rates(placements, buildings, context.resource_nodes))
    _add_rates(net, generator_byproducts(placements, buildings, context.items))
    _add_rates(net, _generator_consumption(context), sign=-1.0)
    return net


def _generator_consumption(context: OrchestratorContext) -> dict[str, float]:
    """What the placed generators consume per minute: their fuel and any supplemental water."""
    placements, buildings = context.existing_placements, context.buildings
    consumed = generator_fuel_demand(placements, buildings, context.items)
    _add_rates(consumed, generator_supplemental_demand(placements, buildings))
    return consumed


def _add_rates(totals: dict[str, float], rates: Mapping[str, float], sign: float = 1.0) -> None:
    for item_id, rate in rates.items():
        totals[item_id] = totals.get(item_id, 0.0) + sign * rate


def _consuming_nodes(
    context: OrchestratorContext, *, shared: Collection[str]
) -> dict[str, set[str]]:
    """Per item, the existing graph's nodes whose recipe consumes it -- leaving out the `shared`
    items something outside the graph (a generator) consumes too, which no node alone is to blame
    for running short."""
    assert context.existing_graph is not None
    recipes = {recipe.recipe_id: recipe for recipe in context.recipes}
    consumers: dict[str, set[str]] = defaultdict(set)
    for node in context.existing_graph.nodes:
        recipe = recipes.get(node.recipe_id)
        for ingredient in recipe.inputs if recipe is not None else ():
            if ingredient.item_id not in shared:
                consumers[ingredient.item_id].add(node.node_id)
    return consumers


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


def raw_resource_ids(context: OrchestratorContext) -> frozenset[str]:
    """Every raw resource the knowledge base knows — what planning stops at, and what a graph is
    allowed to take in from outside. The same set as `knowledge_base.raw_resource_ids`, read off a
    context instead of a `KnowledgeBase`."""
    return frozenset(item.item_id for item in context.items if item.is_raw_resource)


def _inputs_from_outside(context: OrchestratorContext, item_ids: Iterable[str]) -> frozenset[str]:
    """Items diagnosis doesn't judge as short: hand-gathered ones always, and raw resources unless
    resource node data is loaded -- without it, what the extractors mine is unknown and every ore
    would look short."""
    raw = raw_resource_ids(context)
    gathered = _uncraftable_item_ids(context, item_ids) - raw
    return gathered if context.resource_nodes else gathered | raw


def reference_point(args: dict[str, Any], context: OrchestratorContext) -> Coordinates:
    """The point the model asked to measure from; else the middle of the player's buildings --
    their base, near enough -- else the map origin."""
    if any(key in args for key in ("reference_x", "reference_y", "reference_z")):
        return Coordinates(
            x=float(args.get("reference_x", 0.0)),
            y=float(args.get("reference_y", 0.0)),
            z=float(args.get("reference_z", 0.0)),
        )
    placements = context.existing_placements
    if not placements:
        return Coordinates(x=0.0, y=0.0, z=0.0)
    return Coordinates(
        x=sum(p.position.x for p in placements) / len(placements),
        y=sum(p.position.y for p in placements) / len(placements),
        z=sum(p.position.z for p in placements) / len(placements),
    )


def _uncraftable_item_ids(context: OrchestratorContext, item_ids: Iterable[str]) -> frozenset[str]:
    """Of `item_ids`, those no factory recipe makes -- Wood, Mycelia, creature remains: gathered by
    hand, so like raw resources they come into the factory from outside rather than running short
    inside it."""
    craftable = {product.item_id for recipe in context.recipes for product in recipe.outputs}
    return frozenset(item_id for item_id in item_ids if item_id not in craftable)


def _resolve_item_id(raw: object, context: OrchestratorContext) -> str:
    """The item id `raw` names: the id itself, or whatever name `knowledge_base.resolve_item`
    takes as unambiguous ("Screw", "iron plates"). Anything else raises a `_ToolError` carrying
    the closest matches. Passed through unchecked when no knowledge base is loaded -- there's
    nothing to check it against."""
    query = str(raw).strip()
    if query.casefold() in ("geyser", "geysers", _GEYSER_ID.casefold()):
        return _GEYSER_ID
    if not context.items:
        return query

    item = resolve_item(context.items, query)
    if item is None and _CLASS_ID.fullmatch(query):
        # An id the model made up from the name, `Desc_ModularEngine_C` for what the export calls
        # Desc_SpaceElevatorPart_4_C: the name it was made from still says which item is meant.
        item = resolve_item(context.items, _name_in_class_id(query))
    if item is not None:
        return item.item_id
    if any(product.item_id == query for recipe in context.recipes for product in recipe.outputs):
        return query  # a real recipe product the export just has no item descriptor for
    matches = find_items(context.items, query)
    message = (
        f"{query!r} could be several items -- pass one of these by id or full name"
        if matches
        else f"unknown item {query!r}"
    )
    raise _ToolError(message, did_you_mean=[_item_summary(match) for match in matches])


def _main_site(recipe_id: str, context: OrchestratorContext) -> FactorySite | None:
    """The factory site running the most of `recipe_id`, by clock speed."""

    def running(site: FactorySite) -> float:
        return sum(p.clock_speed for p in site.placements if p.recipe_id == recipe_id)

    best = max(context.factory_sites, key=running, default=None)
    return best if best is not None and running(best) > 0 else None


def _site_summary(site: FactorySite, context: OrchestratorContext) -> dict[str, Any]:
    reference = reference_point({}, context)
    counts: dict[str, float] = {}
    for placement in site.placements:
        if placement.recipe_id is not None:
            counts[placement.recipe_id] = counts.get(placement.recipe_id, 0.0) + 1
    return {
        "x": site.position.x,
        "y": site.position.y,
        "z": site.position.z,
        "distance_from_base_m": distance(site.position, reference) / _CM_PER_M,
        "buildings": len(site.placements),
        "main_recipes": sorted(counts, key=lambda r: -counts[r])[:3],
    }


def _suggest_sites(
    raw_rates: Mapping[str, float],
    context: OrchestratorContext,
    accumulator: _ArtifactAccumulator,
) -> dict[str, Any]:
    """`{"suggested_sites": ...}`: the best free deposit for each raw resource in `raw_rates`, from
    the player's base -- also published as the answer's map, unless something already is. Water
    needs no deposit (extractors go on any open water), so it gets none."""
    if not context.resource_nodes:
        return {}
    reference = reference_point({}, context)
    suggestions: dict[str, Any] = {}
    locations = []
    for item_id in sorted(raw_rates):
        if item_id == _WATER_ID:
            continue
        ranked = rank_locations(
            item_id, context.resource_nodes, context.existing_placements, reference
        )
        if not ranked:
            continue
        best = ranked[0]
        locations.append(best)
        suggestions[item_id] = {
            "resource_node_id": best.resource_node_id,
            "purity": best.purity.value,
            "distance_m": best.distance_to_reference / _CM_PER_M,
        }
    if locations and accumulator.map_locations is None:
        accumulator.map_locations = tuple(locations)
        accumulator.map_reference = reference
    return {"suggested_sites": suggestions} if suggestions else {}


def _plan(
    context: OrchestratorContext,
    target_item_id: str,
    target_rate: float,
    recipe_choices: dict[str, str] | None,
    *,
    available_supply: Mapping[str, float] | None = None,
) -> ProductionGraph:
    """A plan from the recipes the player has unlocked, when those can make the item; otherwise
    from every recipe (see `_needs_unlocking`). Raises what `plan_production` raises."""
    raw_item_ids = raw_resource_ids(context)
    unlocked = _unlocked_recipes(context)
    if len(unlocked) < len(context.recipes):
        try:
            return plan_production(
                target_item_id,
                target_rate,
                unlocked,
                recipe_choices,
                raw_item_ids=raw_item_ids,
                available_supply=available_supply,
            )
        except ValueError:
            pass  # not with what's unlocked: plan with everything, and say what's missing
    return plan_production(
        target_item_id,
        target_rate,
        context.recipes,
        recipe_choices,
        raw_item_ids=raw_item_ids,
        available_supply=available_supply,
    )


def _unlocked_recipes(context: OrchestratorContext) -> tuple[Recipe, ...]:
    unlocked = context.unlocked_technology_ids
    if unlocked is None:
        return context.recipes
    return tuple(r for r in context.recipes if recipe_is_unlocked(r, unlocked))


def _needs_unlocking(graph: ProductionGraph, context: OrchestratorContext) -> dict[str, Any]:
    """`{"needs_unlocking": [...]}` naming, for each stage of `graph` the player hasn't unlocked,
    the easiest technology that unlocks it -- empty when every stage is unlocked or it's unknown
    what is."""
    unlocked = context.unlocked_technology_ids
    recipes = {recipe.recipe_id: recipe for recipe in context.recipes}
    locked = []
    for node in graph.nodes:
        recipe = recipes.get(node.recipe_id)
        if recipe is None or recipe_is_unlocked(recipe, unlocked) is not False:
            continue
        technology = easiest_unlock(recipe.unlockable_by, context.technologies)
        locked.append(
            {
                "recipe_id": recipe.recipe_id,
                "unlock_with": technology.technology_id if technology else None,
                "tier": technology.tier if technology else None,
                "kind": technology.kind if technology else None,
            }
        )
    return {"needs_unlocking": locked} if locked else {}


def _target_rate(args: dict[str, Any]) -> float:
    rate = float(args["target_rate_per_minute"])
    if not rate > 0:
        raise _ToolError(f"target_rate_per_minute must be a positive rate, got {rate:g}")
    return rate


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


_RATE_EPSILON = 1e-6

_CLASS_ID = re.compile(r"\b(?:Desc|Recipe|Build|BP|Schematic|Research)_[\w-]+?_C\b")
_CLASS_ID_AFFIX = re.compile(r"^(?:Desc|Recipe|Build|BP|Schematic|Research)_|_C$")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _name_in_class_id(class_id: str) -> str:
    """`Desc_ModularEngine_C` -> `Modular Engine`."""
    words = _CAMEL_BOUNDARY.sub(" ", _CLASS_ID_AFFIX.sub("", class_id)).replace("_", " ")
    return " ".join(words.split())


def display_names(context: OrchestratorContext) -> dict[str, str]:
    """The in-game name of every recipe, building, belt and pipe tier and item `context` knows,
    by id."""
    names = {recipe.recipe_id: recipe.name for recipe in context.recipes}
    names.update({building.building_id: building.name for building in context.buildings})
    names.update({item.item_id: item.name for item in context.items})
    names.update({t.technology_id: t.name for t in context.technologies})
    names.update({tier.building_id: tier.name for tier in context.transport_tiers})
    return names


def _names_mentioned(text: str, names: Mapping[str, str]) -> dict[str, str]:
    """The in-game name of every id in `text` that has one: tool results speak in ids, while the
    model answers — and its answer is checked — in the names the player knows."""
    return {class_id: names[class_id] for class_id in _CLASS_ID.findall(text) if class_id in names}


def _graph_summary(graph: ProductionGraph, context: OrchestratorContext) -> dict[str, Any]:
    """Each stage of a plan -- recipe, the building it runs in, how many, and their draw -- with
    the plan's net item balance and power."""
    summary: dict[str, Any] = {
        "stages": [
            {
                "recipe_id": node.recipe_id,
                "building_id": node.building_id,
                "machines": node.machine_count,
                **_stage_power(node.building_id, node.machine_count, context),
            }
            for node in graph.nodes
        ],
    }
    if context.recipes:
        summary["net_item_balance"] = balance(graph, context.recipes)
    if context.buildings:
        summary["net_power_draw_mw"] = power_balance(graph, context.buildings)
    if context.transport_tiers:
        summary["transport"] = _transport(graph, context)
    return summary


def _transport(graph: ProductionGraph, context: OrchestratorContext) -> list[dict[str, Any]]:
    """The belt or pipe each flow in `graph` needs, between the recipes it links ("outside" and
    "output" at the graph's edges)."""
    recipe_of = {node.node_id: node.recipe_id for node in graph.nodes}
    return [
        {
            "item_id": need.flow.item_id,
            "per_minute": need.flow.amount_per_minute,
            "from": recipe_of.get(need.flow.source_node_id or "", "outside"),
            "to": recipe_of.get(need.flow.target_node_id or "", "output"),
            "tier": need.tier.building_id if need.tier else None,
            "lines": need.lines,
        }
        for need in transport_needs(graph.flows, context.items, context.transport_tiers)
    ]


def _stage_power(
    building_id: str, machines: float, context: OrchestratorContext
) -> dict[str, float]:
    """`{"power_mw": ...}` for `machines` of `building_id` -- empty when the knowledge base doesn't
    list the building: its draw is extra information, not worth failing a tool over."""
    if not any(building.building_id == building_id for building in context.buildings):
        return {}
    stage = ProductionNode(
        node_id=building_id, recipe_id="", building_id=building_id, machine_count=machines
    )
    return {"power_mw": power_balance(ProductionGraph(nodes=(stage,), flows=()), context.buildings)}


def _with_spare_power(result: dict[str, Any], context: OrchestratorContext) -> dict[str, Any]:
    """`result`, plus what the player's grid has to spare, when the save tells."""
    spare = spare_power_mw(context)
    return result if spare is None else {**result, "grid_spare_power_mw": spare}


def spare_power_mw(context: OrchestratorContext) -> float | None:
    """What the player's grid can still supply: its capacity minus its draw, from the save. `None`
    with no save loaded, or no way to tell the capacity."""
    if context.existing_graph is None:
        return None
    try:
        draw, capacity = _existing_power(context)
    except ValueError:
        return None
    return None if capacity is None else capacity - draw
