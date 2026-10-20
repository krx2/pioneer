"""Composition root — wires config, the real LLM transport, the game data and the Orchestrator into
a runnable assistant. Everything below this package is transport-agnostic and independently
testable; this is the one place a concrete LLM backend gets picked and real data gets loaded off
disk (implementation.md Stage 16).

Run as:

    python -m pioneer.app "I want to produce 10/min of Iron Plate"

Requires a local OpenAI-compatible LLM server (Ollama, llama.cpp, vLLM, ...) reachable at
`PIONEER_LLM_BASE_URL` -- see `.env.example`.

- `loading`: the knowledge base from the game's own `docs/en-US.json` export, the resource node
  data shipped at `docs/resource_nodes.json`, live state from the dedicated server, and
  `build_context`, which turns whatever loaded into the Orchestrator's context.
- `live`: `LiveContext`, which keeps that context current while the assistant runs -- a newer
  save is read as soon as one appears, the server is asked again once its answer is stale -- and
  which the web UI's panels steer: which save to read, which server to ask.
- `sources`: what the context is built from right now, source by source, for the page's header.
- `preferences`: the choices made in those panels, kept in `data/settings.json` over `.env`.
- `runtime`: the assistant as configured, and `ask`.

Each source degrades independently, per architecture.md invariant #5: a missing save leaves
`existing_graph` unset and the expansion/diagnosis tools say so rather than inventing a factory,
missing node data leaves the location tool unavailable, and a missing knowledge base leaves
planning unavailable rather than guessing recipes. Nothing here raises on missing data -- only on
a missing LLM endpoint, without which there's no assistant at all.
"""

from pioneer.app.live import SERVER_STATE_TTL_SECONDS, LiveContext
from pioneer.app.loading import (
    DATA_DIR,
    DEFAULT_SERVER_PORT,
    DOCS_JSON,
    RESOURCE_NODES_JSON,
    SETTINGS_JSON,
    build_context,
    load_knowledge_base,
    load_resource_nodes,
    query_game_state,
)
from pioneer.app.preferences import (
    PreferencesStore,
    RecipeChoice,
    SaveMode,
    SaveSelection,
    ServerConfig,
)
from pioneer.app.runtime import ask, env_server, live_context, load_context
from pioneer.app.sources import (
    MapOverview,
    MapStatus,
    RecipeState,
    RecipeStatus,
    SaveStatus,
    ServerStatus,
    SiteOverview,
    Sources,
    TechOverview,
    TechSection,
    TechStatus,
)

__all__ = [
    "DATA_DIR",
    "DEFAULT_SERVER_PORT",
    "DOCS_JSON",
    "RESOURCE_NODES_JSON",
    "SERVER_STATE_TTL_SECONDS",
    "SETTINGS_JSON",
    "LiveContext",
    "MapOverview",
    "MapStatus",
    "PreferencesStore",
    "RecipeChoice",
    "RecipeState",
    "RecipeStatus",
    "SaveMode",
    "SaveSelection",
    "SaveStatus",
    "ServerConfig",
    "ServerStatus",
    "SiteOverview",
    "Sources",
    "TechOverview",
    "TechSection",
    "TechStatus",
    "ask",
    "build_context",
    "env_server",
    "live_context",
    "load_context",
    "load_knowledge_base",
    "load_resource_nodes",
    "query_game_state",
]
