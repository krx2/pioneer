"""Composition root — wires config, the real LLM transport, the game data and the Orchestrator into
a runnable assistant. Everything below this module is transport-agnostic and independently testable;
this is the one place a concrete LLM backend gets picked and real data gets loaded off disk
(implementation.md Stage 16).

Run as:

    python -m pioneer.app "I want to produce 10/min of Iron Plate"

Requires a local OpenAI-compatible LLM server (Ollama, llama.cpp, vLLM, ...) reachable at
`PIONEER_LLM_BASE_URL` -- see `.env.example`.

`LiveContext` loads the Knowledge Base from the game's own `docs/en-US.json` export, the player's
factory from the newest save in `PIONEER_SAVE_DIR` (defaulting to the game's dedicated-server save
folder), the resource node data shipped at `docs/resource_nodes.json`, and live state from the
dedicated server, when one is configured, then hands them to `build_context` -- kept pure so the
end-to-end tests build exactly the context the CLI does. It keeps that context current for as long
as the assistant runs: a newer save is read as soon as one appears, and the server is asked again
once its last answer is `SERVER_STATE_TTL_SECONDS` old. Each source degrades independently, per
architecture.md invariant #5: a missing save leaves `existing_graph` unset and the
expansion/diagnosis tools say so rather than inventing a factory, missing node data leaves the
location tool unavailable, and a missing knowledge base leaves planning unavailable rather than
guessing recipes. Nothing here raises on missing data -- only on a missing LLM endpoint, without
which there's no assistant at all.
"""

from __future__ import annotations

import struct
import sys
import threading
import time
import uuid
import zlib
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from pioneer.config import settings
from pioneer.contracts import FactorySite, GameState, ResourceNode, ResponseArtifact
from pioneer.knowledge_base import KnowledgeBase, load_from_file
from pioneer.llm_client import chat_completion, tool_calling_chat_completion
from pioneer.location_advisor import find_factory_sites
from pioneer.orchestrator import OrchestratorContext, OrchestratorUnavailable, handle_query
from pioneer.qa_engine import build_corpus
from pioneer.resource_db import load_from_file as load_resource_database
from pioneer.save_parser import SaveState, find_latest_save, load_save_state
from pioneer.server_client import ServerUnavailable, post_json, query_server_state

_PROJECT_ROOT = Path(__file__).parent.parent.parent
DOCS_JSON = _PROJECT_ROOT / "docs" / "en-US.json"
RESOURCE_NODES_JSON = _PROJECT_ROOT / "docs" / "resource_nodes.json"
DATA_DIR = _PROJECT_ROOT / "data"
"""Local, gitignored runtime data: player feedback and the response log."""
DEFAULT_SERVER_PORT = 7777
SERVER_STATE_TTL_SECONDS = 60.0
"""How long the dedicated server's answer is trusted before it's asked again."""


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


def load_game_state() -> tuple[GameState | None, str]:
    """Live state from the dedicated server when one is configured, plus a note on how that went
    — an unreachable server is reported, never raised (see server_client)."""
    host = settings.dedicated_server_host
    token = settings.dedicated_server_api_token
    if not host or not token:
        return None, "no dedicated server configured"
    base_url = f"https://{host}:{settings.dedicated_server_port or DEFAULT_SERVER_PORT}"
    result = query_server_state(post_json, base_url, token)
    if isinstance(result, ServerUnavailable):
        return None, f"dedicated server unavailable ({result.reason})"
    return result, f"server: {result.phase}, tech tier {result.tech_tier}"


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
        factory_sites=find_factory_sites(state.placements) if state else (),
        technologies=kb.technologies if kb else (),
        transport_tiers=kb.transport_tiers if kb else (),
        unlocked_technology_ids=state.unlocked_technology_ids if state else None,
    )


class LiveContext:
    """The Orchestrator's context, kept as current as its sources: `current()` rereads the newest
    save whenever a different file, or a newer write of the same one, is the newest, and asks the
    dedicated server again once its last answer is older than `server_ttl_seconds`. The knowledge
    base and node data don't change while the assistant runs, so they're taken once.

    A save that fails to parse -- one still being written, or from a game version the parser
    doesn't handle -- leaves the last good one in use, and isn't retried until it changes on disk.
    Thread-safe: the web UI answers questions concurrently.
    """

    def __init__(
        self,
        kb: KnowledgeBase | None,
        resource_nodes: tuple[ResourceNode, ...],
        *,
        save_directory: str | None,
        load_save: Callable[[Path], SaveState] = load_save_state,
        query_server: Callable[[], tuple[GameState | None, str]] = load_game_state,
        server_ttl_seconds: float = SERVER_STATE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = build_context(kb, None, resource_nodes)
        self._static_notes = [
            f"{len(kb.recipes)} recipes" if kb else "no knowledge base",
            f"{len(resource_nodes)} resource nodes" if resource_nodes else "no resource node data",
        ]
        self._save_directory = save_directory
        self._load_save = load_save
        self._query_server = query_server
        self._server_ttl_seconds = server_ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()

        self._save_key: tuple[Path, int, int] | None = None
        self._state: SaveState | None = None
        self._sites: tuple[FactorySite, ...] = ()
        self._state_note = "no save loaded"
        """What `_state` is, for the summary -- kept apart from `_save_note` so a failed read can
        say what's still in use."""
        self._save_note = self._state_note
        self._game_state: GameState | None = None
        self._server_note = ""
        self._server_asked_at: float | None = None
        self._context = self._base
        self._summary = ""

    def current(self) -> tuple[OrchestratorContext, str]:
        """The context as of now, plus a one-line summary of what it was built from."""
        with self._lock:
            changed = self._refresh_save()
            changed = self._refresh_server() or changed
            if changed or not self._summary:
                self._context = replace(
                    self._base,
                    existing_graph=self._state.graph if self._state else None,
                    existing_placements=self._state.placements if self._state else (),
                    existing_links=self._state.links if self._state else (),
                    factory_sites=self._sites,
                    unlocked_technology_ids=(
                        self._state.unlocked_technology_ids if self._state else None
                    ),
                    game_state=self._game_state,
                )
                self._summary = " | ".join(
                    [*self._static_notes, self._save_note, self._server_note]
                )
            return self._context, self._summary

    def _refresh_save(self) -> bool:
        path = find_latest_save(self._save_directory) if self._save_directory else None
        if path is None:
            changed = self._state is not None or self._save_key is not None
            self._save_key, self._state, self._sites = None, None, ()
            self._state_note = self._save_note = "no save loaded"
            return changed
        try:
            stat = path.stat()
        except OSError:
            return False  # gone between finding and looking at it: try again next time
        key = (path, stat.st_mtime_ns, stat.st_size)
        if key == self._save_key:
            return False
        self._save_key = key
        try:
            state = self._load_save(path)
        except (OSError, ValueError, struct.error, zlib.error) as error:
            kept = "no save loaded" if self._state is None else f"still using {self._state_note}"
            self._save_note = f"could not read {path.name} ({error}); {kept}"
            print(f"warning: could not parse {path.name}: {error}", file=sys.stderr)
            return True
        self._state = state
        self._sites = find_factory_sites(state.placements)
        machines = sum(node.machine_count for node in state.graph.nodes)
        self._state_note = self._save_note = (
            f"save {path.name}: {len(state.placements)} buildings, "
            f"{machines:g} effective machines running {len(state.graph.nodes)} recipes"
        )
        return True

    def _refresh_server(self) -> bool:
        now = self._clock()
        asked_at = self._server_asked_at
        if asked_at is not None and now - asked_at < self._server_ttl_seconds:
            return False
        self._server_asked_at = now
        game_state, note = self._query_server()
        changed = (game_state, note) != (self._game_state, self._server_note)
        self._game_state, self._server_note = game_state, note
        return changed


def live_context() -> LiveContext:
    """A `LiveContext` over what's on disk and the server `settings` name."""
    return LiveContext(
        load_knowledge_base(), load_resource_nodes(), save_directory=settings.save_directory
    )


def load_context() -> tuple[OrchestratorContext, str]:
    """The context built from what's on disk right now, plus a one-line summary of what actually
    got loaded, for the CLI to report."""
    return live_context().current()


def ask(
    question: str,
    context: OrchestratorContext | None = None,
    history: Sequence[tuple[str, str]] = (),
) -> ResponseArtifact | OrchestratorUnavailable:
    """One answer from the configured model; `history` is the conversation so far, as
    (question, answer) pairs."""
    if not settings.llm_base_url or not settings.llm_model:
        raise RuntimeError(
            "PIONEER_LLM_BASE_URL / PIONEER_LLM_MODEL are not set -- copy .env.example to .env "
            "and point them at your local Ollama/llama.cpp/vLLM server first."
        )
    if context is None:
        context, _ = load_context()
    return handle_query(
        tool_calling_chat_completion,
        chat_completion,
        question,
        context,
        llm_base_url=settings.llm_base_url,
        llm_model=settings.llm_model,
        llm_api_key=settings.llm_api_key,
        response_id=str(uuid.uuid4()),
        history=history,
    )


def main() -> None:
    if len(sys.argv) < 2:
        print('usage: python -m pioneer.app "your question"')
        raise SystemExit(1)

    context, summary = load_context()
    print(f"[{summary}]", file=sys.stderr)

    result = ask(" ".join(sys.argv[1:]), context)
    if isinstance(result, OrchestratorUnavailable):
        print(f"Pioneer is unavailable: {result.reason}")
        raise SystemExit(1)
    print(result.chat or "(no answer)")


if __name__ == "__main__":
    main()
