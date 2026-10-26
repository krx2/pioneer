"""Tool handlers for planning and game knowledge: items and recipes, new production
chains and expansions, recipe comparisons, power, unlocks, build locations and free-form
game questions.

Every plan is made from the recipes the player may use (see `_plan`): the ones they ticked, or by
default the ones they've unlocked. A plan the ticked recipes can't make comes back as an error with
`missing_recipes` -- which recipes it would take -- rather than as a plan.

A new production chain or expansion is also offered to the player as their conversation's plan
(see `plans.propose_plan`) -- unless the conversation has one already: then it's a separate plan,
and the model is told that changing theirs is `revise_plan`'s job."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pioneer.common import raw_resource_ids, recipes_for_output
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
    CM_PER_M,
    ArtifactAccumulator,
    OrchestratorContext,
    ToolError,
)
from pioneer.orchestrator.existing_factory import (
    existing_item_balance,
    main_site,
    reference_point,
    site_summary,
    with_spare_power,
)
from pioneer.orchestrator.items import item_summary, resolve_item_id
from pioneer.orchestrator.plans import propose_plan
from pioneer.orchestrator.reservations import reservation_summary, reserve, unreserved
from pioneer.orchestrator.sites import build_sites
from pioneer.orchestrator.summaries import graph_summary, per_item, stage_power
from pioneer.production_planner import (
    MissingRecipe,
    find_missing_recipes,
    plan_targets,
)
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
_SEPARATE_PLAN = (
    "this is a separate plan, not a change to the player's plan for this conversation: to change "
    "theirs -- or add this target to it -- call revise_plan"
)


def handle_find_item(args: dict[str, Any], context: OrchestratorContext) -> dict[str, Any]:
    if not context.items:
        raise ToolError("no knowledge base loaded -- item lookup is unavailable")
    matches = find_items(context.items, str(args["query"]), limit=8)
    return {"items": [item_summary(item) for item in matches]}


def handle_list_recipes(args: dict[str, Any], context: OrchestratorContext) -> dict[str, Any]:
    item_id = resolve_item_id(args["item"], context)
    return {
        "item_id": item_id,
        "raw_resource": item_id in raw_resource_ids(context.items),
        "recipes": [
            {
                "recipe_id": recipe.recipe_id,
                "name": recipe.name,
                "alternate": recipe.is_alternate,
                "unlocked": recipe_is_unlocked(recipe, context.unlocked_technology_ids),
                "enabled": _is_usable(recipe, context),
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


def handle_plan_production(
    args: dict[str, Any], context: OrchestratorContext, accumulator: ArtifactAccumulator
) -> dict[str, Any]:
    target_item_id = resolve_item_id(args["target_item_id"], context)
    target_rate = target_rate_arg(args)
    try:
        graph = _plan(context, target_item_id, target_rate, recipe_choices_arg(args, context))
    except PlanningBlocked as blocked:
        return blocked.result(context, accumulator)
    except (ValueError, KeyError) as error:
        return {"error": str(error)}

    summary = {  # built before publishing: it fails on a building the knowledge base doesn't list
        "target_item_id": target_item_id,
        "target_rate_per_minute": target_rate,
        **graph_summary(graph, context),
        **needs_unlocking(graph, context),
    }
    accumulator.graph = graph
    raw_inputs = per_item(f for f in graph.flows if f.source_node_id is None)
    sites_found, sites = build_sites(raw_inputs, context, accumulator, graph)
    if context.plan is None:
        accumulator.plan_proposal = propose_plan(
            context, {target_item_id: target_rate}, graph, sites=sites
        )
    else:
        summary["note"] = _SEPARATE_PLAN
    summary.update(sites_found)
    return with_spare_power(summary, context)


def handle_expand_existing_factory(
    args: dict[str, Any], context: OrchestratorContext, accumulator: ArtifactAccumulator
) -> dict[str, Any]:
    if context.existing_graph is None:
        return {
            "error": "no existing factory state available (no save loaded) -- cannot compute an "
            "expansion; offer a from-scratch plan instead, or tell the player to load a save"
        }
    target_item_id = resolve_item_id(args["target_item_id"], context)
    target_rate = target_rate_arg(args)
    raw_item_ids = raw_resource_ids(context.items)
    try:
        existing_balance = existing_item_balance(context)
        spare = unreserved(existing_balance, context)  # what other plans reserved isn't spare
        surplus = {
            item_id: rate
            for item_id, rate in spare.items()
            if rate > 0 and item_id not in raw_item_ids
        }
        additions = _plan(
            context,
            target_item_id,
            target_rate,
            recipe_choices_arg(args, context),
            available_supply=surplus,
        )
    except PlanningBlocked as blocked:
        return blocked.result(context, accumulator)
    except (ValueError, KeyError) as error:
        return {"error": str(error)}

    change_set = advise_expansion(context.existing_graph, additions)
    buildings = {node.recipe_id: node.building_id for node in change_set.resulting_graph.nodes}
    at_site = {
        change.recipe_id: site
        for change in change_set.changes
        if change.action is ChangeAction.EXTEND
        and (site := main_site(change.recipe_id, context)) is not None
    }
    changes = [
        {
            "action": change.action.value,
            "recipe_id": change.recipe_id,
            "building_id": buildings[change.recipe_id],
            "additional_machine_count": change.additional_machine_count,
            **stage_power(buildings[change.recipe_id], change.additional_machine_count, context),
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
    items_in_plan = {flow.item_id for flow in additions.flows}
    raw_needed = per_item(
        flow
        for flow in additions.flows
        if flow.source_node_id is None and flow.item_id in raw_item_ids
    )
    drawn = per_item(
        flow for flow in additions.flows if flow.source_node_id is None and flow.item_id in surplus
    )
    # Ore the factory mines to spare is used before any new deposit: reserved like the rest.
    mined_spare = {
        item_id: min(rate, spare[item_id])
        for item_id, rate in raw_needed.items()
        if spare.get(item_id, 0.0) > 0
    }
    # The ore goes to the factory being extended: its deposits are sought from there.
    near = extended_sites[0].position if extended_sites else None
    reservations = reserve(drawn | mined_spare, context, spare, near=near)
    uncovered = {
        item_id: rate - mined_spare.get(item_id, 0.0)
        for item_id, rate in raw_needed.items()
        if rate > mined_spare.get(item_id, 0.0) + _RATE_EPSILON
    }
    sites_found, sites = build_sites(uncovered, context, accumulator, reference=near)
    if change_set.resulting_graph.nodes:  # nothing to build is nothing to draw, nor to plan
        accumulator.graph = change_set.resulting_graph
        if context.plan is None:
            accumulator.plan_proposal = propose_plan(
                context,
                {target_item_id: target_rate},
                change_set.resulting_graph,
                available_supply=surplus,
                sites=sites,
                reservations=reservations,
            )
    result: dict[str, Any] = {
        "target_item_id": target_item_id,
        "target_rate_per_minute": target_rate,
        "changes": changes,
        "drawn_from_existing_surplus_per_minute": drawn,
        "raw_resources_needed_per_minute": raw_needed,
        "spare_extraction_per_minute": {
            item_id: spare[item_id] for item_id in raw_needed if spare.get(item_id, 0.0) > 0
        },
        "existing_shortfalls_per_minute": {
            item_id: -rate
            for item_id, rate in existing_balance.items()
            if rate < 0 and item_id in items_in_plan and item_id not in raw_item_ids
        },
    }
    result.update(needs_unlocking(additions, context))
    if context.plan is not None:
        result["note"] = _SEPARATE_PLAN
    if extended_sites:
        result["sites"] = {site.site_id: site_summary(site, context) for site in extended_sites}
    if reservations:
        result["reserved_from_factories"] = reservation_summary(reservations, context, spare)
    result.update(sites_found)
    if all("power_mw" in change for change in changes):
        added = added_machines(change_set.resulting_graph)
        result["added_power_draw_mw"] = power_balance(added, context.buildings)
    return with_spare_power(result, context)


def handle_compare_recipes(
    args: dict[str, Any], context: OrchestratorContext, accumulator: ArtifactAccumulator
) -> dict[str, Any]:
    item_id = resolve_item_id(args["item"], context)
    rate = target_rate_arg(
        {"target_rate_per_minute": args.get("target_rate_per_minute", _DEFAULT_COMPARISON_RATE)}
    )
    candidates = recipes_for_output(item_id, context.recipes)
    if not candidates:
        raise ToolError(f"no recipe makes {item_id} -- nothing to compare")
    primary = [r for r in candidates if r.outputs[0].item_id == item_id] or list(candidates)

    options: list[dict[str, Any]] = []
    for recipe in primary:
        enabled = _is_usable(recipe, context)
        try:  # a recipe the player turned off is compared as if it were on, and said to be off
            graph = _plan(
                context, item_id, rate, {item_id: recipe.recipe_id}, also_allowed=(recipe,)
            )
        except PlanningBlocked as blocked:
            options.append(
                {"recipe_id": recipe.recipe_id, "enabled": enabled}
                | blocked.result(context, accumulator)
            )
            continue
        except ValueError as error:
            options.append({"recipe_id": recipe.recipe_id, "error": str(error)})
            continue
        net = balance(graph, context.recipes)
        option: dict[str, Any] = {
            "recipe_id": recipe.recipe_id,
            "alternate": recipe.is_alternate,
            "unlocked": recipe_is_unlocked(recipe, context.unlocked_technology_ids),
            "enabled": enabled,
            "stages": len(graph.nodes),
            "machines": sum(node.machine_count for node in graph.nodes),
            "inputs_per_minute": {i: -rate for i, rate in net.items() if rate < -_RATE_EPSILON},
            "byproducts_per_minute": {
                i: rate for i, rate in net.items() if rate > _RATE_EPSILON and i != item_id
            },
            **needs_unlocking(graph, context),
        }
        if all(stage_power(n.building_id, 1, context) for n in graph.nodes):
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


def handle_plan_power(
    args: dict[str, Any], context: OrchestratorContext, accumulator: ArtifactAccumulator
) -> dict[str, Any]:
    if not context.buildings or not context.items:
        raise ToolError("no knowledge base loaded -- power can't be planned")
    target_mw = float(args["target_mw"])
    if not target_mw > 0:
        raise ToolError(f"target_mw must be positive, got {target_mw:g}")
    fuel_id = resolve_item_id(args["fuel"], context) if args.get("fuel") else None
    plants = [
        p
        for p in power_plants(target_mw, context.buildings, context.items)
        if fuel_id is None or p.fuel_item_id == fuel_id
    ]
    if not plants:
        raise ToolError(f"no generator burns {fuel_id}" if fuel_id else "no generator data")
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
                    **stage_power(extractor.building_id, count, context),
                }
        if plant.byproduct_item_id is not None:
            option["waste_id"] = plant.byproduct_item_id
            option["waste_per_minute"] = plant.byproduct_per_minute
        options.append(option)

    result: dict[str, Any] = {"target_mw": target_mw, "options": options}
    if fuel_id is not None:
        result["fuel_supply"] = _fuel_supply(
            fuel_id, plants[0].fuel_per_minute, context, accumulator
        )
    return with_spare_power(result, context)


def _fuel_supply(
    fuel_id: str,
    per_minute: float,
    context: OrchestratorContext,
    accumulator: ArtifactAccumulator,
) -> dict[str, Any]:
    """How the named fuel gets made: mined, or the production chain for it."""
    if fuel_id in raw_resource_ids(context.items) or not recipes_for_output(
        fuel_id, context.recipes
    ):
        return {"mined": True, "per_minute": per_minute}
    try:
        graph = _plan(context, fuel_id, per_minute, None)
    except PlanningBlocked as blocked:
        return blocked.result(context, accumulator)
    except ValueError as error:
        return {"error": str(error)}
    return {"per_minute": per_minute, **graph_summary(graph, context)}


def handle_plan_unlocks(
    args: dict[str, Any], context: OrchestratorContext, accumulator: ArtifactAccumulator
) -> dict[str, Any]:
    if not context.technologies:
        raise ToolError("no technology data loaded -- unlocks can't be planned")
    item_id = resolve_item_id(args["item"], context)
    if item_id in raw_resource_ids(context.items):
        raise ToolError(f"{item_id} is a raw resource: it's mined, not unlocked")
    try:
        graph = _plan(context, item_id, 1.0, None)
    except PlanningBlocked as blocked:
        return blocked.result(context, accumulator)
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


def handle_rank_locations(
    args: dict[str, Any], context: OrchestratorContext, accumulator: ArtifactAccumulator
) -> dict[str, Any]:
    if not context.resource_nodes:
        raise ToolError(
            "no resource node data loaded (assets/resource_nodes.json is missing) -- build "
            "locations can't be ranked"
        )
    item_id = resolve_item_id(args["item_id"], context)
    reference = reference_point(args, context)
    if args.get("rate_per_minute") is not None:
        rate = target_rate_arg({"target_rate_per_minute": args["rate_per_minute"]})
        sites, _ = build_sites(
            {item_id: rate}, context, accumulator, reference=reference, replace_map=True
        )
        if sites:
            return {"reference": {"x": reference.x, "y": reference.y, "z": reference.z}, **sites}
    count = int(args.get("count", 5))
    if count < 1:
        raise ToolError(f"count must be at least 1, got {count}")
    ranked = rank_locations(item_id, context.resource_nodes, context.existing_placements, reference)
    top = ranked[:count]
    accumulator.map_locations = top
    accumulator.map_reference = reference
    accumulator.site_plan = None
    return {
        "reference": {"x": reference.x, "y": reference.y, "z": reference.z},
        "locations": [
            {
                "resource_node_id": location.resource_node_id,
                "purity": location.purity.value,
                "distance_m": location.distance_to_reference / CM_PER_M,
                "score": location.score,
            }
            for location in top
        ],
    }


def handle_answer_question(
    args: dict[str, Any],
    context: OrchestratorContext,
    accumulator: ArtifactAccumulator,
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


class PlanningBlocked(Exception):
    """The recipes the player ticked can't make an item every recipe could: `missing` says which
    of the others it would take."""

    def __init__(self, target_item_id: str, missing: tuple[MissingRecipe, ...]) -> None:
        super().__init__(f"the recipes the player turned on can't make {target_item_id}")
        self.target_item_id = target_item_id
        self.missing = missing

    def result(
        self, context: OrchestratorContext, accumulator: ArtifactAccumulator
    ) -> dict[str, Any]:
        """The tool result saying so -- and the recipes noted on the answer, for the page."""
        recipes = {recipe.recipe_id: recipe for recipe in context.recipes}
        for entry in self.missing:
            accumulator.missing_recipes.extend(entry.any_of)
        return {
            "error": f"no plan: the recipes the player turned on can't make "
            f"{self.target_item_id}, and the ones they turned off could",
            "missing_recipes": [
                {
                    "item_id": entry.item_id,
                    "any_of": [_recipe_option(recipes[r], context) for r in entry.any_of],
                }
                for entry in self.missing
            ],
        }


def _recipe_option(recipe: Recipe, context: OrchestratorContext) -> dict[str, Any]:
    """One recipe the player could turn on: whether they have it, and what unlocks it if not."""
    unlocked = recipe_is_unlocked(recipe, context.unlocked_technology_ids)
    option: dict[str, Any] = {"recipe_id": recipe.recipe_id, "unlocked": unlocked}
    if unlocked is False:
        technology = easiest_unlock(recipe.unlockable_by, context.technologies)
        if technology is not None:
            option.update(
                unlock_with=technology.technology_id, tier=technology.tier, kind=technology.kind
            )
    return option


def _plan(
    context: OrchestratorContext,
    target_item_id: str,
    target_rate: float,
    recipe_choices: dict[str, str] | None,
    *,
    available_supply: Mapping[str, float] | None = None,
    also_allowed: tuple[Recipe, ...] = (),
) -> ProductionGraph:
    """`plan_player_targets` for one target."""
    return plan_player_targets(
        context,
        {target_item_id: target_rate},
        recipe_choices,
        available_supply=available_supply,
        also_allowed=also_allowed,
    )


def plan_player_targets(
    context: OrchestratorContext,
    targets: Mapping[str, float],
    recipe_choices: dict[str, str] | None,
    *,
    available_supply: Mapping[str, float] | None = None,
    also_allowed: tuple[Recipe, ...] = (),
) -> ProductionGraph:
    """A plan making each of `targets` at its rate, from the recipes the player may use
    (`_usable_recipes`, plus `also_allowed`).

    With the player's own pick of recipes that's all there is: when it can't make a target but
    every recipe could, `PlanningBlocked` says which recipes it would take. By default -- the
    recipes the player has unlocked -- a plan those can't make is made from every recipe instead,
    and `needs_unlocking` says what's missing. Raises what `plan_targets` raises."""
    raw_item_ids = raw_resource_ids(context.items)
    usable = _usable_recipes(context)
    usable += tuple(recipe for recipe in also_allowed if recipe not in usable)

    def plan(recipes: tuple[Recipe, ...]) -> ProductionGraph:
        return plan_targets(
            targets,
            recipes,
            recipe_choices,
            raw_item_ids=raw_item_ids,
            available_supply=available_supply,
            game_recipes=context.recipes,
        )

    if context.enabled_recipe_ids is not None:
        _refuse_turned_off(recipe_choices, usable, context)
        try:
            return plan(usable)
        except ValueError:
            for target_item_id, target_rate in targets.items():
                missing = find_missing_recipes(
                    target_item_id,
                    target_rate,
                    usable,
                    context.recipes,
                    recipe_choices,
                    raw_item_ids=raw_item_ids,
                    available_supply=available_supply,
                )
                if missing:
                    raise PlanningBlocked(target_item_id, missing) from None
            raise
    if len(usable) < len(context.recipes):
        try:
            return plan(usable)
        except ValueError:
            pass  # not with what's unlocked: plan with everything, and say what's missing
    return plan(context.recipes)


def _usable_recipes(context: OrchestratorContext) -> tuple[Recipe, ...]:
    """The recipes plans may use: the player's pick, else every one they've unlocked -- every one
    there is while what's unlocked is unknown."""
    if context.enabled_recipe_ids is not None:
        return tuple(r for r in context.recipes if r.recipe_id in context.enabled_recipe_ids)
    unlocked = context.unlocked_technology_ids
    if unlocked is None:
        return context.recipes
    return tuple(r for r in context.recipes if recipe_is_unlocked(r, unlocked))


def _is_usable(recipe: Recipe, context: OrchestratorContext) -> bool:
    if context.enabled_recipe_ids is not None:
        return recipe.recipe_id in context.enabled_recipe_ids
    return recipe_is_unlocked(recipe, context.unlocked_technology_ids) is not False


def _refuse_turned_off(
    recipe_choices: dict[str, str] | None,
    usable: tuple[Recipe, ...],
    context: OrchestratorContext,
) -> None:
    """A recipe the model asks for by name that the player turned off is refused, not planned."""
    allowed = {recipe.recipe_id for recipe in usable}
    known = {recipe.recipe_id for recipe in context.recipes}
    for recipe_id in (recipe_choices or {}).values():
        if recipe_id in known and recipe_id not in allowed:
            raise ToolError(
                f"{recipe_id} is turned off in the player's recipe list -- plan without it, or "
                "tell the player to turn it on in the Recipes panel",
                turned_off=recipe_id,
            )


def needs_unlocking(graph: ProductionGraph, context: OrchestratorContext) -> dict[str, Any]:
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


def target_rate_arg(args: dict[str, Any]) -> float:
    rate = float(args["target_rate_per_minute"])
    if not rate > 0:
        raise ToolError(f"target_rate_per_minute must be a positive rate, got {rate:g}")
    return rate


def recipe_choices_arg(args: dict[str, Any], context: OrchestratorContext) -> dict[str, str] | None:
    raw = args.get("recipe_choices")
    if not isinstance(raw, dict) or not raw:
        return None
    return {resolve_item_id(item, context): str(recipe_id) for item, recipe_id in raw.items()}


_RATE_EPSILON = 1e-6
