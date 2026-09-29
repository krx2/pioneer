"""Tool handlers over what the player has already built: drawing it, its item balance,
its power grid, and diagnosing its problems and wiring."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from pioneer.anomaly_detector import (
    detect_anomalies,
    detect_belt_overloads,
    detect_wiring_problems,
)
from pioneer.contracts import (
    AnomalyKind,
    AnomalyRecord,
    AnomalySeverity,
    FactorySite,
    PlacementRecord,
    ProductionNode,
    Recipe,
)
from pioneer.orchestrator.base import (
    _CM_PER_M,
    OrchestratorContext,
    _ArtifactAccumulator,
    _ToolError,
)
from pioneer.orchestrator.existing_factory import (
    _add_rates,
    _chain_graph,
    _consuming_nodes,
    _existing_item_balance,
    _existing_power,
    _generator_consumption,
    _inputs_from_outside,
    _item_balance,
    _node_links,
    _site_graph,
    _site_summary,
)
from pioneer.orchestrator.items import _resolve_item_id, raw_resource_ids
from pioneer.orchestrator.summaries import _per_item, _stage_power
from pioneer.verifier import (
    allocate_supply,
    belt_loads,
    consumption,
    distance,
    extraction_rates,
    generator_byproducts,
    generator_fuel_demand,
    generator_supplemental_demand,
    implied_flows,
    placed_generation_by_building,
    placed_power_consumption_by_building,
)


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
        **_site_net_rates(site, context),
    }


_MAX_SITE_RATES = 4
"""How many of a site's biggest net outputs and inputs a factory listing gives: enough to say what
a factory is for, few enough that a listing of every factory fits a local model's context."""


def _site_net_rates(site: FactorySite, context: OrchestratorContext) -> dict[str, Any]:
    """What one factory ships out and takes in a minute, net -- its biggest few of each -- so a
    listing of the player's factories carries real rates, not just recipe names. A site is only its
    recipe-running machines (see `find_factory_sites`), so the ore and water it uses always show as
    taken in, wherever it's mined. Empty when the site's recipes aren't all known."""
    known = {recipe.recipe_id for recipe in context.recipes}
    graph = _site_graph(site)
    if not graph.nodes or any(node.recipe_id not in known for node in graph.nodes):
        return {}
    net = _item_balance(graph, site.placements, context)
    out = sorted(((i, r) for i, r in net.items() if r >= _BALANCE_TOLERANCE), key=lambda e: -e[1])
    into = sorted(
        ((i, -r) for i, r in net.items() if r <= -_BALANCE_TOLERANCE), key=lambda e: -e[1]
    )
    return {
        "net_out_per_minute": {i: round(r, 2) for i, r in out[:_MAX_SITE_RATES]},
        "net_in_per_minute": {i: round(r, 2) for i, r in into[:_MAX_SITE_RATES]},
    }


_BALANCE_TOLERANCE = 0.01
"""Rates below this (per minute) are float noise from summing clock speeds, not production."""


def _handle_factory_item_balance(
    args: dict[str, Any], context: OrchestratorContext
) -> dict[str, Any]:
    """What the save's factory produces and uses of each item, and the net of the two -- the same
    balance expansion and diagnosis start from, handed to the model as is. Produced counts what
    extractors mine and generators leave behind; used counts the generators' fuel and water."""
    if context.existing_graph is None:
        return {"error": "no save loaded -- nothing is known about what the player has built"}
    if not context.recipes:
        return {"error": "no knowledge base loaded -- the stages' recipes are unknown"}
    try:
        net = _existing_item_balance(context)
        used = consumption(context.existing_graph, context.recipes)
    except ValueError as error:
        return {"error": f"cannot compute the balance: {error}"}
    _add_rates(used, _generator_consumption(context))

    item = args.get("item")
    wanted = {_resolve_item_id(item, context)} if item else net.keys() | used.keys()
    rows = {}
    for item_id in wanted:
        net_rate, used_rate = net.get(item_id, 0.0), used.get(item_id, 0.0)
        produced = net_rate + used_rate
        if max(produced, used_rate) < _BALANCE_TOLERANCE:
            continue
        rows[item_id] = {
            "produced": round(produced, 2),
            "used": round(used_rate, 2),
            "net": round(net_rate, 2) if abs(net_rate) >= _BALANCE_TOLERANCE else 0.0,
        }
    if item and not rows:
        raise _ToolError(f"the player's factory neither produces nor uses {next(iter(wanted))}")
    return {
        "per_minute": dict(sorted(rows.items(), key=lambda row: (row[1]["net"], row[0]))),
        "note": "net = produced - used; produced includes mining, used includes generator fuel",
    }


def _handle_factory_power(context: OrchestratorContext) -> dict[str, Any]:
    """The save's power grid as it stands: draw against capacity, and which buildings account for
    each -- the numbers diagnosis judges a blackout by, without the rest of a diagnosis."""
    if context.existing_graph is None:
        return {"error": "no save loaded -- nothing is known about what the player has built"}
    try:
        draw, capacity = _existing_power(context)
    except ValueError as error:
        return {"error": f"cannot compute power: {error}"}
    result: dict[str, Any] = {"draw_mw": round(draw, 2), "capacity_mw": None, "spare_mw": None}
    if capacity is not None:
        result.update(capacity_mw=round(capacity, 2), spare_mw=round(capacity - draw, 2))
    if context.available_power_mw is not None:
        result["capacity_from"] = "configured, not the save's generators"
    placements, buildings = context.existing_placements, context.buildings
    if not (placements and buildings):
        result["note"] = "no building placements -- draw is the recipes' machines alone"
        return result
    result["draw_by_building"] = _by_building(
        placed_power_consumption_by_building(placements, buildings)
    )
    result["generators"] = _by_building(placed_generation_by_building(placements, buildings))
    fuel = generator_fuel_demand(placements, buildings, context.items)
    result["fuel_burned_per_minute"] = {item: round(rate, 2) for item, rate in fuel.items()}
    return result


def _by_building(rates: Mapping[str, tuple[int, float]]) -> dict[str, dict[str, float]]:
    return {
        building_id: {"count": count, "mw": round(mw, 2)}
        for building_id, (count, mw) in sorted(rates.items(), key=lambda entry: -entry[1][1])
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
