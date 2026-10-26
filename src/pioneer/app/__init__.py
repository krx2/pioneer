"""Composition root: the one place the real LLM transport is picked and real data is loaded.

- `loading`: each data source off disk or the network, and `build_context`.
- `live`: `LiveContext`, which keeps the context current while the assistant runs.
- `sources`: what the context is built from right now, source by source.
- `preferences`: the choices made in the web UI's panels, kept over `.env`.
- `runtime`: the assistant as configured, and `ask`.

Each source degrades on its own: nothing here raises on missing data, only on a missing LLM
endpoint.
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
