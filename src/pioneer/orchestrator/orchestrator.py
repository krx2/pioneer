"""The routing loop: hand the player's question and the tools to the model, run whichever tools it
calls, feed the results back, and repeat until it answers in plain text. Which tools the model
reaches for is the intent classification; there's no separate call for it.

- Tool handlers return real module output, which goes into the `ResponseArtifact` as is; the model
  only phrases the numbers. Every result it saw is kept as the answer's `grounding`.
- Nothing a tool does escapes as an exception: failures come back to the model as an
  `{"error": ...}` result (`_run_tool`). A tool call written as text is run like any other.
- Only the newest turns of `history` that fit `_HISTORY_BUDGET_CHARS` are passed on.
- A conversation's accepted plan comes in as `OrchestratorContext.plan`; an answer that plans
  something offers its plan as `ResponseArtifact.plan_proposal`, withdrawn if a later tool draws
  another graph.
- With an `on_event` sink, `handle_query` reports what it's doing (`base.ProgressSink`).

The `ToolCallingLLM` transport is injected, so the loop runs against a scripted fake in tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pioneer.contracts import (
    ResponseArtifact,
    TransportError,
)
from pioneer.knowledge_base import recipe_is_unlocked
from pioneer.orchestrator.base import (
    ArtifactAccumulator,
    OrchestratorContext,
    OrchestratorUnavailable,
    ProgressSink,
    ToolCallingLLM,
)
from pioneer.orchestrator.items import display_names
from pioneer.orchestrator.plans import deposits_built, plan_note, progress_of
from pioneer.orchestrator.progress import describe_tool_call
from pioneer.orchestrator.tool_calls import (
    assistant_message,
    execute_tool,
    resolve_tool_name,
    tool_calls_written_as_text,
)
from pioneer.orchestrator.tools import build_tools
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
    "If a tool result has `missing_recipes`, the player has turned off recipes the plan needs: "
    "there is no plan, so don't describe one. Say, for each entry, which item needs a recipe "
    "turned on and that any one of its `any_of` recipes will do; for one they haven't unlocked "
    "yet, say what unlocks it (`unlock_with`). Several entries are all needed. The page offers a "
    "button that opens the player's recipe list on exactly those recipes.\n\n"
    "A plan's `build_sites` says where to build it: one site with every deposit close by, or "
    "several, each building the parts its own deposits are for (`builds`) and sending them on "
    "(`sends`). Give, per site, its deposits as listed -- how many nodes of which purity, the "
    "extractor, its clock speed and power shards -- and for several sites what each builds and "
    "sends where. Never pick deposits or sites yourself.\n\n"
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
    names = display_names(context)
    system = f"{_SYSTEM_PROMPT}\n\n{context_note(context)}"
    accumulator = ArtifactAccumulator(
        grounding=[question, *(text for turn in history for text in turn)]
    )
    if context.plan is not None:
        note = plan_note(
            context.plan,
            names,
            progress_of(context.plan, context),
            deposits_built(context.plan, context),
        )
        system += f"\n\n{note}"
        accumulator.grounding.append(note)
    tools = build_tools(
        context, accumulator, qa_chat_completion, llm_base_url, llm_model, llm_api_key
    )
    tool_schemas = [tool.as_schema() for tool in tools]
    tools_by_name = {tool.name: tool for tool in tools}

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
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

        tool_calls = message.get("tool_calls") or tool_calls_written_as_text(
            message.get("content"), tools_by_name
        )
        if not tool_calls:
            proposal = accumulator.plan_proposal
            return ResponseArtifact(
                response_id=response_id,
                chat=message.get("content") or "",
                graph=accumulator.graph,
                map_locations=accumulator.map_locations,
                map_reference=accumulator.map_reference,
                factory_sites=accumulator.factory_sites,
                build_sites=accumulator.site_plan,
                question=question,
                grounding=tuple(accumulator.grounding),
                missing_recipes=tuple(dict.fromkeys(accumulator.missing_recipes)),
                plan_proposal=(
                    proposal
                    if proposal is not None and proposal.graph is accumulator.graph
                    else None
                ),
            )

        emit({"type": "discard"})
        messages.append(assistant_message(message, tool_calls))
        for call in tool_calls:
            name = resolve_tool_name(call["name"], tools_by_name) or str(call["name"])
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
            result_message = execute_tool(call, tools_by_name, names)
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
    assuming it."""
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
    if context.enabled_recipe_ids is not None:
        lines.append(_recipe_choice_note(context))
    state = context.game_state
    lines.append(
        f"Live server: session {state.session_name or 'unnamed'}, game phase {state.phase}, "
        f"tech tier {state.tech_tier}."
        if state is not None
        else "Live server state: unavailable."
    )
    return "Data available in this conversation:\n" + "\n".join(f"- {line}" for line in lines)


def _recipe_choice_note(context: OrchestratorContext) -> str:
    enabled = context.enabled_recipe_ids or frozenset()
    unlocked = context.unlocked_technology_ids
    turned_off = turned_on_locked = 0
    for recipe in context.recipes:
        is_unlocked = recipe_is_unlocked(recipe, unlocked) is not False
        if is_unlocked and recipe.recipe_id not in enabled:
            turned_off += 1
        elif not is_unlocked and recipe.recipe_id in enabled:
            turned_on_locked += 1
    return (
        f"Recipe choice: the player picked {len(enabled)} of {len(context.recipes)} recipes for "
        f"plans -- turning off {turned_off} they have unlocked and turning on {turned_on_locked} "
        "they haven't yet. Plans use only those."
    )


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
