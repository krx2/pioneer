"""Folds recipe-carrying placements into a `ProductionGraph` of what the player has built: one
node per recipe.

`flows` is empty: rates are the recipes' arithmetic, which needs Knowledge Base data;
`verifier.implied_flows` shares them out along the save's `links`. A node's `machine_count` is its
buildings' clock speeds summed — three constructors at 50% make what 1.5 would — and paused
buildings don't count. `production_boost` is the Somersloop boost averaged by clock speed, so
`machine_count * production_boost` is what the node outputs.
"""

from __future__ import annotations

from collections.abc import Sequence

from pioneer.contracts import PlacementRecord, ProductionGraph, ProductionNode


def to_production_graph(placements: Sequence[PlacementRecord]) -> ProductionGraph:
    """Every placement carrying a `recipe_id`, grouped into one node per recipe, `is_existing` on
    every node. Placements without one (belts, storage, unconfigured machines) are skipped."""
    machine_counts: dict[str, float] = {}
    boosted_counts: dict[str, float] = {}
    building_ids: dict[str, str] = {}

    for placement in placements:
        if placement.recipe_id is None or placement.is_paused:
            continue
        recipe_id = placement.recipe_id
        machine_counts[recipe_id] = machine_counts.get(recipe_id, 0.0) + placement.clock_speed
        boosted_counts[recipe_id] = (
            boosted_counts.get(recipe_id, 0.0) + placement.clock_speed * placement.production_boost
        )
        building_ids.setdefault(recipe_id, placement.building_id)

    nodes = tuple(
        ProductionNode(
            node_id=f"save_{recipe_id}",
            recipe_id=recipe_id,
            building_id=building_ids[recipe_id],
            machine_count=count,
            is_existing=True,
            existing_machine_count=count,
            production_boost=boosted_counts[recipe_id] / count if count > 0 else 1.0,
        )
        for recipe_id, count in machine_counts.items()
    )
    return ProductionGraph(nodes=nodes, flows=())
