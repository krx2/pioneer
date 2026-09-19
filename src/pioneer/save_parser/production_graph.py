"""Folds recipe-carrying placements into a `ProductionGraph` describing what the player has built.

One node per distinct recipe, its `machine_count` being how much of that recipe the player runs —
which is exactly what the Expansion Advisor (Stage 8) matches on when deciding whether to extend
an existing factory or add a new stage, and what the Verifier (Stage 4) needs to compute balance
and power draw over the existing factory.

**`flows` is empty, deliberately.** A flow is an amount of an item a minute, and how much of what
a machine makes goes where is the recipes' arithmetic — the Knowledge Base's data, which this
module must not import. Which machines the belts and pipes join *is* read, though, as the save
state's `links` (see connections.py), and `verifier.implied_flows` shares the recipes' rates out
along them. The Stage 8 consumer needs neither (`advise_expansion` takes its flows from the plan
of additions).

**Machines are clock-scaled.** A node's `machine_count` is the sum of its buildings' clock speeds
(`PlacementRecord.clock_speed`) — effective machines at 100% — because that, not the number of
buildings, is what its input and output rates scale with: three constructors underclocked to 50%
make what 1.5 would. So a count can be fractional. Paused buildings run nothing, so they don't
count, and Somersloops multiply only the output: the node's `production_boost` is its buildings'
boost averaged by clock speed, which makes `machine_count * production_boost` what it outputs.
"""

from __future__ import annotations

from collections.abc import Sequence

from pioneer.contracts import PlacementRecord, ProductionGraph, ProductionNode


def to_production_graph(placements: Sequence[PlacementRecord]) -> ProductionGraph:
    """Every placement carrying a `recipe_id`, grouped into one node per recipe. Placements without
    one (belts, storage, unconfigured machines) are skipped — they aren't production stages.

    `is_existing=True` on every node: all of this came out of a save file, so Graph presentation
    (Stage 13) renders it as already-built rather than newly-proposed.
    """
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
