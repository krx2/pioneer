"""The tools the model is offered: each one's name, description and parameter schema, bound to its
handler. The plan's own tools are offered only in a conversation that has a plan: elsewhere they'd
be tokens of a small context window spent on nothing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pioneer.orchestrator.base import ArtifactAccumulator, OrchestratorContext, Tool
from pioneer.orchestrator.factory_tools import (
    handle_diagnose_factory,
    handle_factory_item_balance,
    handle_factory_power,
    handle_show_existing_factory,
)
from pioneer.orchestrator.plan_tools import handle_revise_plan, handle_show_plan
from pioneer.orchestrator.planning_tools import (
    handle_answer_question,
    handle_compare_recipes,
    handle_expand_existing_factory,
    handle_find_item,
    handle_list_recipes,
    handle_plan_power,
    handle_plan_production,
    handle_plan_unlocks,
    handle_rank_locations,
)
from pioneer.qa_engine import ChatCompletion


@dataclass(frozen=True)
class _ToolEnv:
    """What a handler may need besides the model's arguments."""

    context: OrchestratorContext
    accumulator: ArtifactAccumulator
    qa_chat_completion: ChatCompletion
    llm_base_url: str
    llm_model: str
    llm_api_key: str | None


@dataclass(frozen=True)
class _ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any], _ToolEnv], dict[str, Any]]


def _params(*required: str, **properties: dict[str, Any]) -> dict[str, Any]:
    """A JSON schema object with `properties`, of which `required` must be given."""
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    return schema


def _string(description: str | None = None) -> dict[str, Any]:
    return {"type": "string", **({"description": description} if description else {})}


def _number(description: str | None = None) -> dict[str, Any]:
    return {"type": "number", **({"description": description} if description else {})}


def _mapping(description: str, value_type: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "additionalProperties": {"type": value_type},
    }


_ITEM_HINT = "(item id or in-game name)"
_ITEM = _string("Item id (e.g. Desc_IronPlate_C) or in-game name (e.g. 'Iron Plate')")
_RECIPE_CHOICES = _mapping(
    "Optional: item -> recipe id, to use a specific (e.g. alternate) recipe for that item "
    "instead of the default standard one",
    "string",
)
_TARGET = _params(
    "target_item_id",
    "target_rate_per_minute",
    target_item_id=_ITEM,
    target_rate_per_minute=_number(),
    recipe_choices=_RECIPE_CHOICES,
)

_TOOLS = (
    _ToolSpec(
        name="find_item",
        description=(
            "Look up items by in-game name or id, e.g. 'reinforced plate' -> "
            "Desc_IronPlateReinforced_C. Use when unsure which item the player means."
        ),
        parameters=_params("query", query=_string()),
        run=lambda args, env: handle_find_item(args, env.context),
    ),
    _ToolSpec(
        name="list_recipes_for_item",
        description=(
            "List every recipe producing an item -- the standard one and any alternates -- "
            "with its building and per-machine rates. Use for 'how is X made' or 'which "
            "alternate recipe should I use for X' questions."
        ),
        parameters=_params("item", item=_ITEM),
        run=lambda args, env: handle_list_recipes(args, env.context),
    ),
    _ToolSpec(
        name="plan_production",
        description=(
            "Plan a brand-new production chain from raw resources up to a target item and "
            "rate: each stage's recipe, building, machine count and power, and where to "
            "build it (build_sites). Use for 'I want to produce N/min of X' when nothing "
            "needs to be extended."
        ),
        parameters=_TARGET,
        run=lambda args, env: handle_plan_production(args, env.context, env.accumulator),
    ),
    _ToolSpec(
        name="expand_existing_factory",
        description=(
            "Given a new target item and rate, compute the minimal change (extend/add) to the "
            "player's *existing* factory instead of planning from scratch -- the existing "
            "factory's spare output is used first. Reports the buildings to add, the power "
            "they draw and what the grid has to spare. Requires save data."
        ),
        parameters=_TARGET,
        run=lambda args, env: handle_expand_existing_factory(args, env.context, env.accumulator),
    ),
    _ToolSpec(
        name="show_existing_factory",
        description=(
            "Draw a production graph of what the player has ALREADY BUILT, from the latest "
            "save. When they ask about an item ('how does my factory make Computers?'), pass "
            "item_id: the stages making it, across all their factories. Pass site_id only "
            "when they name one of their factories, as an earlier result listed it. With "
            "neither: the whole factory, or a list of its factories if too big to draw. "
            "Flows are what the recipes imply, not traced belts. Requires save data."
        ),
        parameters=_params(site_id=_string(), item_id=_ITEM),
        run=lambda args, env: handle_show_existing_factory(args, env.context, env.accumulator),
    ),
    _ToolSpec(
        name="factory_item_balance",
        description=(
            "The player's existing factory's per-item rates, from the latest save: how much "
            "of each item it produces and uses a minute, and the net surplus (+) or deficit "
            "(-). Use for 'what/how much does my factory produce', 'net production', 'item "
            "balance' questions -- pass item for just one item. Requires save data."
        ),
        parameters=_params(item=_ITEM),
        run=lambda args, env: handle_factory_item_balance(args, env.context),
    ),
    _ToolSpec(
        name="factory_power",
        description=(
            "The player's existing power grid, from the latest save: total draw, generating "
            "capacity and what's spare, which buildings draw it, which generators make it and "
            "the fuel they burn a minute. Use for 'how much power do I use/have' questions. "
            "Requires save data."
        ),
        parameters=_params(),
        run=lambda _args, env: handle_factory_power(env.context),
    ),
    _ToolSpec(
        name="compare_recipes",
        description=(
            "Compare every recipe for an item -- the standard one and each alternate -- as a "
            "whole production chain at the given rate: machines, power, the resources it "
            "takes in, byproducts, and whether the player has it unlocked. Use for 'which "
            "recipe/alternate is best for X' questions."
        ),
        parameters=_params(
            "item", item=_ITEM, target_rate_per_minute=_number("Rate to compare at, default 10")
        ),
        run=lambda args, env: handle_compare_recipes(args, env.context, env.accumulator),
    ),
    _ToolSpec(
        name="plan_power",
        description=(
            "Ways to generate a given amount of power: for each generator and fuel, how many "
            "generators, the fuel and water they need at full load, the water extractors for "
            "that, and waste. Name a fuel to also get the production chain that makes it. Use "
            "for 'how do I get N MW' questions; factory_power tells the current draw and "
            "capacity."
        ),
        parameters=_params(
            "target_mw",
            target_mw=_number(),
            fuel=_string("Optional: only plants burning this fuel " + _ITEM_HINT),
        ),
        run=lambda args, env: handle_plan_power(args, env.context, env.accumulator),
    ),
    _ToolSpec(
        name="plan_unlocks",
        description=(
            "What the player still has to unlock to make an item: the technologies its "
            "production chain needs, with their prerequisites, in the order to get them, and "
            "what each costs. Use for 'what do I need to research/unlock for X' questions."
        ),
        parameters=_params("item", item=_ITEM),
        run=lambda args, env: handle_plan_unlocks(args, env.context, env.accumulator),
    ),
    _ToolSpec(
        name="rank_build_locations",
        description=(
            "Rank unclaimed resource deposits of a given item (or 'geyser'), best first, for "
            "where to build next. Use for 'where should I build/mine X' questions. With "
            "rate_per_minute: the free deposits close together that give that much, each "
            "with its extractor, clock speed and power shards (build_sites). Distances "
            "are measured from the given reference point, else from the middle of the "
            "player's existing buildings."
        ),
        parameters=_params(
            "item_id",
            item_id=_ITEM,
            rate_per_minute=_number("Optional: how much of it a minute the deposits must give"),
            count={"type": "integer", "description": "Max locations to return, default 5"},
            reference_x=_number("Reference point to measure distance from"),
            reference_y=_number(),
            reference_z=_number(),
        ),
        run=lambda args, env: handle_rank_locations(args, env.context, env.accumulator),
    ),
    _ToolSpec(
        name="diagnose_factory_problems",
        description=(
            "Scan the player's existing factory for resource deficits/surpluses, power "
            "blackouts, and its wiring: machines no belt or pipe feeds, products with "
            "nowhere to go, belts carrying more than their tier can. Use for 'what's wrong' "
            "questions; for how much is produced use factory_item_balance, for power use "
            "factory_power. Requires save data."
        ),
        parameters=_params(),
        run=lambda _args, env: handle_diagnose_factory(env.context),
    ),
    _ToolSpec(
        name="answer_game_question",
        description=(
            "Answer a free-form Satisfactory game-mechanics question via retrieval over the "
            "knowledge base/wiki. Use for general 'how does X work' questions."
        ),
        parameters=_params("question", question=_string()),
        run=lambda args, env: handle_answer_question(
            args,
            env.context,
            env.accumulator,
            env.qa_chat_completion,
            env.llm_base_url,
            env.llm_model,
            env.llm_api_key,
        ),
    ),
)

_PLAN_TOOLS = (
    _ToolSpec(
        name="show_plan",
        description=(
            "Draw the player's plan for this conversation as it stands, with each stage's "
            "recipe, building, machines and power, and how much of it the player has built "
            "by the latest save. Use when they ask to see their plan or how far they are."
        ),
        parameters=_params(),
        run=lambda _args, env: handle_show_plan(env.context, env.accumulator),
    ),
    _ToolSpec(
        name="revise_plan",
        description=(
            "Change the player's plan for this conversation, keeping everything they didn't "
            "ask to change: a target's rate, another target besides the others, a target "
            "dropped, another recipe for an item. Reports what changes -- machines to add, "
            "remove, or reconfigure to another recipe, and how many of those the player has "
            "built already -- and draws the revised plan, which the player then accepts on the "
            "page. Use for every change to the plan, never plan_production."
        ),
        parameters=_params(
            targets=_mapping(
                "Item -> rate per minute: a target of the plan gets the new rate, any other "
                "item becomes a new target besides the others " + _ITEM_HINT,
                "number",
            ),
            remove_targets={
                "type": "array",
                "description": "Targets to drop from the plan " + _ITEM_HINT,
                "items": {"type": "string"},
            },
            recipe_choices=_mapping(
                "Item -> recipe id: run that item's stage with this recipe from now on", "string"
            ),
        ),
        run=lambda args, env: handle_revise_plan(args, env.context, env.accumulator),
    ),
)


def build_tools(
    context: OrchestratorContext,
    accumulator: ArtifactAccumulator,
    qa_chat_completion: ChatCompletion,
    llm_base_url: str,
    llm_model: str,
    llm_api_key: str | None,
) -> list[Tool]:
    env = _ToolEnv(context, accumulator, qa_chat_completion, llm_base_url, llm_model, llm_api_key)
    specs = _TOOLS + (_PLAN_TOOLS if context.plan is not None else ())
    return [
        Tool(
            spec.name, spec.description, spec.parameters, lambda args, run=spec.run: run(args, env)
        )
        for spec in specs
    ]
