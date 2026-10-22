"""Location shapes: ranked build sites for new extraction, the player's existing factories, and
where a plan is to be built -- which deposits feed it, worked how, and on how many sites.

Produced by the Location Advisor (Stage 9); consumed by Map presentation (Stage 14).
"""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.contracts.geometry import Coordinates
from pioneer.contracts.placement import PlacementRecord
from pioneer.contracts.resources import Purity


@dataclass(frozen=True)
class RankedLocation:
    resource_node_id: str
    position: Coordinates
    purity: Purity
    distance_to_reference: float
    score: float


@dataclass(frozen=True)
class FactorySite:
    """One of the player's factories: running production buildings standing close together."""

    site_id: str
    position: Coordinates
    """The middle of its buildings."""
    placements: tuple[PlacementRecord, ...]


@dataclass(frozen=True)
class Extractor:
    """The extractor a resource node would be worked with, and how hard it can be pushed."""

    building_id: str
    rate_per_minute: float
    """At 100% clock speed on a normal node (m³ for fluids)."""
    max_clock_speed: float
    """2.5 for one overclocked with three power shards; 1.0 for one that isn't."""
    output_capacity: float
    """The most the belt or pipe out of it carries a minute: what it's no use pushing past."""
    unlocked: bool | None = True
    """Whether the player has it -- `None`: unknown, no save says. An extractor they don't have is
    named only where none they do could work the node."""


@dataclass(frozen=True)
class NodeAssignment:
    """A resource node a plan is to be fed from, and how it's to be worked."""

    resource_node_id: str
    item_id: str
    purity: Purity
    position: Coordinates
    extractor_id: str
    clock_speed: float
    """1.0 is 100%; past it, the extractor is overclocked with power shards."""
    power_shards: int
    """One per 50 points of clock speed past 100%: three take it to 250%."""
    rate_per_minute: float
    site_id: str
    """The `BuildSite` it feeds."""


@dataclass(frozen=True)
class SiteStage:
    """The part of one of a plan's stages a site builds."""

    recipe_id: str
    building_id: str
    machine_count: float
    """Whole machines: the site's share of the stage's machines, rounded up."""
    share: float
    """How much of the stage the site makes, from 0 to 1."""


@dataclass(frozen=True)
class SiteTransfer:
    """What one site of a plan sends another, a minute."""

    item_id: str
    from_site_id: str
    to_site_id: str
    rate_per_minute: float


@dataclass(frozen=True)
class BuildSite:
    """One place a plan is built at: its deposits next to each other, and what it makes there."""

    site_id: str
    position: Coordinates
    """The middle of its deposits, weighed by what each gives."""
    stages: tuple[SiteStage, ...] = ()
    """Empty when where the plan's stages go wasn't asked -- only where its resources come from."""


@dataclass(frozen=True)
class SitePlan:
    """Where a plan is to be built: one site with every deposit it needs close by, or several,
    each making the parts its own deposits are for, sending them on to where they're needed."""

    sites: tuple[BuildSite, ...]
    deposits: tuple[NodeAssignment, ...]
    transfers: tuple[SiteTransfer, ...] = ()
    shortfall: tuple[tuple[str, float], ...] = ()
    """Item id -> per minute no free deposit is left for."""


@dataclass(frozen=True)
class SupplyReservation:
    """What a plan takes a minute of one item one of the player's factories has to spare — just
    what the plan uses, the rest of it left for anything else."""

    site_id: str
    """The factory, as the save named it then (`FactorySite.site_id`): ids follow the factories'
    sizes, so a later save may call it otherwise — `position` finds it again."""
    position: Coordinates
    item_id: str
    rate_per_minute: float
