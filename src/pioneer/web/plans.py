"""The player's plan as a conversation keeps it on disk: `contracts.Plan` to JSON and back, and the
few words the page shows of it.

Mappings go out as JSON objects, in order; what comes back in is checked field by field, and
anything that doesn't fit -- a file torn or edited by hand -- is a `ValueError`, never a plan
half made up. The fields that came with tracking how far a plan is built (`baseline_buildings`,
a change's `already_built`) may be missing from a plan kept before: it reads back without them,
its progress unknown -- and so may where it's built (`sites`, `reservations`), for a plan kept
before plans kept that. What the player marked built isn't part of a plan's JSON: the conversation
keeps it beside the plan (see `conversations`), and `marks_from_json` reads it back.

The page is shown a plan's revision, targets and progress (`plan_summary`) -- where it's built
too: its sites, their deposits and how many have an extractor yet, and what it reserves of the
factories' spare output -- and the revisions it went through, newest first (`history_summary`),
all in the game's names.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from pioneer.contracts import (
    BuildSite,
    ChangeAction,
    ChangeItem,
    Coordinates,
    MaterialFlow,
    NodeAssignment,
    Plan,
    PlanTarget,
    ProductionGraph,
    ProductionNode,
    Purity,
    SitePlan,
    SiteStage,
    SiteTransfer,
    StageProgress,
    SupplyReservation,
)


def plan_to_json(plan: Plan) -> dict[str, Any]:
    return {
        "targets": [asdict(target) for target in plan.targets],
        "graph": {
            "nodes": [asdict(node) for node in plan.graph.nodes],
            "flows": [asdict(flow) for flow in plan.graph.flows],
        },
        "recipe_choices": dict(plan.recipe_choices),
        "extends_factory": plan.extends_factory,
        "available_supply": dict(plan.available_supply),
        "baseline_machines": dict(plan.baseline_machines),
        "baseline_buildings": dict(plan.baseline_buildings),
        "revision": plan.revision,
        "changes": [{**asdict(change), "action": change.action.value} for change in plan.changes],
        "sites": _sites_to_json(plan.sites) if plan.sites is not None else None,
        "reservations": [asdict(reservation) for reservation in plan.reservations],
    }


def plan_from_json(data: Any) -> Plan:
    """The plan `plan_to_json` made; `ValueError` for anything else."""
    try:
        graph = data["graph"]
        return Plan(
            targets=tuple(
                PlanTarget(str(t["item_id"]), float(t["rate_per_minute"])) for t in data["targets"]
            ),
            graph=ProductionGraph(
                nodes=tuple(_node(node) for node in graph["nodes"]),
                flows=tuple(_flow(flow) for flow in graph["flows"]),
            ),
            recipe_choices=tuple(
                (str(item), str(recipe)) for item, recipe in _mapping(data["recipe_choices"])
            ),
            extends_factory=bool(data["extends_factory"]),
            available_supply=tuple(
                (str(item), float(rate)) for item, rate in _mapping(data["available_supply"])
            ),
            baseline_machines=tuple(
                (str(recipe), float(count)) for recipe, count in _mapping(data["baseline_machines"])
            ),
            baseline_buildings=tuple(
                (str(recipe), int(count))
                for recipe, count in _mapping(data.get("baseline_buildings", {}))
            ),
            revision=int(data["revision"]),
            changes=tuple(_change(change) for change in data["changes"]),
            sites=_sites(data["sites"]) if data.get("sites") is not None else None,
            reservations=tuple(_reservation(r) for r in data.get("reservations") or ()),
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"not a stored plan: {error!r}") from None


def marks_from_json(data: Any) -> tuple[tuple[str, float], ...]:
    """What the player marked built, recipe id -> machines, as `Plan.marked_built` takes it --
    anything that isn't a count left out."""
    if not isinstance(data, dict):
        return ()
    return tuple(
        (str(recipe_id), float(count))
        for recipe_id, count in data.items()
        if isinstance(count, int | float) and not isinstance(count, bool) and count > 0
    )


def plan_summary(
    plan: Plan,
    names: Mapping[str, str],
    progress: Sequence[StageProgress] | None = None,
    *,
    as_of: float | None = None,
    icons: Mapping[str, str] | None = None,
    worked_nodes: Collection[str] | None = None,
) -> dict[str, Any]:
    """What the page shows of a plan: its revision and what it makes, in the game's names, and
    where it's built -- each site with its deposits, of which as many as `worked_nodes` (nodes an
    extractor stands on in the save; `None`: unknown) have an extractor yet, and what the plan
    reserves of the factories' spare output -- and, given its `progress`, how far it's built,
    stage by stage and in all, by the save written at `as_of` (a Unix time). Totals count only the
    stages whose progress can be told: `known` says whether that's all of them."""
    summary: dict[str, Any] = {
        "revision": plan.revision,
        "targets": [
            {
                "item_id": target.item_id,
                "name": names.get(target.item_id, target.item_id),
                "rate_per_minute": target.rate_per_minute,
            }
            for target in plan.targets
        ],
    }
    where = _where(plan, names, worked_nodes)
    if where:
        summary["where"] = where
    if progress is None:
        return summary
    told = [stage for stage in progress if stage.built is not None]
    summary["progress"] = {
        "built": sum(stage.built or 0.0 for stage in told),
        "to_build": sum(stage.to_build for stage in told),
        "known": "all" if len(told) == len(progress) else "some" if told else "none",
        "as_of": as_of,
        "stages": [
            {
                "recipe_id": stage.recipe_id,
                "recipe": names.get(stage.recipe_id, stage.recipe_id),
                "building": names.get(stage.building_id, stage.building_id),
                "icon": (icons or {}).get(stage.recipe_id),
                "to_build": stage.to_build,
                "built": stage.built,
                "in_save": stage.in_save,
                "marked": stage.marked,
            }
            for stage in progress
        ],
    }
    return summary


def _where(
    plan: Plan, names: Mapping[str, str], worked_nodes: Collection[str] | None
) -> dict[str, Any] | None:
    """Where `plan` is built, as the page shows it; `None` when it keeps nothing of it."""
    if (plan.sites is None or not plan.sites.sites) and not plan.reservations:
        return None
    sites = []
    for site in plan.sites.sites if plan.sites is not None else ():
        deposits = [d for d in plan.sites.deposits if d.site_id == site.site_id]
        sites.append(
            {
                "site_id": site.site_id,
                "deposits": len(deposits),
                "with_extractor": (
                    sum(d.resource_node_id in worked_nodes for d in deposits)
                    if worked_nodes is not None
                    else None
                ),
                "mines": sorted({names.get(d.item_id, d.item_id) for d in deposits}),
                "builds": [names.get(stage.recipe_id, stage.recipe_id) for stage in site.stages],
            }
        )
    return {
        "sites": sites,
        "reserved": [
            {
                "site_id": reservation.site_id,
                "item": names.get(reservation.item_id, reservation.item_id),
                "rate_per_minute": reservation.rate_per_minute,
            }
            for reservation in plan.reservations
        ],
    }


def history_summary(entries: Any, names: Mapping[str, str]) -> list[dict[str, Any]]:
    """The revisions a conversation's plan went through, newest first, as the page shows them:
    when each was accepted, the question that asked for it, and what it changed. An entry that
    can't be read is left out."""

    def named(class_id: str | None) -> str | None:
        return names.get(class_id, class_id) if class_id is not None else None

    shown = []
    for entry in entries if isinstance(entries, list) else ():
        try:
            changes = [_change(change) for change in entry.get("changes", [])]
            shown.append(
                {
                    "revision": int(entry["revision"]),
                    "accepted_at": _optional(entry.get("accepted_at")),
                    "question": _optional(entry.get("question")),
                    "response_id": _optional(entry.get("response_id")),
                    "changes": [
                        {
                            "action": change.action.value,
                            "recipe": named(change.recipe_id),
                            "from_recipe": named(change.from_recipe_id),
                            "building": named(change.building_id),
                            "machines": change.additional_machine_count,
                            "already_built": change.already_built,
                        }
                        for change in changes
                    ],
                }
            )
        except (KeyError, TypeError, AttributeError, ValueError):
            continue
    return sorted(shown, key=lambda entry: entry["revision"], reverse=True)


def _mapping(value: Any) -> list[tuple[Any, Any]]:
    if not isinstance(value, dict):
        raise TypeError(f"expected an object, got {type(value).__name__}")
    return list(value.items())


def _optional(value: Any) -> str | None:
    return None if value is None else str(value)


def _node(data: Any) -> ProductionNode:
    return ProductionNode(
        node_id=str(data["node_id"]),
        recipe_id=str(data["recipe_id"]),
        building_id=str(data["building_id"]),
        machine_count=float(data["machine_count"]),
        is_existing=bool(data["is_existing"]),
        existing_machine_count=float(data["existing_machine_count"]),
        production_boost=float(data["production_boost"]),
    )


def _flow(data: Any) -> MaterialFlow:
    return MaterialFlow(
        item_id=str(data["item_id"]),
        amount_per_minute=float(data["amount_per_minute"]),
        source_node_id=_optional(data["source_node_id"]),
        target_node_id=_optional(data["target_node_id"]),
    )


def _change(data: Any) -> ChangeItem:
    return ChangeItem(
        action=ChangeAction(data["action"]),
        recipe_id=str(data["recipe_id"]),
        additional_machine_count=float(data["additional_machine_count"]),
        target_node_id=_optional(data["target_node_id"]),
        from_recipe_id=_optional(data["from_recipe_id"]),
        already_built=(
            float(data["already_built"]) if data.get("already_built") is not None else None
        ),
        building_id=_optional(data.get("building_id")),
    )


def _sites_to_json(sites: SitePlan) -> dict[str, Any]:
    return {
        "sites": [asdict(site) for site in sites.sites],
        "deposits": [
            {**asdict(deposit), "purity": deposit.purity.value} for deposit in sites.deposits
        ],
        "transfers": [asdict(transfer) for transfer in sites.transfers],
        "shortfall": dict(sites.shortfall),
    }


def _sites(data: Any) -> SitePlan:
    return SitePlan(
        sites=tuple(
            BuildSite(
                site_id=str(site["site_id"]),
                position=_position(site["position"]),
                stages=tuple(
                    SiteStage(
                        recipe_id=str(stage["recipe_id"]),
                        building_id=str(stage["building_id"]),
                        machine_count=float(stage["machine_count"]),
                        share=float(stage["share"]),
                    )
                    for stage in site["stages"]
                ),
            )
            for site in data["sites"]
        ),
        deposits=tuple(
            NodeAssignment(
                resource_node_id=str(deposit["resource_node_id"]),
                item_id=str(deposit["item_id"]),
                purity=Purity(deposit["purity"]),
                position=_position(deposit["position"]),
                extractor_id=str(deposit["extractor_id"]),
                clock_speed=float(deposit["clock_speed"]),
                power_shards=int(deposit["power_shards"]),
                rate_per_minute=float(deposit["rate_per_minute"]),
                site_id=str(deposit["site_id"]),
            )
            for deposit in data["deposits"]
        ),
        transfers=tuple(
            SiteTransfer(
                item_id=str(transfer["item_id"]),
                from_site_id=str(transfer["from_site_id"]),
                to_site_id=str(transfer["to_site_id"]),
                rate_per_minute=float(transfer["rate_per_minute"]),
            )
            for transfer in data["transfers"]
        ),
        shortfall=tuple((str(item), float(rate)) for item, rate in _mapping(data["shortfall"])),
    )


def _reservation(data: Any) -> SupplyReservation:
    return SupplyReservation(
        site_id=str(data["site_id"]),
        position=_position(data["position"]),
        item_id=str(data["item_id"]),
        rate_per_minute=float(data["rate_per_minute"]),
    )


def _position(data: Any) -> Coordinates:
    return Coordinates(x=float(data["x"]), y=float(data["y"]), z=float(data.get("z", 0.0)))
