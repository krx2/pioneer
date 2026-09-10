"""Classifies balance / power / flow problems in a `ProductionGraph` into `AnomalyRecord`s.

This module *classifies*, it doesn't *compute* — the numeric inputs (`item_balance`,
`net_power_draw_mw`) are expected to come from the Verifier (`verifier.balance` /
`verifier.power_balance`), matching architecture.md's picture of the Anomaly Detector consuming
Verifier output. It never imports `verifier` itself; the orchestrator wires them together at
Stage 16.

What it flags:

- **RESOURCE_DEFICIT** — an item consumed faster than it's produced, and not explained as a raw
  input. "Explained as raw" means: named in `raw_item_ids`, or fed by a boundary inflow in the
  graph's flows (`source_node_id is None`). Without either signal a bare negative balance is
  treated as a genuine shortage.
- **RESOURCE_SURPLUS** — an item overproduced with nothing downstream consuming it, and not the
  graph's intended output (named in `output_item_ids`, or drained by a boundary outflow,
  `target_node_id is None`). Always LOW severity: wasteful, not broken.
- **POWER_BLACKOUT** — only when `available_power_mw` is given and net draw exceeds it. With no
  budget supplied there's nothing to judge against, so nothing is flagged.
- **CONGESTION** — a single flow whose rate exceeds `belt_capacity_per_minute` (default 780/min,
  the Mk.5 belt; pass the tier the player has actually unlocked).

Severity for deficits/power/congestion scales with how far over the line the number is (>=100%
over -> HIGH, >=50% -> MEDIUM, else LOW); a deficit with no derivable demand falls back to MEDIUM.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterator, Mapping

from pioneer.contracts import (
    AnomalyKind,
    AnomalyRecord,
    AnomalySeverity,
    MaterialFlow,
    ProductionGraph,
)


def detect_anomalies(
    graph: ProductionGraph,
    item_balance: Mapping[str, float],
    net_power_draw_mw: float,
    *,
    raw_item_ids: Collection[str] = (),
    output_item_ids: Collection[str] = (),
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

    records: list[AnomalyRecord] = []
    records.extend(
        _deficits(item_balance, expected_raw, demand_by_item, consumers_by_item, rate_tolerance)
    )
    records.extend(
        _power_blackout(net_power_draw_mw, available_power_mw, rate_tolerance)
    )
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
