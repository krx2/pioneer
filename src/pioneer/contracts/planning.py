"""Expansion change-set shape.

Produced by the Expansion Advisor (Stage 8); consumed by Graph presentation (Stage 13) via its
`resulting_graph` field.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pioneer.contracts.production_graph import ProductionGraph


class ChangeAction(StrEnum):
    EXTEND = "extend"
    ADD = "add"


@dataclass(frozen=True)
class ChangeItem:
    action: ChangeAction
    recipe_id: str
    additional_machine_count: float
    target_node_id: str | None = None
    """The existing `ProductionNode.node_id` being extended; `None` when `action` is `ADD`."""


@dataclass(frozen=True)
class ChangeSet:
    changes: tuple[ChangeItem, ...]
    resulting_graph: ProductionGraph
