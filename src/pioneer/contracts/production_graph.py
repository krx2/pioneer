"""The production graph shape: the central contract most modules read or write.

Nodes are machines running a recipe; flows are material moving between nodes, or in and out of the
graph at its raw-input and final-output edges.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductionNode:
    node_id: str
    recipe_id: str
    building_id: str
    machine_count: float
    is_existing: bool = False
    """True when this node comes from a real save file rather than a freshly-planned addition."""
    existing_machine_count: float = 0.0
    """How many of `machine_count` already stand; the rest are to be built. All of them for a node
    straight from a save; some for an existing node an expansion extends; none for a new node."""
    production_boost: float = 1.0
    """How much Somersloops multiply the node's output, averaged over its machines by clock speed
    (see `PlacementRecord.production_boost`). Its inputs don't scale with it."""


@dataclass(frozen=True)
class MaterialFlow:
    item_id: str
    amount_per_minute: float
    source_node_id: str | None = None
    """`None` means the material enters the graph from an external/raw source."""
    target_node_id: str | None = None
    """`None` means the material leaves the graph as a final output."""


@dataclass(frozen=True)
class ProductionGraph:
    nodes: tuple[ProductionNode, ...]
    flows: tuple[MaterialFlow, ...]
