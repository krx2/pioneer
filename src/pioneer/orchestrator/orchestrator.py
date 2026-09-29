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

**Progress.** Given an `on_event` sink, `handle_query` says what it's doing while it works (see
`base.ProgressSink`): that it's thinking, which tool it's running on what, and when text a
streaming transport already passed on turned out to be a round of tool calls rather than the
answer. The page shows those as the answer's live status.

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

from collections.abc import Sequence
from typing import Any

from pioneer.contracts import (
    ResponseArtifact,
    TransportError,
)
from pioneer.orchestrator.base import (
    OrchestratorContext,
    OrchestratorUnavailable,
    ProgressSink,
    ToolCallingLLM,
    _ArtifactAccumulator,
)
from pioneer.orchestrator.items import display_names
from pioneer.orchestrator.progress import describe_tool_call
from pioneer.orchestrator.tool_calls import (
    _assistant_message,
    _execute_tool,
    _resolve_tool_name,
    _tool_calls_written_as_text,
)
from pioneer.orchestrator.tools import _build_tools
from pioneer.qa_engine import ChatCompletion

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
    "Answer only what was asked. A simple lookup -- how much the factory produces, an item's "
    "balance, what a recipe needs -- is one tool call: make it, then report its numbers (a "
    "short list or table is fine) with no diagnosis, recommendations or follow-up offers the "
    "player didn't ask for. Diagnose problems only when asked what's wrong or how to improve.\n\n"
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
    on_event: ProgressSink | None = None,
) -> ResponseArtifact | OrchestratorUnavailable:
    emit = on_event or _ignore
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

    for round_number in range(max_tool_rounds):
        emit({"type": "status", "text": "Thinking" if round_number == 0 else "Reading the results"})
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

        emit({"type": "discard"})
        messages.append(_assistant_message(message, tool_calls))
        for call in tool_calls:
            name = _resolve_tool_name(call["name"], tools_by_name) or str(call["name"])
            arguments = call.get("arguments")
            emit(
                {
                    "type": "tool",
                    "name": name,
                    "text": describe_tool_call(
                        name, arguments if isinstance(arguments, dict) else {}, names
                    ),
                }
            )
            result_message = _execute_tool(call, tools_by_name, names)
            messages.append(result_message)
            accumulator.grounding.append(result_message["content"])

    return OrchestratorUnavailable(
        reason=f"exceeded {max_tool_rounds} tool-call rounds without a final answer"
    )


def _ignore(event: dict[str, Any]) -> None:
    pass


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
