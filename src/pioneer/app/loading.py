"""Loading each data source off disk or the network, each degrading on its own (architecture.md
invariant #5), and `build_context`, which turns whatever loaded into the Orchestrator's context —
kept pure, so the end-to-end tests build exactly the context the CLI does."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pioneer.app.preferences import ServerConfig
from pioneer.contracts import GameState, ResourceNode
from pioneer.knowledge_base import KnowledgeBase, load_from_file
from pioneer.location_advisor import find_factory_sites
from pioneer.orchestrator import OrchestratorContext
from pioneer.qa_engine import build_corpus
from pioneer.resource_db import load_from_file as load_resource_database
from pioneer.save_parser import SaveState
from pioneer.server_client import (
    LoginToken,
    PostJson,
    ServerUnavailable,
    health_check,
    log_in,
    post_json,
    query_server_state,
    verify_token,
)

_PROJECT_ROOT = Path(__file__).parents[3]
DOCS_JSON = _PROJECT_ROOT / "docs" / "en-US.json"
RESOURCE_NODES_JSON = _PROJECT_ROOT / "docs" / "resource_nodes.json"
DATA_DIR = _PROJECT_ROOT / "data"
"""Local, gitignored runtime data: player feedback, the response log, the conversations and the
choices made in the web UI's panels."""
SETTINGS_JSON = DATA_DIR / "settings.json"
DEFAULT_SERVER_PORT = 7777


def load_knowledge_base(path: Path = DOCS_JSON) -> KnowledgeBase | None:
    if not path.is_file():
        return None
    try:
        return load_from_file(path)
    except (OSError, ValueError, KeyError):
        return None


def load_resource_nodes(path: Path = RESOURCE_NODES_JSON) -> tuple[ResourceNode, ...]:
    """The shipped resource node data; empty if the file is missing or can't be read."""
    if not path.is_file():
        return ()
    try:
        return load_resource_database(path).nodes
    except (OSError, ValueError, KeyError, TypeError):
        return ()


def server_base_url(server: ServerConfig) -> str:
    return f"https://{server.host}:{server.port}"


def query_game_state(
    server: ServerConfig, post: PostJson = post_json
) -> GameState | ServerUnavailable:
    """Live state from the dedicated server — an unreachable one is reported, never raised (see
    server_client)."""
    if not server.token:
        return ServerUnavailable(reason="no API token")
    return query_server_state(post, server_base_url(server), server.token)


@dataclass(frozen=True)
class ServerCheck:
    """Whether a server answers, and whether it takes a token."""

    reachable: bool
    health: str | None = None
    """"healthy", or "slow" when it ticks fewer than ten times a second."""
    token_accepted: bool | None = None
    """`None` when there was no token to try."""
    error: str | None = None


def check_server(server: ServerConfig, post: PostJson = post_json) -> ServerCheck:
    """`HealthCheck` on `server`, and, with a token, whether the server takes it."""
    base_url = server_base_url(server)
    health = health_check(post, base_url)
    if isinstance(health, ServerUnavailable):
        return ServerCheck(reachable=False, error=health.reason)
    if not server.token:
        return ServerCheck(reachable=True, health=health.health)
    accepted = verify_token(post, base_url, server.token)
    if isinstance(accepted, ServerUnavailable):
        return ServerCheck(reachable=True, health=health.health, error=accepted.reason)
    return ServerCheck(reachable=True, health=health.health, token_accepted=accepted)


def server_login(
    server: ServerConfig, password: str, post: PostJson = post_json
) -> LoginToken | ServerUnavailable:
    """A token for `server` from its admin password (see `server_client.log_in`)."""
    return log_in(post, server_base_url(server), password)


def build_context(
    kb: KnowledgeBase | None,
    state: SaveState | None,
    resource_nodes: tuple[ResourceNode, ...] = (),
    game_state: GameState | None = None,
) -> OrchestratorContext:
    """The Orchestrator's view of whichever data sources actually loaded. The Q&A corpus is built
    here from the knowledge base: the game's own descriptions plus a passage per recipe."""
    corpus = (
        build_corpus(kb.descriptions, kb.recipes, kb.items, kb.buildings, kb.technologies)
        if kb
        else ()
    )
    return OrchestratorContext(
        recipes=kb.recipes if kb else (),
        buildings=kb.buildings if kb else (),
        items=kb.items if kb else (),
        resource_nodes=resource_nodes,
        qa_corpus=corpus,
        game_state=game_state,
        existing_graph=state.graph if state else None,
        existing_placements=state.placements if state else (),
        existing_links=state.links if state else (),
        factory_sites=find_factory_sites(state.placements, state.links) if state else (),
        technologies=kb.technologies if kb else (),
        transport_tiers=kb.transport_tiers if kb else (),
        unlocked_technology_ids=state.unlocked_technology_ids if state else None,
    )
