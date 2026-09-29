"""Tool handlers for planning and game knowledge: items and recipes, new production
chains and expansions, recipe comparisons, power, unlocks, build locations and free-form
game questions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pioneer.contracts import (
    ChangeAction,
    ProductionGraph,
    Recipe,
)
from pioneer.expansion_advisor import advise_expansion
from pioneer.knowledge_base import (
    easiest_unlock,
    find_items,
    recipe_is_unlocked,
    unlock_order,
)
from pioneer.location_advisor import rank_locations
from pioneer.orchestrator.base import (
    _CM_PER_M,
    OrchestratorContext,
    _ArtifactAccumulator,
    _ToolError,
)
from pioneer.orchestrator.existing_factory import (
    _existing_item_balance,
    _main_site,
    _site_summary,
    _with_spare_power,
    reference_point,
)
from pioneer.orchestrator.items import _item_summary, _resolve_item_id, raw_resource_ids
from pioneer.orchestrator.summaries import _graph_summary, _per_item, _stage_power
from pioneer.production_planner.planner import plan_production, recipes_for_output
from pioneer.qa_engine import ChatCompletion, LLMUnavailable, NoRelevantPassages, QAAnswer
from pioneer.qa_engine import answer_question as qa_answer_question
from pioneer.verifier import (
    added_machines,
    balance,
    extractors_needed,
    power_balance,
    power_plants,
)

_DEFAULT_COMPARISON_RATE = 10.0
_WATER_ID = "Desc_Water_C"


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


_RATE_EPSILON = 1e-6
