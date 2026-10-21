"""The player's plan: the production chain one conversation works towards.

Proposed by the Orchestrator (Stage 16) in an answer that planned something, and kept with the
conversation once the player accepts it. Asked for a change, the Orchestrator revises it rather
than planning anew: each revision is planned again from the same parameters -- its targets, the
recipe each stage runs, the factory surplus it draws on -- with only what the player asked for
changed, so every stage they didn't touch stays exactly as it was.

How far the player has built it is told by the Verifier from the save (`StageProgress`): what
stands of each stage's recipe beyond what stood when it came into the plan -- or what the player
marked built by hand, where that's more.

Mappings are tuples of pairs, so a plan stays a plain frozen value like every other contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.contracts.planning import ChangeItem
from pioneer.contracts.production_graph import ProductionGraph


@dataclass(frozen=True)
class PlanTarget:
    item_id: str
    rate_per_minute: float


@dataclass(frozen=True)
class Plan:
    targets: tuple[PlanTarget, ...]
    graph: ProductionGraph
    recipe_choices: tuple[tuple[str, str], ...] = ()
    """Item id -> recipe id, for every stage of `graph`: pinned, so a revision plans each item the
    way this plan does unless the player asked for another recipe."""
    extends_factory: bool = False
    """Planned on top of the player's existing factory (its spare output used first, its stages
    extended) rather than from raw resources up."""
    available_supply: tuple[tuple[str, float], ...] = ()
    """Item id -> per-minute spare output of the existing factory the plan may draw on, as it was
    when the plan was first made: a revision draws on the same, not on what the plan's own
    machines have added to the factory since."""
    baseline_machines: tuple[tuple[str, float], ...] = ()
    """Recipe id -> how many machines ran it in the save when it came into the plan, clock-scaled
    like the save's production graph, for every recipe the plan has had: what a plan extending the
    factory extends."""
    baseline_buildings: tuple[tuple[str, int], ...] = ()
    """Recipe id -> how many buildings ran it in the save when it came into the plan, whatever
    their clock: what the player builds for the plan is counted from there. A recipe missing here
    came into the plan with no save to count from, and its progress can't be told."""
    revision: int = 1
    changes: tuple[ChangeItem, ...] = ()
    """What this revision changed from the one before it; empty for the first."""
    marked_built: tuple[tuple[str, float], ...] = ()
    """Recipe id -> how many of its machines the player marked built by hand: counted as built
    whatever the save says, which may be older than what they've done. Kept by the conversation
    beside the plan, across its revisions -- not part of what a revision proposes."""


@dataclass(frozen=True)
class StageProgress:
    """How far one stage of a plan is built."""

    node_id: str
    recipe_id: str
    building_id: str
    to_build: float
    """The machines the plan has this stage build: its machines less those that stood already."""
    built: float | None
    """How many of them are built: what the save shows or the player marked, whichever is more --
    never more than `to_build`. `None` when it can't be told: nothing marked, and no save now or
    none when the stage's recipe came into the plan."""
    in_save: float | None = None
    """How many of them stand in the save now, `None` when that can't be told."""
    marked: float = 0.0
    """How many of them the player marked built by hand."""
