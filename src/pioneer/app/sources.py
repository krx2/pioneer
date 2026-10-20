"""What the assistant's context is built from right now, source by source — what the web UI's
header buttons and panels show, and, joined into one line by `Sources.summary`, the status the CLI
prints and `/api/ask` answers with."""

from __future__ import annotations

from dataclasses import dataclass

from pioneer.contracts import FactorySite, Recipe, ResourceNode, Technology
from pioneer.knowledge_base import TierProgress
from pioneer.location_advisor import SiteFeed


@dataclass(frozen=True)
class RecipeStatus:
    total: int
    enabled: int
    """How many recipes planning may use: the player's choice, else every recipe the save has
    unlocked — all of them while nothing says what's unlocked."""
    locked_enabled: int = 0
    """Of `enabled`, how many the game hasn't unlocked yet: a plan may ask for those to be."""
    customized: bool = False
    """Whether the player changed the default."""


@dataclass(frozen=True)
class RecipeState:
    """One recipe as the Recipes panel shows it."""

    recipe: Recipe
    unlocked: bool | None
    """Whether the save has it unlocked; `None` when that's unknown."""
    enabled: bool
    """Whether plans may use it."""
    default: bool
    """Whether they would without the player's pick: it's unlocked, or nothing says it isn't."""
    unlock_with: Technology | None = None
    """For a recipe not unlocked yet, the easiest technology that unlocks it."""


@dataclass(frozen=True)
class SiteOverview:
    """One of the player's factories as the Map panel shows it."""

    site: FactorySite
    main_product: str | None
    """What it's there for: of what it has to spare, the item the most of its machines make."""
    balance: dict[str, float]
    """Net per item a minute: what it needs from outside is negative, its surplus positive."""
    feeds: tuple[SiteFeed, ...]
    """The resource nodes its extractors work."""


@dataclass(frozen=True)
class MapOverview:
    sites: tuple[SiteOverview, ...]
    nodes: tuple[ResourceNode, ...]
    occupied: frozenset[str]
    """The nodes an extractor works or something is built on."""


@dataclass(frozen=True)
class TechSection:
    """One part of the Tech panel: a tier of HUB milestones, a MAM tree, the hard drives."""

    group: str
    """"HUB", "MAM" or "Hard drives"."""
    title: str
    technologies: tuple[Technology, ...]


@dataclass(frozen=True)
class TechOverview:
    """Every technology the player can unlock, and which ones they have."""

    unlocked: frozenset[str] | None
    """`None` when no save says."""
    tiers: tuple[TierProgress, ...]
    sections: tuple[TechSection, ...]
    unlocks: dict[str, tuple[str, ...]]
    """The recipes each technology unlocks, by technology id."""


@dataclass(frozen=True)
class TechStatus:
    known: bool
    """Whether the save says what's unlocked."""
    tier: int | None = None
    """The highest tier with a milestone unlocked."""
    milestones_unlocked: int = 0
    milestones_total: int = 0


@dataclass(frozen=True)
class MapStatus:
    sites: int
    """The player's factories (see `location_advisor.find_factory_sites`)."""
    resource_nodes: int


@dataclass(frozen=True)
class SaveStatus:
    mode: str
    """How the save is picked (see `preferences.SaveMode`)."""
    file: str | None = None
    """The save in use, by file name."""
    path: str | None = None
    session: str | None = None
    buildings: int = 0
    machines: float = 0.0
    """Effective machines: a clocked-down one counts for less than one."""
    recipes_running: int = 0
    loading: str | None = None
    """The save being read in the background, by file name, while `file` stays in use."""
    error: str | None = None
    """Why the save that should be in use isn't: unreadable, or gone."""


@dataclass(frozen=True)
class ServerStatus:
    configured: bool
    source: str | None = None
    """Where the configuration comes from: "env", or "page" once set in the web UI — which then
    wins, also when it's set to nothing."""
    host: str | None = None
    port: int | None = None
    has_token: bool = False
    phase: str | None = None
    tech_tier: int | None = None
    session: str | None = None
    error: str | None = None
    """Why its state isn't known: unreachable, no token, a token it refused."""


@dataclass(frozen=True)
class Sources:
    knowledge_base: bool
    recipes: RecipeStatus
    tech: TechStatus
    map: MapStatus
    save: SaveStatus
    server: ServerStatus
    context_window_warning: str | None = None
    """Set when the model's context window is too small for its answers to be trusted."""

    def summary(self) -> str:
        """One line saying what loaded, for the CLI and `/api/ask`."""
        notes = [
            f"{self.recipes.total} recipes" if self.knowledge_base else "no knowledge base",
            f"{self.map.resource_nodes} resource nodes"
            if self.map.resource_nodes
            else "no resource node data",
            _save_note(self.save),
            _server_note(self.server),
        ]
        if self.context_window_warning:
            notes.append(self.context_window_warning)
        return " | ".join(notes)


def _save_note(save: SaveStatus) -> str:
    in_use = (
        f"save {save.file}: {save.buildings} buildings, {save.machines:g} effective machines "
        f"running {save.recipes_running} recipes"
        if save.file
        else None
    )
    kept = f"still using {in_use}" if in_use else "no save loaded"
    if save.loading:
        return f"loading {save.loading}…; {kept}" if in_use else f"loading {save.loading}…"
    if save.error:
        return f"{save.error}; {kept}"
    return in_use or "no save loaded"


def _server_note(server: ServerStatus) -> str:
    if not server.configured:
        return "no dedicated server configured"
    if server.error:
        return f"dedicated server unavailable ({server.error})"
    if server.phase is None:
        return "dedicated server not asked yet"
    return f"server: {server.phase}, tech tier {server.tech_tier}"
