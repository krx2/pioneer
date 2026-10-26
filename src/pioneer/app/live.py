"""`LiveContext`: the Orchestrator's context, kept as current as its sources, and the controls the
web UI's panels change those sources with.

The knowledge base and node data are taken once; the save is `saves.SaveWatcher`'s, the dedicated
server `server_link.ServerLink`'s, and which recipes plans may use is the player's pick
(`recipes`). This puts them together, rebuilding the context whenever one of them changed.
Thread-safe: the web UI answers questions concurrently.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from pioneer.app.loading import ServerCheck, build_context
from pioneer.app.overviews import map_overview, tech_overview
from pioneer.app.preferences import (
    PreferencesStore,
    RecipeChoice,
    SaveSelection,
    ServerConfig,
)
from pioneer.app.recipes import (
    changed_choice,
    enabled_recipe_ids,
    recipe_states,
    store_choice,
    stored_choice,
)
from pioneer.app.saves import SaveWatcher
from pioneer.app.server_link import QueryServer, ServerLink
from pioneer.app.sources import (
    MapOverview,
    MapStatus,
    RecipeState,
    RecipeStatus,
    SaveStatus,
    ServerStatus,
    Sources,
    TechOverview,
    TechStatus,
)
from pioneer.contracts import ResourceNode
from pioneer.knowledge_base import KnowledgeBase, current_tier, milestone_progress
from pioneer.orchestrator import OrchestratorContext
from pioneer.save_parser import (
    SaveFileInfo,
    SaveHeader,
    SaveState,
    load_save_state,
    read_save_header,
)
from pioneer.server_client import PostJson, post_json

SERVER_STATE_TTL_SECONDS = 60.0
"""How long the dedicated server's answer is trusted before it's asked again."""
WARNING_TTL_SECONDS = 30.0
"""How long the model's context window check is trusted: it's a request to the model server."""


class LiveContext:
    def __init__(
        self,
        kb: KnowledgeBase | None,
        resource_nodes: tuple[ResourceNode, ...],
        *,
        save_directory: str | None,
        save_root: str | None = None,
        server: ServerConfig | None = None,
        preferences: PreferencesStore | None = None,
        load_save: Callable[[Path], SaveState] = load_save_state,
        read_header: Callable[[Path], SaveHeader] = read_save_header,
        query_server: QueryServer | None = None,
        post: PostJson = post_json,
        server_ttl_seconds: float = SERVER_STATE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        background: bool = False,
        context_warning: Callable[[], str | None] | None = None,
    ) -> None:
        """`save_directory` and `server` are what `.env` says; `preferences` holds what the player
        picked since, which wins. `save_root` is the game's `SaveGames` folder, whose saves
        `list_saves` lists. `post` is the transport to the dedicated server; `query_server` asks
        it for its state, by default over `post`."""
        self._kb = kb
        self._resource_nodes = resource_nodes
        self._base = build_context(kb, None, resource_nodes)
        self._preferences = preferences if preferences is not None else PreferencesStore(None)
        self._clock = clock
        self._context_warning = context_warning
        self._lock = threading.Lock()
        self._saves = SaveWatcher(
            self._lock,
            self._preferences,
            save_directory=save_directory,
            save_root=save_root,
            load_save=load_save,
            read_header=read_header,
            background=background,
        )
        self._server = ServerLink(
            self._preferences,
            env_server=server,
            post=post,
            query_server=query_server,
            ttl_seconds=server_ttl_seconds,
            clock=clock,
        )
        self._warning: str | None = None
        self._warning_asked_at: float | None = None

        self._context = self._base
        self._sources: Sources | None = None
        """`None`: to be described again, along with `_context`, on the next look."""

    # --- What the assistant knows ----------------------------------------------------------------

    def current(self, *, wait: bool = False) -> tuple[OrchestratorContext, str]:
        """The context as of now, plus a one-line summary of what it was built from. With `wait`,
        a save being read in the background is waited for rather than left to arrive later."""
        with self._lock:
            sources = self._refreshed(wait)
            return self._context, sources.summary()

    def sources(self) -> Sources:
        """What the context is built from right now, source by source."""
        with self._lock:
            return self._refreshed(False)

    def _refreshed(self, wait: bool) -> Sources:
        changed = self._saves.refresh()
        changed = self._server.refresh() or changed
        changed = self._refresh_warning() or changed
        if wait:
            self._saves.wait()
        if self._saves.news() or changed or self._sources is None:
            self._rebuild()
        assert self._sources is not None
        return self._sources

    def _rebuild(self) -> None:
        state = self._saves.state
        recipes = self._recipe_states()
        self._context = replace(
            self._base,
            existing_graph=state.graph if state else None,
            existing_placements=state.placements if state else (),
            existing_links=state.links if state else (),
            factory_sites=self._saves.sites,
            unlocked_technology_ids=state.unlocked_technology_ids if state else None,
            game_state=self._server.game_state,
            enabled_recipe_ids=enabled_recipe_ids(recipes),
        )
        self._sources = self._describe(recipes)

    def _describe(self, recipes: tuple[RecipeState, ...]) -> Sources:
        saves = self._saves
        state = saves.state
        unlocked = state.unlocked_technology_ids if state else None
        progress = milestone_progress(self._kb.technologies if self._kb else (), unlocked or ())
        server = self._server.config()
        game = self._server.game_state
        return Sources(
            knowledge_base=self._kb is not None,
            recipes=RecipeStatus(
                total=len(recipes),
                enabled=sum(recipe.enabled for recipe in recipes),
                locked_enabled=sum(r.enabled and r.unlocked is False for r in recipes),
                customized=enabled_recipe_ids(recipes) is not None,
            ),
            tech=TechStatus(
                known=unlocked is not None,
                tier=current_tier(progress) if unlocked is not None else None,
                milestones_unlocked=sum(tier.unlocked for tier in progress),
                milestones_total=sum(tier.total for tier in progress),
            ),
            map=MapStatus(sites=len(saves.sites), resource_nodes=len(self._resource_nodes)),
            save=SaveStatus(
                mode=saves.selection().mode,
                file=saves.path.name if state and saves.path else None,
                path=str(saves.path) if state and saves.path else None,
                session=(state.header.session_name or None) if state else None,
                buildings=len(state.placements) if state else 0,
                machines=sum(node.machine_count for node in state.graph.nodes) if state else 0.0,
                recipes_running=len(state.graph.nodes) if state else 0,
                saved_at=saves.saved_at if state else None,
                loading=saves.loading,
                error=saves.error,
            ),
            server=ServerStatus(
                configured=server is not None,
                source=self._server.source(),
                host=server.host if server else None,
                port=server.port if server else None,
                has_token=bool(server and server.token),
                phase=game.phase if game else None,
                tech_tier=game.tech_tier if game else None,
                session=game.session_name if game else None,
                error=self._server.error,
            ),
            context_window_warning=self._warning,
        )

    def _refresh_warning(self) -> bool:
        if self._context_warning is None:
            return False
        now = self._clock()
        asked_at = self._warning_asked_at
        if asked_at is not None and now - asked_at < WARNING_TTL_SECONDS:
            return False
        self._warning_asked_at = now
        warning = self._context_warning()
        changed = warning != self._warning
        self._warning = warning
        return changed

    # --- The map and the technologies ------------------------------------------------------------

    def map_overview(self) -> MapOverview:
        """The player's factories -- what each takes in, puts out and is fed by -- and every
        resource node, taken or free."""
        with self._lock:
            self._refreshed(False)
            context = self._context
        return map_overview(context)

    def tech_overview(self) -> TechOverview:
        """The HUB's milestones tier by tier, the MAM's trees and the hard drives' alternates,
        with what the save has unlocked of them."""
        with self._lock:
            self._refreshed(False)
            state = self._saves.state
        return tech_overview(self._kb, state.unlocked_technology_ids if state else None)

    # --- Recipes ---------------------------------------------------------------------------------

    def recipe_states(self) -> tuple[RecipeState, ...]:
        """Every recipe: whether the save has it unlocked, and whether plans may use it."""
        with self._lock:
            self._refreshed(False)
            return self._recipe_states()

    def set_recipes(self, changes: Mapping[str, bool]) -> None:
        """Lets plans use the recipes `changes` maps to `True`, and not the ones it maps to
        `False` -- for the save's session, other sessions keeping their own pick. Raises
        `ValueError` for an id no recipe has, changing nothing."""
        with self._lock:
            self._refreshed(False)  # the session whose pick it is: the save's in use right now
            choice = changed_choice(self._recipe_choice(), self._recipe_states(), changes)
            store_choice(self._preferences, self._saves.session_name, choice)
            self._sources = None

    def reset_recipes(self) -> None:
        """Back to the default for the save's session: every recipe it has unlocked."""
        with self._lock:
            self._refreshed(False)
            store_choice(self._preferences, self._saves.session_name, RecipeChoice())
            self._sources = None

    def _recipe_states(self) -> tuple[RecipeState, ...]:
        state = self._saves.state
        unlocked = state.unlocked_technology_ids if state else None
        return recipe_states(self._kb, unlocked, self._recipe_choice())

    def _recipe_choice(self) -> RecipeChoice:
        return stored_choice(self._preferences, self._saves.session_name)

    # --- The save --------------------------------------------------------------------------------

    def list_saves(self) -> tuple[SaveFileInfo, ...]:
        """Every save under the game's `SaveGames` folder, newest first."""
        return self._saves.list_saves()

    def save_selection(self) -> SaveSelection:
        with self._lock:
            return self._saves.selection()

    def select_save(self, selection: SaveSelection | None) -> None:
        """Reads the save `selection` picks from now on; `None` goes back to `.env`'s folder. The
        save itself is read on the next look -- in the background, with `background`."""
        self._saves.select(selection)
        with self._lock:
            self._sources = None

    def choose_save(self, mode: str, path: str | None) -> None:
        """Picks the listed save at `path` in `mode` (see `SaveWatcher.selection_for`). Raises
        `ValueError` for a path `list_saves` doesn't list."""
        self.select_save(self._saves.selection_for(mode, path))

    # --- The dedicated server --------------------------------------------------------------------

    def current_server(self) -> ServerConfig | None:
        """The server asked: the one the page set, else the one `.env` names."""
        return self._server.config()

    def check_server(self, host: str, port: int, token: str | None = None) -> ServerCheck:
        """Whether the server at `host`:`port` answers, and takes `token` -- or, without one, the
        token in use. Nothing changes: the server asked stays the one it was."""
        return self._server.check(host, port, token)

    def connect_server(
        self, host: str, port: int, *, token: str | None = None, password: str | None = None
    ) -> str | None:
        """Asks the server at `host`:`port` from now on (see `configure_server`), logging in with
        `password` if given (see `ServerLink.connection`). Raises `ValueError` when the login
        fails, changing nothing; returns a note for the player when there's something they
        should know."""
        server, note = self._server.connection(host, port, token=token, password=password)
        self.configure_server(server)
        return note

    def configure_server(self, server: ServerConfig | None) -> None:
        """Asks `server` from now on -- straight away -- instead of what `.env` names; `None`
        disconnects, leaving the save alone to say what's built."""
        self._server.store(server)
        result = self._server.ask(server) if server is not None else None
        with self._lock:
            self._server.answered(server, result)
            self._sources = None
