"""Classifies balance, power, flow and wiring problems into `AnomalyRecord`s.

It classifies, it doesn't compute: the numbers (`item_balance`, `net_power_draw_mw`, belt loads)
come from the Verifier, passed in by the caller.

- RESOURCE_DEFICIT: consumed faster than produced, and not a raw input (named in `raw_item_ids`
  or fed by a boundary inflow).
- RESOURCE_SURPLUS: overproduced, consumed by nothing and not an intended output. Always LOW.
- POWER_BLACKOUT: net draw over `available_power_mw`, when that's given.
- CONGESTION: a flow over `belt_capacity_per_minute` (default 780/min, Mk.5), or a save's belt over
  its tier's capacity (`detect_belt_overloads`).
- MACHINE_NOT_FED / OUTPUT_BLOCKED: from the save's belts and pipes (`detect_wiring_problems`).

Severity scales with how far over the line a number is: >=100% HIGH, >=50% MEDIUM, else LOW.
A save's graph has no flows, so `item_demand` and `item_consumers` stand in for them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterator, Mapping, Sequence

from pioneer.contracts import (
    AnomalyKind,
    AnomalyRecord,
    AnomalySeverity,
    MaterialFlow,
    ProductionGraph,
    Recipe,
    TransportLink,
)


def detect_anomalies(
    graph: ProductionGraph,
    item_balance: Mapping[str, float],
    net_power_draw_mw: float,
    *,
    raw_item_ids: Collection[str] = (),
    output_item_ids: Collection[str] = (),
    item_demand: Mapping[str, float] | None = None,
    item_consumers: Mapping[str, Collection[str]] | None = None,
    available_power_mw: float | None = None,
    belt_capacity_per_minute: float = 780.0,
    rate_tolerance: float = 1e-6,
) -> tuple[AnomalyRecord, ...]:
    flows = graph.flows
    expected_raw = set(raw_item_ids) | {f.item_id for f in flows if f.source_node_id is None}
    expected_output = set(output_item_ids) | {f.item_id for f in flows if f.target_node_id is None}

    demand_by_item: dict[str, float] = defaultdict(float)
    consumers_by_item: dict[str, set[str]] = defaultdict(set)
    for flow in flows:
        if flow.target_node_id is not None:
            demand_by_item[flow.item_id] += flow.amount_per_minute
            consumers_by_item[flow.item_id].add(flow.target_node_id)
    demand_by_item.update(item_demand or {})
    consumers_by_item.update({item: set(nodes) for item, nodes in (item_consumers or {}).items()})

    records: list[AnomalyRecord] = []
    records.extend(
        _deficits(item_balance, expected_raw, demand_by_item, consumers_by_item, rate_tolerance)
    )
    records.extend(_power_blackout(net_power_draw_mw, available_power_mw, rate_tolerance))
    records.extend(_congestion(flows, belt_capacity_per_minute, rate_tolerance))
    records.extend(_surpluses(item_balance, expected_output, rate_tolerance))
    return tuple(records)


def _severity(overshoot_fraction: float) -> AnomalySeverity:
    if overshoot_fraction >= 1.0:
        return AnomalySeverity.HIGH
    if overshoot_fraction >= 0.5:
        return AnomalySeverity.MEDIUM
    return AnomalySeverity.LOW


def _deficits(
    item_balance: Mapping[str, float],
    expected_raw: set[str],
    demand_by_item: Mapping[str, float],
    consumers_by_item: Mapping[str, set[str]],
    rate_tolerance: float,
) -> Iterator[AnomalyRecord]:
    for item_id, balance in sorted(item_balance.items()):
        if balance >= -rate_tolerance or item_id in expected_raw:
            continue
        deficit = -balance
        demand = demand_by_item.get(item_id, 0.0)
        severity = _severity(deficit / demand) if demand > 0 else AnomalySeverity.MEDIUM
        consumers = consumers_by_item.get(item_id, set())
        node_id = next(iter(consumers)) if len(consumers) == 1 else None
        yield AnomalyRecord(
            kind=AnomalyKind.RESOURCE_DEFICIT,
            severity=severity,
            description=f"{item_id} short by {deficit:.3g}/min (produced less than consumed)",
            item_id=item_id,
            node_id=node_id,
        )


def _power_blackout(
    net_power_draw_mw: float, available_power_mw: float | None, rate_tolerance: float
) -> Iterator[AnomalyRecord]:
    if available_power_mw is None:
        return
    overshoot = net_power_draw_mw - available_power_mw
    if overshoot <= rate_tolerance:
        return
    if available_power_mw > 0:
        severity = _severity(overshoot / available_power_mw)
    else:
        severity = AnomalySeverity.HIGH
    yield AnomalyRecord(
        kind=AnomalyKind.POWER_BLACKOUT,
        severity=severity,
        description=(
            f"power demand {net_power_draw_mw:.3g} MW exceeds available "
            f"{available_power_mw:.3g} MW by {overshoot:.3g} MW"
        ),
    )


def _congestion(
    flows: tuple[MaterialFlow, ...], belt_capacity_per_minute: float, rate_tolerance: float
) -> Iterator[AnomalyRecord]:
    for flow in flows:
        over = flow.amount_per_minute - belt_capacity_per_minute
        if over <= rate_tolerance:
            continue
        yield AnomalyRecord(
            kind=AnomalyKind.CONGESTION,
            severity=_severity(over / belt_capacity_per_minute),
            description=(
                f"{flow.item_id} flow of {flow.amount_per_minute:.3g}/min exceeds belt "
                f"capacity {belt_capacity_per_minute:.3g}/min"
            ),
            item_id=flow.item_id,
            node_id=flow.target_node_id,
        )


def _surpluses(
    item_balance: Mapping[str, float], expected_output: set[str], rate_tolerance: float
) -> Iterator[AnomalyRecord]:
    for item_id, balance in sorted(item_balance.items()):
        if balance <= rate_tolerance or item_id in expected_output:
            continue
        yield AnomalyRecord(
            kind=AnomalyKind.RESOURCE_SURPLUS,
            severity=AnomalySeverity.LOW,
            description=f"{item_id} overproduced by {balance:.3g}/min (nothing downstream uses it)",
            item_id=item_id,
        )


def detect_wiring_problems(
    machines: Sequence[tuple[str, Recipe]],
    links: Sequence[TransportLink],
    *,
    supplies: Mapping[str, Collection[str] | None],
    accepts: Mapping[str, Collection[str] | None],
    fluid_item_ids: Collection[str] = (),
) -> tuple[AnomalyRecord, ...]:
    """What the save's belts and pipes leave undone, per running machine and item of its recipe
    (`machines`: object id and recipe). An ingredient no belt -- a pipe, for a fluid -- brings in
    from anything that may carry it is MACHINE_NOT_FED; a product no belt or pipe takes to anything
    that accepts it is OUTPUT_BLOCKED: the machine fills up and stops. Both are HIGH.

    `supplies` and `accepts` say which items each building at the other end of a link may put out
    and take in -- a machine its recipe's, an extractor its resource. One they leave out, or give
    as `None` (a container, a station, a sink), may carry anything: a belt from a train station can
    bring whatever the train does, so it's never the reason a machine goes short."""
    incoming: dict[str, list[TransportLink]] = defaultdict(list)
    outgoing: dict[str, list[TransportLink]] = defaultdict(list)
    for link in links:
        incoming[link.target_id].append(link)
        outgoing[link.source_id].append(link)

    def carrier(item_id: str) -> str:
        return "pipe" if item_id in fluid_item_ids else "belt"

    records = []
    for machine_id, recipe in machines:
        for ingredient in recipe.inputs:
            item_id, way = ingredient.item_id, carrier(ingredient.item_id)
            if not any(
                link.carrier == way and _may_carry(supplies, link.source_id, item_id)
                for link in incoming[machine_id]
            ):
                records.append(
                    AnomalyRecord(
                        kind=AnomalyKind.MACHINE_NOT_FED,
                        severity=AnomalySeverity.HIGH,
                        description=f"a {recipe.recipe_id} machine gets no {item_id}: none of "
                        f"its {way}s brings it",
                        item_id=item_id,
                        node_id=machine_id,
                    )
                )
        for product in recipe.outputs:
            item_id, way = product.item_id, carrier(product.item_id)
            if not any(
                link.carrier == way and _may_carry(accepts, link.target_id, item_id)
                for link in outgoing[machine_id]
            ):
                records.append(
                    AnomalyRecord(
                        kind=AnomalyKind.OUTPUT_BLOCKED,
                        severity=AnomalySeverity.HIGH,
                        description=f"a {recipe.recipe_id} machine's {item_id} has nowhere to "
                        f"go: its {way}s lead to nothing that takes it, so it fills up and stops",
                        item_id=item_id,
                        node_id=machine_id,
                    )
                )
    return tuple(records)


def detect_belt_overloads(
    loads: Mapping[str, float],
    capacities: Mapping[str, float],
    *,
    belt_ids: Mapping[str, str] | None = None,
    rate_tolerance: float = 1e-6,
) -> tuple[AnomalyRecord, ...]:
    """CONGESTION for each belt whose load (`verifier.belt_loads`) is over its capacity, by object
    id -- a belt with no capacity given is skipped. `belt_ids` names each belt's class for the
    description."""
    records = []
    for belt, load in sorted(loads.items()):
        capacity = capacities.get(belt)
        if not capacity or load - capacity <= rate_tolerance:
            continue
        name = (belt_ids or {}).get(belt, "belt")
        records.append(
            AnomalyRecord(
                kind=AnomalyKind.CONGESTION,
                severity=_severity(load / capacity - 1.0),
                description=f"a {name} has to carry {load:.3g}/min but is rated {capacity:.3g}/min",
                node_id=belt,
            )
        )
    return tuple(records)


def _may_carry(known: Mapping[str, Collection[str] | None], building: str, item_id: str) -> bool:
    items = known.get(building)
    return items is None or item_id in items
