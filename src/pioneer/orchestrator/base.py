"""What every part of the Orchestrator shares: its context, its typed failure, the tool
shape and the accumulator tool handlers publish their structured output into."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pioneer.contracts import (
    Building,
    Coordinates,
    FactorySite,
    GameState,
    Item,
    PlacementRecord,
    ProductionGraph,
    RankedLocation,
    Recipe,
    ResourceNode,
    Technology,
    TransportLink,
    TransportTier,
)
from pioneer.qa_engine import Passage

_CM_PER_M = 100.0
"""Save coordinates are centimetres; tool results speak metres, so the model never converts."""


@dataclass(frozen=True)
class OrchestratorUnavailable:
    """Typed "couldn't produce a response" result -- architecture.md §7.5's graceful-degradation
    invariant applied to the Orchestrator itself: an unreachable LLM or a runaway tool-call loop
    comes back as this, never a raised exception."""

    reason: str


class ToolCallingLLM(Protocol):
    """Sends `messages` (OpenAI chat/completions shape) plus `tools` (OpenAI tool-schema shape) to
    `base_url` for `model`, returning the assistant's reply as `{"content": str | None,
    "tool_calls": [{"id": str, "name": str, "arguments": dict[str, Any]}, ...]}` -- each tool
    call's arguments already parsed from the wire format's JSON string, so this module never
    touches that wire format directly. Raises `contracts.TransportError` if the request couldn't
    complete at all."""

    def __call__(
        self,
        base_url: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        api_key: str | None,
    ) -> dict[str, Any]: ...


ProgressSink = Callable[[dict[str, Any]], None]
"""Told what the Orchestrator is doing while an answer is on its way, one event at a time, each a
JSON-ready dict with a `type`: `status` (a line saying what it's on), `tool` (a tool about to run,
its `name` and what it's doing in words), `discard` (the text streamed so far was not the answer:
the round ended in tool calls). The streaming transport adds `text` and `thinking`."""


@dataclass(frozen=True)
class OrchestratorContext:
    """Everything the Orchestrator needs beyond the player's question -- plain data, no I/O, the
    same dependency-injection pattern every other module uses. Every field defaults to empty/None
    so a missing data source (no save loaded, no dedicated server reachable, no knowledge base
    wired yet) degrades gracefully (architecture.md invariant #5) instead of crashing a tool call.
    """

    recipes: tuple[Recipe, ...] = ()
    buildings: tuple[Building, ...] = ()
    items: tuple[Item, ...] = ()
    """Every item the Knowledge Base knows: resolves the in-game names the model passes to tools
    into ids, marks the raw resources planning must stop at, and carries the fuels' energy values.
    Empty (no knowledge base) means item ids are passed through to the modules unchecked."""
    resource_nodes: tuple[ResourceNode, ...] = ()
    existing_graph: ProductionGraph | None = None
    """The player's current factory state, e.g. from the Save Parser. `None` means no save is
    loaded -- expansion/diagnosis tools report that explicitly rather than fabricating a factory."""
    existing_placements: tuple[PlacementRecord, ...] = ()
    existing_links: tuple[TransportLink, ...] = ()
    """Which of `existing_placements` the save's belts and pipes join. Empty when unknown: then a
    drawing's flows are shared out by the recipes alone, and wiring isn't diagnosed."""
    qa_corpus: tuple[Passage, ...] = ()
    game_state: GameState | None = None
    available_power_mw: float | None = None
    """Overrides the grid capacity diagnosis would otherwise derive from the placed generators."""
    technologies: tuple[Technology, ...] = ()
    transport_tiers: tuple[TransportTier, ...] = ()
    factory_sites: tuple[FactorySite, ...] = ()
    """Where the player's factories stand (see `location_advisor.find_factory_sites`)."""
    unlocked_technology_ids: frozenset[str] | None = None
    """Every technology the player has unlocked, from the save. `None`: unknown, so every recipe is
    treated as available."""


@dataclass
class _ArtifactAccumulator:
    """Collects the *real* structured output of whichever tool(s) actually ran this turn, so the
    final `ResponseArtifact` is built from module output, never re-derived from the LLM's text."""

    graph: ProductionGraph | None = None
    map_locations: tuple[RankedLocation, ...] | None = None
    map_reference: Coordinates | None = None
    factory_sites: tuple[FactorySite, ...] | None = None
    grounding: list[str] = field(default_factory=list)


class _ToolError(Exception):
    """A tool's own, expected failure (unknown item, no knowledge base, ...), reported back to the
    model as `{"error": message, **details}` -- see `_run_tool`."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]

    def as_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
