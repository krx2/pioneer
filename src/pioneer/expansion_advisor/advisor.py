"""Turns a plan of *additional* machines into the `ChangeSet` an existing factory needs: extend the
factories that already run a recipe, add new stages only where none does.

**The input is a delta plan, not a from-scratch one.** `additions` is what the Production Planner
returns when run with the existing factory's spare output as `available_supply` (the Verifier's
positive balance over the existing graph): it already holds only machines that have to be built —
demand the existing surplus covers is gone from it, along with everything upstream of that
demand. That's what makes the answer right. Comparing a from-scratch plan against the existing
factory's *gross* machine counts instead counts machines already busy feeding the existing factory
as free, and tells a player who is already short of Iron Ingot that their smelters cover a new
Reinforced Iron Plate line.

**Matching is by `recipe_id`.** An addition running a recipe some existing node already runs is an
`EXTEND` of that node (the first one, if several run it): more of the same machine, where it
already stands. Anything else is an `ADD`. Producing the same *item* via a different recipe (an
alternate) does not match — feed Stage 7 the right `recipe_choices` if you want an
alternate-recipe factory extended.

`resulting_graph` is the production chain for the request after the change: every extended node
(`is_existing=True`, `machine_count` bumped by the addition, `existing_machine_count` what already
stood) and every added node (`is_existing=False`, nothing standing yet), with the plan's flows
rewired onto those node ids — so every flow endpoint is a node in the graph, or `None` for material
entering from outside (raw resources, or the existing factory's surplus). Existing nodes the change
doesn't touch are left out: they're the rest of the factory, not part of this chain.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from pioneer.contracts import (
    ChangeAction,
    ChangeItem,
    ChangeSet,
    MaterialFlow,
    ProductionGraph,
    ProductionNode,
)


def advise_expansion(existing: ProductionGraph, additions: ProductionGraph) -> ChangeSet:
    anchors: dict[str, ProductionNode] = {}
    for node in existing.nodes:
        anchors.setdefault(node.recipe_id, node)
    existing_ids = {node.node_id for node in existing.nodes}

    changes: list[ChangeItem] = []
    result_nodes: dict[str, ProductionNode] = {}
    rewired: dict[str, str] = {}  # additions' node id -> its node id in resulting_graph

    for node in additions.nodes:
        if node.machine_count <= 0:
            continue
        anchor = anchors.get(node.recipe_id)
        if anchor is not None:
            changes.append(
                ChangeItem(
                    action=ChangeAction.EXTEND,
                    recipe_id=node.recipe_id,
                    additional_machine_count=node.machine_count,
                    target_node_id=anchor.node_id,
                )
            )
            extended = result_nodes.get(
                anchor.node_id,
                replace(anchor, is_existing=True, existing_machine_count=anchor.machine_count),
            )
            machines = extended.machine_count + node.machine_count
            # New machines carry no Somersloops: the boost thins out over the larger count.
            boosted = extended.machine_count * extended.production_boost + node.machine_count
            result_nodes[anchor.node_id] = replace(
                extended, machine_count=machines, production_boost=boosted / machines
            )
            rewired[node.node_id] = anchor.node_id
        else:
            changes.append(
                ChangeItem(
                    action=ChangeAction.ADD,
                    recipe_id=node.recipe_id,
                    additional_machine_count=node.machine_count,
                    target_node_id=None,
                )
            )
            new_id = _unique_id(node.node_id, existing_ids | result_nodes.keys())
            result_nodes[new_id] = replace(
                node, node_id=new_id, is_existing=False, existing_machine_count=0.0
            )
            rewired[node.node_id] = new_id

    flows = tuple(_rewire(flow, rewired) for flow in additions.flows)
    resulting_graph = ProductionGraph(nodes=tuple(result_nodes.values()), flows=flows)
    return ChangeSet(changes=tuple(changes), resulting_graph=resulting_graph)


def _rewire(flow: MaterialFlow, rewired: dict[str, str]) -> MaterialFlow:
    source = flow.source_node_id
    target = flow.target_node_id
    return replace(
        flow,
        source_node_id=rewired.get(source, source) if source is not None else None,
        target_node_id=rewired.get(target, target) if target is not None else None,
    )


def _unique_id(preferred: str, taken: Iterable[str]) -> str:
    taken_set = set(taken)
    if preferred not in taken_set:
        return preferred
    suffix = 2
    while f"{preferred}_{suffix}" in taken_set:
        suffix += 1
    return f"{preferred}_{suffix}"
