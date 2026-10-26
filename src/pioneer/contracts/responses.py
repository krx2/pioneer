"""The response artifact shape: one assistant turn's Chat + Graph + Map payload, plus feedback."""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.contracts.geometry import Coordinates
from pioneer.contracts.locations import FactorySite, RankedLocation, SitePlan
from pioneer.contracts.plan import Plan
from pioneer.contracts.production_graph import ProductionGraph


@dataclass(frozen=True)
class Feedback:
    thumbs_up: bool | None = None
    """Chat channel: did the player like the answer."""
    applied_plan: bool | None = None
    """Graph channel: did they apply the plan."""
    built_at_location: bool | None = None
    """Map channel: did they build there."""
    qualitative_score: int | None = None
    """1-5."""


@dataclass(frozen=True)
class ResponseArtifact:
    response_id: str
    chat: str | None = None
    graph: ProductionGraph | None = None
    map_locations: tuple[RankedLocation, ...] | None = None
    map_reference: Coordinates | None = None
    """The point `map_locations`' `distance_to_reference` is measured from."""
    factory_sites: tuple[FactorySite, ...] | None = None
    """The player's existing factories the answer points at — where an expansion extends."""
    build_sites: SitePlan | None = None
    """Where the answer's plan is to be built — its deposits, worked how, on how many sites, and
    what goes between them. Its deposits are `map_locations` too."""
    feedback: Feedback | None = None
    question: str | None = None
    """The player's message this answers."""
    grounding: tuple[str, ...] = ()
    """What the chat answer was built from, and is checked against: the question, every tool
    result the model saw (with the game's names for the ids in it), and the text of every
    knowledge passage it retrieved."""
    missing_recipes: tuple[str, ...] = ()
    """Recipes the player turned off that a plan in this answer would need — any of them, for the
    page to offer turning on."""
    plan_proposal: Plan | None = None
    """The plan this answer offers the player to accept as their conversation's plan — the first
    one, or a revision of the one they have. Its graph is the answer's `graph`."""
