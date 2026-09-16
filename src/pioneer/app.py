"""Composition root — wires config, the real LLM transport, the game data and the Orchestrator into
a runnable assistant. Everything below this module is transport-agnostic and independently testable;
this is the one place a concrete LLM backend gets picked and real data gets loaded off disk
(implementation.md Stage 16).

Run as:

    python -m pioneer.app "I want to produce 10/min of Iron Plate"

Requires a local OpenAI-compatible LLM server (Ollama, llama.cpp, vLLM, ...) reachable at
`PIONEER_LLM_BASE_URL` -- see `.env.example`.

`load_context` loads the Knowledge Base from the game's own `docs/en-US.json` export and the
player's factory from the newest save in `PIONEER_SAVE_DIR` (defaulting to the game's dedicated-
server save folder), then hands both to `build_context` -- kept pure so the end-to-end tests build
exactly the context the CLI does. Each source degrades independently, per architecture.md
invariant #5: a missing save leaves `existing_graph` unset and the expansion/diagnosis tools say so
rather than inventing a factory, and a missing knowledge base leaves planning unavailable rather
than guessing recipes. Nothing here raises on missing data -- only on a missing LLM endpoint,
without which there's no assistant at all.
"""

from __future__ import annotations

import struct
import sys
import uuid
import zlib
from pathlib import Path

from pioneer.config import settings
from pioneer.contracts import ResponseArtifact
from pioneer.knowledge_base import KnowledgeBase, load_from_file
from pioneer.llm_client import chat_completion, tool_calling_chat_completion
from pioneer.orchestrator import OrchestratorContext, OrchestratorUnavailable, handle_query
from pioneer.save_parser import SaveState, find_latest_save, load_save_state

DOCS_JSON = Path(__file__).parent.parent.parent / "docs" / "en-US.json"


def load_knowledge_base(path: Path = DOCS_JSON) -> KnowledgeBase | None:
    if not path.is_file():
        return None
    try:
        return load_from_file(path)
    except (OSError, ValueError, KeyError):
        return None


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


def build_context(kb: KnowledgeBase | None, state: SaveState | None) -> OrchestratorContext:
    """The Orchestrator's view of whichever data sources actually loaded."""
    return OrchestratorContext(
        recipes=kb.recipes if kb else (),
        buildings=kb.buildings if kb else (),
        items=kb.items if kb else (),
        existing_graph=state.graph if state else None,
        existing_placements=state.placements if state else (),
    )


def load_context() -> tuple[OrchestratorContext, str]:
    """The context built from what's on disk, plus a one-line summary of what actually got loaded,
    for the CLI to report."""
    kb = load_knowledge_base()
    state, save_path = load_latest_save_state()

    notes = [f"{len(kb.recipes)} recipes" if kb else "no knowledge base"]
    if state is not None and save_path is not None:
        machines = sum(node.machine_count for node in state.graph.nodes)
        notes.append(
            f"save {save_path.name}: {len(state.placements)} buildings, "
            f"{machines:g} effective machines running {len(state.graph.nodes)} recipes"
        )
    else:
        notes.append("no save loaded")

    return build_context(kb, state), " | ".join(notes)


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
