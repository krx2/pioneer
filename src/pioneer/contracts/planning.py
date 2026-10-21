"""Expansion change-set shape.

Produced by the Expansion Advisor (Stage 8); consumed by Graph presentation (Stage 13) via its
`resulting_graph` field. Its `ChangeItem`s also say what a revision changes in the player's plan
(see `contracts.plan`) -- which is where stages come to be removed or switched to another recipe.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pioneer.contracts.production_graph import ProductionGraph


class ChangeAction(StrEnum):
    EXTEND = "extend"
    ADD = "add"
    REMOVE = "remove"
    """Fewer machines of a recipe, or none: `additional_machine_count` is negative."""
    RECONFIGURE = "reconfigure"
    """Machines switched to another recipe in the same building: from `from_recipe_id` to
    `recipe_id`, `additional_machine_count` of them."""


@dataclass(frozen=True)
class ChangeItem:
    action: ChangeAction
    recipe_id: str
    additional_machine_count: float
    target_node_id: str | None = None
    """The existing `ProductionNode.node_id` being extended; `None` when `action` is `ADD`."""
    from_recipe_id: str | None = None
    """The recipe a `RECONFIGURE` switches machines from; `None` for every other action."""
    already_built: float | None = None
    """For a `REMOVE` or `RECONFIGURE` of the player's plan: how many of these machines they have
    built already -- to be taken down or switched over in the game; the rest just won't be built.
    `None` for other actions, and when it can't be told."""
    building_id: str | None = None
    """The building the change is about, when the one making it says: a change to the player's
    plan does, so its record reads on its own."""


@dataclass(frozen=True)
class ChangeSet:
    changes: tuple[ChangeItem, ...]
    resulting_graph: ProductionGraph
