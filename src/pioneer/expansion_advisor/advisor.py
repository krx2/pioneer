"""Turns a plan of *additional* machines into the `ChangeSet` an existing factory needs: extend the
factories that already run a recipe, add new stages only where none does.

`additions` must be a delta plan: planned with the existing factory's spare output as
`available_supply`, so it holds only machines still to be built. Comparing a from-scratch plan
with the factory's gross machine counts would count machines already busy as free.

Matching is by `recipe_id`: an addition running a recipe an existing node runs `EXTEND`s that node
(the first, if several do); anything else is an `ADD`. An alternate recipe for the same item does
not match. `resulting_graph` holds the extended and added nodes, with the plan's flows rewired onto
them; untouched existing nodes are left out.
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
