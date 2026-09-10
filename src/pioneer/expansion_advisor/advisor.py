"""Diffs an existing production state against a from-scratch target plan and returns the minimal
`ChangeSet` to get from one to the other — extend factories that just need more machines, add
brand-new stages only where nothing exists yet.

**Matching is by `recipe_id`.** An existing node and a target node running the same recipe are
"the same factory": the existing one gets extended by the shortfall rather than a second parallel
factory being proposed. Producing the same *item* via a different recipe (an alternate) does not
match — feed Stage 7 the right `recipe_choices` if you want an alternate-recipe factory extended.

**"Prefer extension when numerically sufficient"** (architecture.md §5): if existing capacity for
a recipe already meets or exceeds what the target needs, nothing is emitted for it at all. If it
falls short, a single `EXTEND` covers the deficit, anchored to the first existing node running
that recipe (existing capacity is summed across all nodes running it, but the change points at
one). Only when there is no existing node for a recipe is an `ADD` emitted.

`resulting_graph` is the merged end state: carried-over nodes (normalized to `is_existing=True`,
extended ones with their bumped `machine_count`) plus the newly added nodes (`is_existing=False`),
so Stage 13 can highlight new vs. existing straight off the flag. Its `flows` are taken from the
target plan as the intended final routing — reconciling flow endpoints across the two graphs'
node-id namespaces is left to whoever renders it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace

from pioneer.contracts import ChangeAction, ChangeItem, ChangeSet, ProductionGraph, ProductionNode


def advise_expansion(existing: ProductionGraph, target: ProductionGraph) -> ChangeSet:
    existing_by_recipe: dict[str, list[ProductionNode]] = defaultdict(list)
    for node in existing.nodes:
        existing_by_recipe[node.recipe_id].append(node)
    capacity = {
        recipe_id: sum(n.machine_count for n in nodes)
        for recipe_id, nodes in existing_by_recipe.items()
    }

    changes: list[ChangeItem] = []
    result_nodes: dict[str, ProductionNode] = {
        node.node_id: replace(node, is_existing=True) for node in existing.nodes
    }

    for target_node in target.nodes:
        recipe_id = target_node.recipe_id
        deficit = target_node.machine_count - capacity.get(recipe_id, 0.0)
        if deficit <= 0:
            continue  # existing capacity already covers this stage

        anchors = existing_by_recipe.get(recipe_id)
        if anchors:
            anchor_id = anchors[0].node_id
            changes.append(
                ChangeItem(
                    action=ChangeAction.EXTEND,
                    recipe_id=recipe_id,
                    additional_machine_count=deficit,
                    target_node_id=anchor_id,
                )
            )
            bumped = result_nodes[anchor_id]
            result_nodes[anchor_id] = replace(
                bumped, machine_count=bumped.machine_count + deficit
            )
        else:
            changes.append(
                ChangeItem(
                    action=ChangeAction.ADD,
                    recipe_id=recipe_id,
                    additional_machine_count=deficit,
                    target_node_id=None,
                )
            )
            new_id = _unique_id(target_node.node_id, result_nodes.keys())
            result_nodes[new_id] = replace(target_node, node_id=new_id, is_existing=False)

    resulting_graph = ProductionGraph(nodes=tuple(result_nodes.values()), flows=target.flows)
    return ChangeSet(changes=tuple(changes), resulting_graph=resulting_graph)


def _unique_id(preferred: str, taken: Iterable[str]) -> str:
    taken_set = set(taken)
    if preferred not in taken_set:
        return preferred
    suffix = 2
    while f"{preferred}_{suffix}" in taken_set:
        suffix += 1
    return f"{preferred}_{suffix}"
