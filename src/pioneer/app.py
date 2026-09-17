"""Composition root — wires config, the real LLM transport, the game data and the Orchestrator into
a runnable assistant. Everything below this module is transport-agnostic and independently testable;
this is the one place a concrete LLM backend gets picked and real data gets loaded off disk
(implementation.md Stage 16).

Run as:

    python -m pioneer.app "I want to produce 10/min of Iron Plate"

Requires a local OpenAI-compatible LLM server (Ollama, llama.cpp, vLLM, ...) reachable at
`PIONEER_LLM_BASE_URL` -- see `.env.example`.

`load_context` loads the Knowledge Base from the game's own `docs/en-US.json` export, the player's
factory from the newest save in `PIONEER_SAVE_DIR` (defaulting to the game's dedicated-server save
folder), the resource node data shipped at `docs/resource_nodes.json`, and live state from the
dedicated server, when one is configured, then hands them to `build_context` -- kept pure so the
end-to-end tests build exactly the context the CLI does. Each source degrades independently, per
architecture.md invariant #5: a missing save leaves `existing_graph` unset and the
expansion/diagnosis tools say so rather than inventing a factory, missing node data leaves the
location tool unavailable, and a missing knowledge base leaves planning unavailable rather than
guessing recipes. Nothing here raises on missing data -- only on a missing LLM endpoint, without
which there's no assistant at all.
"""

from __future__ import annotations

import struct
import sys
import uuid
import zlib
from pathlib import Path

from pioneer.config import settings
from pioneer.contracts import GameState, ResourceNode, ResponseArtifact
from pioneer.knowledge_base import KnowledgeBase, load_from_file
from pioneer.llm_client import chat_completion, tool_calling_chat_completion
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


def load_latest_save_state() -> tuple[SaveState | None, Path | None]:
    """The newest save's parsed state, plus which file it came from (for reporting). Both `None`
    when no save directory is configured, none is found, or the file can't be parsed."""
    if not settings.save_directory:
        return None, None
    save_path = find_latest_save(settings.save_directory)
    if save_path is None:
        return None, None
    try:
        return load_save_state(save_path), save_path
    except (OSError, ValueError, struct.error, zlib.error) as error:
        # A save being written as we read it, or from a game version this parser doesn't handle
        # yet, degrades to "no factory state" rather than taking the whole assistant down.
        print(f"warning: could not parse {save_path.name}: {error}", file=sys.stderr)
        return None, save_path


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
    )


def load_context() -> tuple[OrchestratorContext, str]:
    """The context built from what's on disk, plus a one-line summary of what actually got loaded,
    for the CLI to report."""
    kb = load_knowledge_base()
    state, save_path = load_latest_save_state()
    resource_nodes = load_resource_nodes()
    game_state, server_note = load_game_state()

    notes = [f"{len(kb.recipes)} recipes" if kb else "no knowledge base"]
    notes.append(
        f"{len(resource_nodes)} resource nodes" if resource_nodes else "no resource node data"
    )
    if state is not None and save_path is not None:
        machines = sum(node.machine_count for node in state.graph.nodes)
        notes.append(
            f"save {save_path.name}: {len(state.placements)} buildings, "
            f"{machines:g} effective machines running {len(state.graph.nodes)} recipes"
        )
    else:
        notes.append("no save loaded")
    notes.append(server_note)

    return build_context(kb, state, resource_nodes, game_state), " | ".join(notes)


def ask(
    question: str, context: OrchestratorContext | None = None
) -> ResponseArtifact | OrchestratorUnavailable:
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
