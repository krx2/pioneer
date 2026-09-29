"""Compact summaries of a production graph for the model: each stage's power, its
transport needs, and per-item totals of its flows."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pioneer.contracts import (
    MaterialFlow,
    ProductionGraph,
    ProductionNode,
)
from pioneer.orchestrator.base import OrchestratorContext
from pioneer.verifier import (
    balance,
    power_balance,
    transport_needs,
)


def _per_item(flows: Iterable[MaterialFlow]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for flow in flows:
        totals[flow.item_id] = totals.get(flow.item_id, 0.0) + flow.amount_per_minute
    return totals


def _graph_summary(graph: ProductionGraph, context: OrchestratorContext) -> dict[str, Any]:
    """Each stage of a plan -- recipe, the building it runs in, how many, and their draw -- with
    the plan's net item balance and power."""
    summary: dict[str, Any] = {
        "stages": [
            {
                "recipe_id": node.recipe_id,
                "building_id": node.building_id,
                "machines": node.machine_count,
                **_stage_power(node.building_id, node.machine_count, context),
            }
            for node in graph.nodes
        ],
    }
    if context.recipes:
        summary["net_item_balance"] = balance(graph, context.recipes)
    if context.buildings:
        summary["net_power_draw_mw"] = power_balance(graph, context.buildings)
    if context.transport_tiers:
        summary["transport"] = _transport(graph, context)
    return summary


def _transport(graph: ProductionGraph, context: OrchestratorContext) -> list[dict[str, Any]]:
    """The belt or pipe each flow in `graph` needs, between the recipes it links ("outside" and
    "output" at the graph's edges)."""
    recipe_of = {node.node_id: node.recipe_id for node in graph.nodes}
    return [
        {
            "item_id": need.flow.item_id,
            "per_minute": need.flow.amount_per_minute,
            "from": recipe_of.get(need.flow.source_node_id or "", "outside"),
            "to": recipe_of.get(need.flow.target_node_id or "", "output"),
            "tier": need.tier.building_id if need.tier else None,
            "lines": need.lines,
        }
        for need in transport_needs(graph.flows, context.items, context.transport_tiers)
    ]


def _stage_power(
    building_id: str, machines: float, context: OrchestratorContext
) -> dict[str, float]:
    """`{"power_mw": ...}` for `machines` of `building_id` -- empty when the knowledge base doesn't
    list the building: its draw is extra information, not worth failing a tool over."""
    if not any(building.building_id == building_id for building in context.buildings):
        return {}
    stage = ProductionNode(
        node_id=building_id, recipe_id="", building_id=building_id, machine_count=machines
    )
    return {"power_mw": power_balance(ProductionGraph(nodes=(stage,), flows=()), context.buildings)}
