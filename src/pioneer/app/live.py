"""`LiveContext`: the Orchestrator's context, kept as current as its sources, and the controls the
web UI's header panels change those sources with.

The knowledge base and the node data don't change while the assistant runs, so they're taken
once. The save is reread whenever a different file, or a newer write of the same one, is the one
to read — which one that is, the player picks (`select_save`; see `preferences.SaveMode`), and
`.env`'s folder decides until they do. The dedicated server is asked again once its last answer
is `server_ttl_seconds` old, and at once when it's configured anew (`configure_server`). Which
recipes plans may use is the player's pick for the save's session (`set_recipes`), kept as the
difference from the default -- every recipe the save has unlocked -- so that one unlocked later in
the game comes in ticked on its own.

A save that fails to parse -- one still being written, or from a game version the parser doesn't
handle -- leaves the last good one in use, and isn't retried until it changes on disk. With
`background`, a save is read on a thread of its own: a real one takes seconds, and the page's
requests shouldn't wait on it. The last good save stays in use meanwhile, and the status says
what's being read. Thread-safe: the web UI answers questions concurrently.
"""

from __future__ import annotations

import re
import struct
import sys
import threading
import time
import zlib
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from pioneer.app.loading import (
    ServerCheck,
    build_context,
    check_server,
    query_game_state,
    server_login,
)
from pioneer.app.preferences import (
    RECIPES,
    SAVE,
    SERVER,
    PreferencesStore,
    RecipeChoice,
    SaveSelection,
    ServerConfig,
)
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
from pioneer.contracts import FactorySite, GameState, ResourceNode, Technology
from pioneer.knowledge_base import (
    KnowledgeBase,
    current_tier,
    easiest_unlock,
    milestone_progress,
    recipe_is_unlocked,
    research_trees,
)
from pioneer.location_advisor import claimed_node_ids, find_factory_sites, site_feeds
from pioneer.orchestrator import OrchestratorContext, site_balance
from pioneer.save_parser import (
    SaveFileInfo,
    SaveHeader,
    SaveState,
    discover_saves,
    find_latest_save,
    load_save_state,
    read_save_header,
    saves_in,
)
from pioneer.server_client import PostJson, ServerUnavailable, post_json

SERVER_STATE_TTL_SECONDS = 60.0
"""How long the dedicated server's answer is trusted before it's asked again."""
WARNING_TTL_SECONDS = 30.0
"""How long the model's context window check is trusted: it's a request to the model server."""
_KEPT_HEADERS = 2000
_SPARE_EPSILON = 0.01
"""Per minute: less than this to spare is rounding, not a product."""

_FileKey = tuple[Path, int, int]
"""A save as of one write: its path, modification time and size."""
_Loaded = tuple[SaveState, tuple[FactorySite, ...]]


def _main_product(
    site: FactorySite, balance: Mapping[str, float], context: OrchestratorContext, raw: set[str]
) -> str | None:
    """What `site` is there for: of what it makes and has to spare, the end products -- ones none
    of its own recipes takes in -- before the rest, and of those the item the most of its
    machines make. With nothing to spare, what the most of its machines make."""
    recipes = {recipe.recipe_id: recipe for recipe in context.recipes}
    makers: dict[str, int] = {}
    consumed: set[str] = set()
    for placement in site.placements:
        recipe = recipes.get(placement.recipe_id or "")
        if recipe is None:
            continue
        for output in recipe.outputs:
            makers[output.item_id] = makers.get(output.item_id, 0) + 1
        consumed.update(ingredient.item_id for ingredient in recipe.inputs)
    spare = [
        item
        for item, rate in balance.items()
        if rate > _SPARE_EPSILON and item in makers and item not in raw
    ]
    candidates = [item for item in spare if item not in consumed] or spare or list(makers)
    return max(candidates, key=lambda item: makers[item], default=None)


def _by_steps(technology: Technology) -> tuple[tuple[int, ...], str]:
    """Milestones in the HUB's order: `Schematic_3-2_C` after `_3-1_`."""
    return tuple(int(n) for n in re.findall(r"\d+", technology.technology_id)), technology.name


def _enabled_recipe_ids(recipes: tuple[RecipeState, ...]) -> frozenset[str] | None:
    """The recipes plans may use, for the Orchestrator -- `None` while that's just the default,
    which it then falls back from (see `orchestrator.planning_tools._plan`)."""
    if all(recipe.enabled == recipe.default for recipe in recipes):
        return None
    return frozenset(recipe.recipe.recipe_id for recipe in recipes if recipe.enabled)


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
        query_server: Callable[[ServerConfig], GameState | ServerUnavailable] | None = None,
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
        self._default_save_directory = save_directory
        self._save_root = save_root
        self._env_server = server
        self._preferences = preferences if preferences is not None else PreferencesStore(None)
        self._load_save = load_save
        self._read_header = read_header
        self._post = post
        self._query_server = query_server or (lambda server: query_game_state(server, post))
        self._server_ttl_seconds = server_ttl_seconds
        self._clock = clock
        self._background = background
        self._context_warning = context_warning
        self._lock = threading.Lock()
        self._loaded = threading.Condition(self._lock)
        """Notified whenever a background read ends."""
        self._headers_lock = threading.Lock()
        self._headers: dict[_FileKey, SaveHeader] = {}

        self._save_key: _FileKey | None = None
        """The save as last read -- or tried, if that failed."""
        self._loading: _FileKey | None = None
        """The save being read in the background."""
        self._state: SaveState | None = None
        self._state_path: Path | None = None
        self._state_saved_at: float | None = None
        self._sites: tuple[FactorySite, ...] = ()
        self._save_error: str | None = None

        self._game_state: GameState | None = None
        self._server_error: str | None = None
        self._server_asked_at: float | None = None
        self._server_asked_for: ServerConfig | None = None

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
        changed = self._refresh_save()
        changed = self._refresh_server() or changed
        changed = self._refresh_warning() or changed
        if wait and self._loading is not None:
            self._loaded.wait_for(lambda: self._loading is None)
        if changed or self._sources is None:
            self._rebuild()
        assert self._sources is not None
        return self._sources

    def _rebuild(self) -> None:
        state = self._state
        recipes = self._recipe_states()
        self._context = replace(
            self._base,
            existing_graph=state.graph if state else None,
            existing_placements=state.placements if state else (),
            existing_links=state.links if state else (),
            factory_sites=self._sites,
            unlocked_technology_ids=state.unlocked_technology_ids if state else None,
            game_state=self._game_state,
            enabled_recipe_ids=_enabled_recipe_ids(recipes),
        )
        self._sources = self._describe(recipes)

    def _describe(self, recipes: tuple[RecipeState, ...]) -> Sources:
        state = self._state
        unlocked = state.unlocked_technology_ids if state else None
        technologies = self._kb.technologies if self._kb else ()
        progress = milestone_progress(technologies, unlocked or ())
        server = self._server_config()
        game = self._game_state
        return Sources(
            knowledge_base=self._kb is not None,
            recipes=RecipeStatus(
                total=len(recipes),
                enabled=sum(recipe.enabled for recipe in recipes),
                locked_enabled=sum(r.enabled and r.unlocked is False for r in recipes),
                customized=_enabled_recipe_ids(recipes) is not None,
            ),
            tech=TechStatus(
                known=unlocked is not None,
                tier=current_tier(progress) if unlocked is not None else None,
                milestones_unlocked=sum(tier.unlocked for tier in progress),
                milestones_total=sum(tier.total for tier in progress),
            ),
            map=MapStatus(sites=len(self._sites), resource_nodes=len(self._resource_nodes)),
            save=SaveStatus(
                mode=self._save_selection().mode,
                file=self._state_path.name if state and self._state_path else None,
                path=str(self._state_path) if state and self._state_path else None,
                session=(state.header.session_name or None) if state else None,
                buildings=len(state.placements) if state else 0,
                machines=sum(node.machine_count for node in state.graph.nodes) if state else 0.0,
                recipes_running=len(state.graph.nodes) if state else 0,
                saved_at=self._state_saved_at if state else None,
                loading=self._loading[0].name if self._loading else None,
                error=self._save_error,
            ),
            server=ServerStatus(
                configured=server is not None,
                source="page" if SERVER in self._preferences else "env" if server else None,
                host=server.host if server else None,
                port=server.port if server else None,
                has_token=bool(server and server.token),
                phase=game.phase if game else None,
                tech_tier=game.tech_tier if game else None,
                session=game.session_name if game else None,
                error=self._server_error,
            ),
            context_window_warning=self._warning,
        )

    # --- The map ---------------------------------------------------------------------------------

    def map_overview(self) -> MapOverview:
        """The player's factories -- what each takes in, puts out and is fed by -- and every
        resource node, taken or free."""
        with self._lock:
            self._refreshed(False)
            context = self._context
        feeds = site_feeds(context.factory_sites, context.existing_placements)
        raw = {item.item_id for item in context.items if item.is_raw_resource}
        sites = []
        for site in context.factory_sites:
            balance = site_balance(site, context)
            sites.append(
                SiteOverview(
                    site=site,
                    main_product=_main_product(site, balance, context, raw),
                    balance=balance,
                    feeds=feeds.get(site.site_id, ()),
                )
            )
        return MapOverview(
            sites=tuple(sites),
            nodes=context.resource_nodes,
            occupied=claimed_node_ids(context.resource_nodes, context.existing_placements),
        )

    # --- Technologies ----------------------------------------------------------------------------

    def tech_overview(self) -> TechOverview:
        """The HUB's milestones tier by tier, the MAM's trees and the hard drives' alternates,
        with what the save has unlocked of them."""
        with self._lock:
            self._refreshed(False)
            unlocked = self._state.unlocked_technology_ids if self._state else None
        technologies = self._kb.technologies if self._kb else ()
        hub = [t for t in technologies if t.kind in ("tutorial", "milestone")]
        sections = [
            TechSection("HUB", f"Tier {tier}", tuple(sorted(at_tier, key=_by_steps)))
            for tier in sorted({t.tier for t in hub})
            if (at_tier := [t for t in hub if t.tier == tier])
        ]
        sections += [TechSection("MAM", name, tree) for name, tree in research_trees(technologies)]
        drives = sorted(
            (t for t in technologies if t.kind in ("alternate", "harddrive")),
            key=lambda technology: (technology.tier, technology.name),
        )
        if drives:
            sections.append(TechSection("Hard drives", "Alternate recipes", tuple(drives)))
        unlocks: dict[str, list[str]] = {}
        for recipe in self._kb.recipes if self._kb else ():
            for technology_id in recipe.unlockable_by:
                unlocks.setdefault(technology_id, []).append(recipe.recipe_id)
        return TechOverview(
            unlocked=unlocked,
            tiers=milestone_progress(technologies, unlocked or ()),
            sections=tuple(sections),
            unlocks={technology: tuple(recipes) for technology, recipes in unlocks.items()},
        )

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
            recipes = {state.recipe.recipe_id: state for state in self._recipe_states()}
            unknown = sorted(set(changes) - set(recipes))
            if unknown:
                raise ValueError(f"no recipe {unknown[0]!r}")
            choice = self._recipe_choice()
            enabled, disabled = set(choice.enabled), set(choice.disabled)
            for recipe_id, on in changes.items():
                enabled.discard(recipe_id)
                disabled.discard(recipe_id)
                default = recipes[recipe_id].default
                if on and not default:
                    enabled.add(recipe_id)
                elif not on and default:
                    disabled.add(recipe_id)
            self._store_recipe_choice(RecipeChoice(frozenset(enabled), frozenset(disabled)))
            self._sources = None

    def reset_recipes(self) -> None:
        """Back to the default for the save's session: every recipe it has unlocked."""
        with self._lock:
            self._refreshed(False)
            self._store_recipe_choice(RecipeChoice())
            self._sources = None

    def _recipe_states(self) -> tuple[RecipeState, ...]:
        if self._kb is None:
            return ()
        unlocked = self._state.unlocked_technology_ids if self._state else None
        choice = self._recipe_choice()
        states = []
        for recipe in self._kb.recipes:
            is_unlocked = recipe_is_unlocked(recipe, unlocked)
            default = is_unlocked is not False
            states.append(
                RecipeState(
                    recipe=recipe,
                    unlocked=is_unlocked,
                    enabled=recipe.recipe_id in choice.enabled
                    or (default and recipe.recipe_id not in choice.disabled),
                    default=default,
                    unlock_with=easiest_unlock(recipe.unlockable_by, self._kb.technologies)
                    if is_unlocked is False
                    else None,
                )
            )
        return tuple(states)

    def _session_key(self) -> str:
        """Whose pick of recipes applies: the save's session's -- "" with no save loaded."""
        return self._state.header.session_name if self._state else ""

    def _recipe_choice(self) -> RecipeChoice:
        stored = self._preferences.get(RECIPES)
        picked = stored.get(self._session_key()) if isinstance(stored, dict) else None
        return RecipeChoice.from_json(picked) or RecipeChoice()

    def _store_recipe_choice(self, choice: RecipeChoice) -> None:
        stored = self._preferences.get(RECIPES)
        sessions = stored if isinstance(stored, dict) else {}
        if choice.enabled or choice.disabled:
            sessions[self._session_key()] = choice.to_json()
        else:
            sessions.pop(self._session_key(), None)
        if sessions:
            self._preferences.put(RECIPES, sessions)
        else:
            self._preferences.remove(RECIPES)

    # --- The save --------------------------------------------------------------------------------

    def list_saves(self) -> tuple[SaveFileInfo, ...]:
        """Every save under the game's `SaveGames` folder, newest first."""
        if not self._save_root:
            return ()
        return discover_saves(self._save_root, read_header=self._cached_header)

    def save_selection(self) -> SaveSelection:
        with self._lock:
            return self._save_selection()

    def select_save(self, selection: SaveSelection | None) -> None:
        """Reads the save `selection` picks from now on; `None` goes back to `.env`'s folder. The
        save itself is read on the next look -- in the background, with `background`."""
        if selection is None:
            self._preferences.remove(SAVE)
        else:
            self._preferences.put(SAVE, selection.to_json())
        with self._lock:
            self._sources = None

    def choose_save(self, mode: str, path: str | None) -> None:
        """Picks the listed save at `path` (as `list_saves` gives it) in `mode`: it, the newest in
        its folder, or the newest of its session. No `path`: back to `.env`'s folder. Raises
        `ValueError` for a path `list_saves` doesn't list -- the page only ever names one it was
        given, and a request naming any other file is refused."""
        if path is None:
            if mode != "auto":
                raise ValueError(f"mode {mode!r} needs a save")
            self.select_save(None)
            return
        save = next((save for save in self.list_saves() if str(save.path) == path), None)
        if save is None:
            raise ValueError("not a listed save")
        if mode == "auto":
            selection = SaveSelection("auto", directory=str(save.folder))
        elif mode == "session":
            selection = SaveSelection(
                "session", directory=str(save.folder), session=save.session_name
            )
        elif mode == "file":
            selection = SaveSelection("file", path=str(save.path))
        else:
            raise ValueError(f"unknown mode {mode!r}")
        self.select_save(selection)

    def _save_selection(self) -> SaveSelection:
        return SaveSelection.from_json(self._preferences.get(SAVE)) or SaveSelection()

    def _refresh_save(self) -> bool:
        path, problem = self._save_to_read()
        if path is None:
            before = (self._state, self._save_key, self._loading, self._save_error)
            self._save_key = self._loading = self._state = self._state_path = None
            self._state_saved_at = None
            self._sites, self._save_error = (), problem
            self._loaded.notify_all()
            return before != (None, None, None, problem)
        try:
            stat = path.stat()
        except OSError:
            return False  # gone between finding and looking at it: try again next time
        key = (path, stat.st_mtime_ns, stat.st_size)
        if key == self._save_key:
            if self._loading is None:
                return False
            self._loading = None  # back to the save in use: what's being read is no longer wanted
            self._loaded.notify_all()
            return True
        if key == self._loading:
            return False
        if self._background:
            self._loading = key
            threading.Thread(
                target=self._read_in_background, args=(key,), name="save reader", daemon=True
            ).start()
            return True
        self._apply(key, self._read(path))
        return True

    def _save_to_read(self) -> tuple[Path | None, str | None]:
        """The save the selection picks right now, or why there's none to read."""
        selection = self._save_selection()
        if selection.mode == "file" and selection.path:
            path = Path(selection.path)
            return (path, None) if path.is_file() else (None, f"{path.name} is gone")
        if selection.mode == "session" and selection.directory:
            saves = saves_in(selection.directory, read_header=self._cached_header)
            found = next((s for s in saves if s.session_name == selection.session), None)
            if found is None:
                return None, f"no save of session {selection.session!r} left"
            return found.path, None
        directory = selection.directory or self._default_save_directory
        return (find_latest_save(directory) if directory else None), None

    def _read(self, path: Path) -> _Loaded | str:
        """The save at `path` and the factories in it, or why it couldn't be read."""
        try:
            state = self._load_save(path)
        except (OSError, ValueError, struct.error, zlib.error) as error:
            print(f"warning: could not parse {path.name}: {error}", file=sys.stderr)
            return f"could not read {path.name} ({error})"
        return state, find_factory_sites(state.placements, state.links)

    def _read_in_background(self, key: _FileKey) -> None:
        try:
            outcome = self._read(key[0])
        except Exception as error:  # a parser bug mustn't leave the save "loading" for good
            outcome = f"could not read {key[0].name} ({type(error).__name__}: {error})"
        with self._lock:
            if self._loading != key:
                return  # another save was picked meanwhile
            self._loading = None
            self._apply(key, outcome)
            self._loaded.notify_all()

    def _apply(self, key: _FileKey, outcome: _Loaded | str) -> None:
        self._save_key = key
        if isinstance(outcome, str):
            self._save_error = outcome  # the last good save stays in use
        else:
            self._state, self._sites = outcome
            self._state_path = key[0]
            self._state_saved_at = key[1] / 1e9
            self._save_error = None
        self._sources = None

    def _cached_header(self, path: Path) -> SaveHeader:
        """A save's header, read once per write of the file."""
        stat = path.stat()
        key = (path, stat.st_mtime_ns, stat.st_size)
        with self._headers_lock:
            header = self._headers.get(key)
        if header is None:
            header = self._read_header(path)
            with self._headers_lock:
                if len(self._headers) >= _KEPT_HEADERS:
                    self._headers.clear()
                self._headers[key] = header
        return header

    # --- The dedicated server --------------------------------------------------------------------

    def current_server(self) -> ServerConfig | None:
        """The server asked: the one the page set, else the one `.env` names."""
        return self._server_config()

    def check_server(self, host: str, port: int, token: str | None = None) -> ServerCheck:
        """Whether the server at `host`:`port` answers, and takes `token` -- or, without one, the
        token in use. Nothing changes: the server asked stays the one it was."""
        return check_server(ServerConfig(host, port, token or self._kept_token()), self._post)

    def connect_server(
        self, host: str, port: int, *, token: str | None = None, password: str | None = None
    ) -> str | None:
        """Asks the server at `host`:`port` from now on (see `configure_server`). With
        `password`, its admin password, logs in for a token, which is kept, and the password not
        (see `server_client.log_in`); with neither, the token in use is kept. Raises `ValueError`
        when the login fails, changing nothing; returns a note for the player when there's
        something they should know."""
        server = ServerConfig(host, port, token or self._kept_token())
        note = None
        if password:
            login = server_login(server, password, self._post)
            if isinstance(login, ServerUnavailable):
                raise ValueError(f"could not log in: {login.reason}")
            server = replace(server, token=login.token)
            if not login.lasting:
                note = (
                    "The server didn't hand out a lasting API token, so the login token is kept "
                    "instead -- the server lets those lapse. If it stops answering, log in again, "
                    "or paste a token from server.GenerateAPIToken in the server's console."
                )
        self.configure_server(server)
        return note

    def _kept_token(self) -> str | None:
        server = self._server_config()
        return server.token if server is not None else None

    def configure_server(self, server: ServerConfig | None) -> None:
        """Asks `server` from now on -- straight away -- instead of what `.env` names; `None`
        disconnects, leaving the save alone to say what's built."""
        self._preferences.put(SERVER, server.to_json() if server is not None else None)
        result = self._query_server(server) if server is not None else None
        with self._lock:
            self._server_answered(server, result)
            self._sources = None

    def _server_config(self) -> ServerConfig | None:
        if SERVER in self._preferences:
            return ServerConfig.from_json(self._preferences.get(SERVER))
        return self._env_server

    def _refresh_server(self) -> bool:
        server = self._server_config()
        asked_at = self._server_asked_at if server == self._server_asked_for else None
        if server is not None and asked_at is not None:
            if self._clock() - asked_at < self._server_ttl_seconds:
                return False
        before = (self._game_state, self._server_error, self._server_asked_for)
        self._server_answered(server, self._query_server(server) if server is not None else None)
        return before != (self._game_state, self._server_error, self._server_asked_for)

    def _server_answered(
        self, server: ServerConfig | None, result: GameState | ServerUnavailable | None
    ) -> None:
        self._server_asked_for = server
        self._server_asked_at = self._clock() if server is not None else None
        if isinstance(result, ServerUnavailable):
            self._game_state, self._server_error = None, result.reason
        else:
            self._game_state, self._server_error = result, None

    # --- The model -------------------------------------------------------------------------------

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
