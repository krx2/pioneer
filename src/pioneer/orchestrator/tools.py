"""The tools the model is offered: each one's name, description and parameter schema,
bound to its handler."""

from __future__ import annotations

from pioneer.orchestrator.base import OrchestratorContext, _ArtifactAccumulator, _Tool
from pioneer.orchestrator.factory_tools import (
    _handle_diagnose_factory,
    _handle_factory_item_balance,
    _handle_factory_power,
    _handle_show_existing_factory,
)
from pioneer.orchestrator.planning_tools import (
    _handle_answer_question,
    _handle_compare_recipes,
    _handle_expand_existing_factory,
    _handle_find_item,
    _handle_list_recipes,
    _handle_plan_power,
    _handle_plan_production,
    _handle_plan_unlocks,
    _handle_rank_locations,
)
from pioneer.qa_engine import ChatCompletion

_ITEM_DESCRIPTION = "Item id (e.g. Desc_IronPlate_C) or in-game name (e.g. 'Iron Plate')"
_ITEM_HINT = "(item id or in-game name)"


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
            name="factory_item_balance",
            description=(
                "The player's existing factory's per-item rates, from the latest save: how much "
                "of each item it produces and uses a minute, and the net surplus (+) or deficit "
                "(-). Use for 'what/how much does my factory produce', 'net production', 'item "
                "balance' questions -- pass item for just one item. Requires save data."
            ),
            parameters={
                "type": "object",
                "properties": {"item": {"type": "string", "description": _ITEM_DESCRIPTION}},
            },
            handler=lambda args: _handle_factory_item_balance(args, context),
        ),
        _Tool(
            name="factory_power",
            description=(
                "The player's existing power grid, from the latest save: total draw, generating "
                "capacity and what's spare, which buildings draw it, which generators make it and "
                "the fuel they burn a minute. Use for 'how much power do I use/have' questions. "
                "Requires save data."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda args: _handle_factory_power(context),
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
                "for 'how do I get N MW' questions; factory_power tells the current draw and "
                "capacity."
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
                "nowhere to go, belts carrying more than their tier can. Use for 'what's wrong' "
                "questions; for how much is produced use factory_item_balance, for power use "
                "factory_power. Requires save data."
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
