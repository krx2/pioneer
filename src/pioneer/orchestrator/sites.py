"""Where a plan is to be built, for the planning tools: the extractors and belts the player has to
work its deposits with, `location_advisor.plan_sites` choosing the deposits and sites, and what
of that the model is told.

**Extractors.** Each node is worked with the best extractor the player has unlocked for it — Miner
Mk.3 over Mk.2 over Mk.1 for ores, the Oil Extractor for crude oil, the Resource Well Extractor on
a well's satellite node — overclocked with power shards up to 250%, as players do, but no further
than the fastest belt or pipe they have carries out of it. Satellite nodes run at the pressurizer's
clock, which isn't overclocked here. With no save, what's unlocked is unknown and everything counts;
where nothing the player has can work a node, the plan names the extractor to unlock.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Collection, Mapping
from functools import cache
from typing import Any

from pioneer.contracts import (
    Building,
    Coordinates,
    Extractor,
    MaterialFlow,
    PlacementRecord,
    ProductionGraph,
    RankedLocation,
    SitePlan,
    TransportTier,
)
from pioneer.knowledge_base import easiest_unlock
from pioneer.location_advisor import SITE_RADIUS, ExtractorFor, plan_sites
from pioneer.orchestrator.base import _CM_PER_M, OrchestratorContext, _ArtifactAccumulator
from pioneer.orchestrator.existing_factory import reference_point
from pioneer.verifier import distance, placed_power_consumption_mw, transport_needs

_MAX_CLOCK_SPEED = 2.5
"""Three power shards' worth."""
_WATER_ID = "Desc_Water_C"


def _build_sites(
    raw_rates: Mapping[str, float],
    context: OrchestratorContext,
    accumulator: _ArtifactAccumulator,
    graph: ProductionGraph | None = None,
    reference: Coordinates | None = None,
    *,
    kept: SitePlan | None = None,
    replace_map: bool = False,
) -> tuple[dict[str, Any], SitePlan | None]:
    """`{"build_sites": ...}`, and the `SitePlan` it tells of: where to build for `raw_rates` (raw
    resource -> per minute) — with `graph`, where each of its stages goes too — measured from
    `reference`, else the player's base; a revision of `kept`, the plan's own, when given. Nodes
    other plans work are left alone. Also the answer's map, unless something else is already
    (`replace_map`: whatever is). Water needs no deposit (extractors go on any water), so it gets
    none."""
    needs = {item: rate for item, rate in raw_rates.items() if item != _WATER_ID and rate > 0}
    if not context.resource_nodes or not needs:
        return {}, None
    extractor_for = _extractor_for(context)
    if extractor_for is None:
        return {}, None
    reference = reference if reference is not None else reference_point({}, context)
    plan = plan_sites(
        needs,
        context.resource_nodes,
        context.existing_placements,
        extractor_for,
        reference,
        graph,
        kept=kept,
        reserved=context.reserved_node_ids,
    )
    if not plan.deposits and not plan.shortfall:
        return {}, None
    return _shown(plan, context, accumulator, reference, kept, replace_map=replace_map), plan


def _shown(
    plan: SitePlan,
    context: OrchestratorContext,
    accumulator: _ArtifactAccumulator,
    reference: Coordinates | None = None,
    kept: SitePlan | None = None,
    *,
    replace_map: bool = False,
) -> dict[str, Any]:
    """`{"build_sites": ...}` for `plan` as it stands — the answer's map too, unless something else
    is already (`replace_map`: whatever is)."""
    reference = reference if reference is not None else reference_point({}, context)
    if replace_map or accumulator.map_locations is None:
        accumulator.map_locations = _as_locations(plan, reference)
        accumulator.map_reference = reference
        accumulator.site_plan = plan
    return {"build_sites": _summary(plan, context, reference, kept)}


def _extractor_for(context: OrchestratorContext) -> ExtractorFor | None:
    """The extractor each node would be worked with — see the module docstring. `None` when the
    knowledge base lists no extractors at all."""
    extractors = [
        building
        for building in context.buildings
        if building.extracts_from in ("node", "well") and building.extraction_rate_per_minute > 0
    ]
    if not extractors:
        return None
    unlocked = context.unlocked_technology_ids
    fluids = frozenset(item.item_id for item in context.items if item.is_fluid)
    belt = _fastest(context.transport_tiers, unlocked, fluids=False)
    pipe = _fastest(context.transport_tiers, unlocked, fluids=True)

    @cache
    def best(item_id: str, on_well: bool) -> Extractor | None:
        able = [b for b in extractors if _works(b, item_id, on_well, fluids)]
        if not able:
            return None
        owned = [b for b in able if _have(b.unlockable_by, unlocked) is not False]
        pick = (
            max(owned, key=lambda b: (b.extraction_rate_per_minute, b.building_id))
            if owned
            else min(able, key=lambda b: (b.extraction_rate_per_minute, b.building_id))
        )
        carrier = pipe if item_id in fluids else belt
        return Extractor(
            building_id=pick.building_id,
            rate_per_minute=pick.extraction_rate_per_minute,
            max_clock_speed=_MAX_CLOCK_SPEED if pick.extracts_from == "node" else 1.0,
            output_capacity=carrier.capacity_per_minute if carrier is not None else math.inf,
            unlocked=_have(pick.unlockable_by, unlocked),
        )

    return lambda node: best(node.item_id, node.on_well)


def _works(building: Building, item_id: str, on_well: bool, fluids: Collection[str]) -> bool:
    """Whether `building` can work a node of `item_id`: a well's satellite node takes a well
    extractor, any other an extractor for nodes — limited to its one resource if it has one (the
    Oil Extractor), else to solids (miners) or, on a well, fluids."""
    if building.extracts_from != ("well" if on_well else "node"):
        return False
    if building.fixed_resource_id is not None:
        return building.fixed_resource_id == item_id
    return (item_id in fluids) == on_well


def _have(technologies: Collection[str], unlocked: Collection[str] | None) -> bool | None:
    """Whether the player has what any of `technologies` unlocks: `None` when that's unknown, and
    true for something nothing has to unlock."""
    if unlocked is None:
        return None
    return not technologies or any(technology in unlocked for technology in technologies)


def _fastest(
    tiers: Collection[TransportTier], unlocked: Collection[str] | None, *, fluids: bool
) -> TransportTier | None:
    """The fastest belt (or pipe) the player has; the slowest there is when they have none."""
    kind = [tier for tier in tiers if tier.carries_fluids == fluids]
    owned = [tier for tier in kind if _have(tier.unlockable_by, unlocked) is not False]
    if owned:
        return max(owned, key=lambda tier: tier.capacity_per_minute)
    return min(kind, key=lambda tier: tier.capacity_per_minute, default=None)


def _as_locations(plan: SitePlan, reference: Coordinates) -> tuple[RankedLocation, ...]:
    """The plan's deposits as the map's pins — what the map channel checks — most given first."""
    return tuple(
        RankedLocation(
            resource_node_id=deposit.resource_node_id,
            position=deposit.position,
            purity=deposit.purity,
            distance_to_reference=distance(deposit.position, reference),
            score=deposit.rate_per_minute,
        )
        for deposit in plan.deposits
    )


def _summary(
    plan: SitePlan,
    context: OrchestratorContext,
    reference: Coordinates,
    kept: SitePlan | None = None,
) -> dict[str, Any]:
    """What the model is told of `plan`: per site, its deposits — alike nodes counted together,
    with how many have an extractor standing on them already — and, when the plan is split, what
    the site builds; what goes between sites, on which belts; and the shards and power it all
    takes. A site `kept` from the plan as it was says so."""
    built = {p.resource_node_id for p in context.existing_placements if p.resource_node_id}
    kept_ids = {site.site_id for site in kept.sites} if kept is not None else set()
    tiers = [
        tier
        for tier in context.transport_tiers
        if _have(tier.unlockable_by, context.unlocked_technology_ids) is not False
    ]
    sites: list[dict[str, Any]] = []
    for site in plan.sites:
        alike: dict[tuple[str, str, str, float], list] = defaultdict(list)
        for deposit in plan.deposits:
            if deposit.site_id == site.site_id:
                key = (
                    deposit.item_id,
                    deposit.purity.value,
                    deposit.extractor_id,
                    round(deposit.clock_speed * 100, 1),
                )
                alike[key].append(deposit)
        entry: dict[str, Any] = {
            "site_id": site.site_id,
            "distance_from_base_m": round(distance(site.position, reference) / _CM_PER_M),
            "deposits": [
                {
                    "item_id": item_id,
                    "purity": purity,
                    "nodes": len(deposits),
                    "extractor": extractor_id,
                    "clock_percent": clock,
                    "power_shards_each": deposits[0].power_shards,
                    "per_minute": round(sum(d.rate_per_minute for d in deposits), 1),
                }
                | (
                    {"extractors_built": standing}
                    if (standing := sum(d.resource_node_id in built for d in deposits))
                    else {}
                )
                for (item_id, purity, extractor_id, clock), deposits in alike.items()
            ],
        }
        if site.site_id in kept_ids:
            entry["kept_from_plan"] = True
        if any(site.stages for site in plan.sites) and len(plan.sites) > 1:
            entry["builds"] = [
                {"recipe_id": stage.recipe_id, "machines": int(stage.machine_count)}
                | ({"share_percent": round(stage.share * 100)} if stage.share < 0.999 else {})
                for stage in site.stages
            ]
        sites.append(entry)
    summary: dict[str, Any] = {
        "layout": (
            f"one site: every deposit within {SITE_RADIUS / _CM_PER_M:.0f} m of its middle"
            if len(plan.sites) == 1
            else f"{len(plan.sites)} sites: what can't be had close together is split, each site "
            "building the parts its own deposits are for and sending them on"
        ),
        "sites": sites,
        "power_shards": sum(deposit.power_shards for deposit in plan.deposits),
        "extraction_power_mw": round(_extraction_power(plan, context), 1),
    }
    if plan.transfers:
        needs = transport_needs(
            [MaterialFlow(t.item_id, t.rate_per_minute) for t in plan.transfers],
            context.items,
            tiers,
        )
        summary["sends"] = [
            {
                "item_id": transfer.item_id,
                "from": transfer.from_site_id,
                "to": transfer.to_site_id,
                "per_minute": round(transfer.rate_per_minute, 1),
                **({"carrier": need.tier.building_id, "lines": need.lines} if need.tier else {}),
            }
            for transfer, need in zip(plan.transfers, needs, strict=True)
        ]
    if plan.shortfall:
        summary["not_covered_per_minute"] = {
            item_id: round(rate, 1) for item_id, rate in plan.shortfall
        }
    locked = _locked_extractors(plan, context)
    if locked:
        summary["extractors_to_unlock"] = locked
    return summary


def _extraction_power(plan: SitePlan, context: OrchestratorContext) -> float:
    return placed_power_consumption_mw(
        [
            PlacementRecord(d.extractor_id, d.position, clock_speed=d.clock_speed)
            for d in plan.deposits
        ],
        context.buildings,
    )


def _locked_extractors(plan: SitePlan, context: OrchestratorContext) -> list[dict[str, Any]]:
    """The extractors the plan's deposits are worked with that the player hasn't unlocked, with
    what unlocks each."""
    if context.unlocked_technology_ids is None:
        return []
    buildings = {building.building_id: building for building in context.buildings}
    locked = []
    for building_id in sorted({deposit.extractor_id for deposit in plan.deposits}):
        building = buildings.get(building_id)
        if building is None or _have(building.unlockable_by, context.unlocked_technology_ids):
            continue
        technology = easiest_unlock(building.unlockable_by, context.technologies)
        locked.append(
            {
                "building_id": building_id,
                "unlock_with": technology.technology_id if technology else None,
                "tier": technology.tier if technology else None,
            }
        )
    return locked
