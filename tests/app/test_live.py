"""Tests for `app.LiveContext`, against save files that are just empty stand-ins, a fake save
loader, a fake header reader, a fake server query and a fake clock — no real save parsing, no
network."""

import os
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from pioneer.app import LiveContext, PreferencesStore, SaveSelection, ServerConfig
from pioneer.contracts import (
    Coordinates,
    GameState,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
    Recipe,
    Technology,
)
from pioneer.knowledge_base import KnowledgeBase
from pioneer.save_parser import SaveHeader, SaveState
from pioneer.server_client import ServerUnavailable

_SERVER = ServerConfig(host="factory.local", port=7777, token="secret")


def _header(session: str = "test") -> SaveHeader:
    return SaveHeader(
        save_header_version=14,
        save_version=60,
        build_version=1,
        save_name="",
        map_name="Persistent_Level",
        map_options="",
        session_name=session,
        play_duration_seconds=0,
    )


def _state(machines: float, unlocked: frozenset[str] | None = None) -> SaveState:
    node = ProductionNode(
        node_id="save_Recipe_IngotIron_C",
        recipe_id="Recipe_IngotIron_C",
        building_id="Build_SmelterMk1_C",
        machine_count=machines,
        is_existing=True,
        existing_machine_count=machines,
    )
    return SaveState(
        header=_header(),
        placements=(
            PlacementRecord(building_id="Build_SmelterMk1_C", position=Coordinates(x=0, y=0)),
        ),
        graph=ProductionGraph(nodes=(node,), flows=()),
        unlocked_technology_ids=unlocked,
    )


def _save(directory: Path, name: str, mtime: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"save")
    os.utime(path, (mtime, mtime))
    return path


class _FakeSaves:
    def __init__(self) -> None:
        self.loaded: list[str] = []
        self.states: dict[str, SaveState | Exception] = {}
        self.gate: threading.Event | None = None
        """When set, a read waits for it: a save that takes a while."""

    def __call__(self, path: Path) -> SaveState:
        if self.gate is not None:
            assert self.gate.wait(5)
        self.loaded.append(path.name)
        state = self.states[path.name]
        if isinstance(state, Exception):
            raise state
        return state


class _FakeServer:
    def __init__(self) -> None:
        self.asked: list[ServerConfig] = []
        self.answer: GameState | ServerUnavailable = GameState(phase="Phase_1", tech_tier=2)

    def __call__(self, server: ServerConfig) -> GameState | ServerUnavailable:
        self.asked.append(server)
        return self.answer


def _session_of(path: Path) -> SaveHeader:
    """A header naming the session the way the game names its files: `<session>_autosave_0`."""
    return _header(session=path.stem.split("_")[0])


def _live(
    directory: Path | None,
    saves: _FakeSaves,
    query: _FakeServer | None = None,
    clock: list[float] | None = None,
    **options,
) -> LiveContext:
    clock = clock if clock is not None else [0.0]
    return LiveContext(
        options.pop("kb", None),
        (),
        save_directory=str(directory) if directory is not None else None,
        load_save=saves,
        read_header=_session_of,
        query_server=query or _FakeServer(),
        server_ttl_seconds=60,
        clock=lambda: clock[0],
        **options,
    )


def _machines(context) -> float | None:
    graph = context.existing_graph
    return None if graph is None else graph.nodes[0].machine_count


def test_a_newer_save_is_read_and_an_unchanged_one_is_not(tmp_path) -> None:
    saves = _FakeSaves()
    saves.states = {"a_autosave_0.sav": _state(3), "a_autosave_1.sav": _state(5)}
    _save(tmp_path, "a_autosave_0.sav", 1_000)
    live = _live(tmp_path, saves)

    first, summary = live.current()
    again, _ = live.current()
    _save(tmp_path, "a_autosave_1.sav", 2_000)
    newer, newer_summary = live.current()

    assert (_machines(first), _machines(again), _machines(newer)) == (3, 3, 5)
    assert saves.loaded == ["a_autosave_0.sav", "a_autosave_1.sav"]
    assert "save a_autosave_0.sav: 1 buildings, 3 effective machines" in summary
    assert "a_autosave_1.sav" in newer_summary
    assert again is first  # nothing changed: the same context


def test_the_same_file_rewritten_is_read_again(tmp_path) -> None:
    saves = _FakeSaves()
    saves.states = {"a.sav": _state(3)}
    path = _save(tmp_path, "a.sav", 1_000)
    live = _live(tmp_path, saves)
    live.current()

    saves.states["a.sav"] = _state(4)
    os.utime(path, (2_000, 2_000))

    assert _machines(live.current()[0]) == 4


def test_a_save_that_fails_to_parse_keeps_the_last_good_one_until_it_changes(tmp_path) -> None:
    saves = _FakeSaves()
    saves.states = {"good.sav": _state(3), "torn.sav": ValueError("chunk tag mismatch")}
    _save(tmp_path, "good.sav", 1_000)
    live = _live(tmp_path, saves)
    live.current()

    torn = _save(tmp_path, "torn.sav", 2_000)
    context, summary = live.current()
    live.current()

    assert _machines(context) == 3
    assert (
        summary.count("could not read torn.sav (chunk tag mismatch); still using save good.sav")
        == 1
    )
    assert saves.loaded == ["good.sav", "torn.sav"]  # not retried while unchanged
    assert live.sources().save.error == "could not read torn.sav (chunk tag mismatch)"
    assert live.sources().save.file == "good.sav"
    assert live.sources().save.saved_at == 1_000  # what's in use is as of the good one

    saves.states["torn.sav"] = _state(7)
    os.utime(torn, (3_000, 3_000))
    assert _machines(live.current()[0]) == 7
    assert live.sources().save.error is None
    assert live.sources().save.saved_at == 3_000


def test_without_a_save_nothing_is_known_about_the_factory(tmp_path) -> None:
    saves = _FakeSaves()

    for directory in (None, tmp_path / "missing", tmp_path):
        context, summary = _live(directory, saves).current()
        assert context.existing_graph is None
        assert context.existing_placements == ()
        assert "no save loaded" in summary
    assert saves.loaded == []


def test_the_server_is_asked_again_only_once_its_answer_is_stale() -> None:
    saves, server = _FakeSaves(), _FakeServer()
    clock = [0.0]
    live = _live(None, saves, server, clock, server=_SERVER)

    live.current()
    clock[0] = 59.0
    live.current()
    server.answer = GameState(phase="Phase_2", tech_tier=5)
    clock[0] = 61.0
    context, summary = live.current()

    assert server.asked == [_SERVER, _SERVER]
    assert context.game_state == GameState(phase="Phase_2", tech_tier=5)
    assert summary.endswith("server: Phase_2, tech tier 5")


def test_without_a_server_none_is_asked() -> None:
    server = _FakeServer()

    _, summary = _live(None, _FakeSaves(), server).current()

    assert server.asked == []
    assert summary.endswith("no dedicated server configured")


def test_an_unreachable_server_is_reported_in_the_status() -> None:
    server = _FakeServer()
    server.answer = ServerUnavailable(reason="could not reach dedicated server: timed out")
    live = _live(None, _FakeSaves(), server, server=_SERVER)

    context, summary = live.current()
    status = live.sources().server

    assert context.game_state is None
    assert summary.endswith(
        "dedicated server unavailable (could not reach dedicated server: timed out)"
    )
    assert (status.configured, status.host, status.port, status.has_token) == (
        True,
        "factory.local",
        7777,
        True,
    )
    assert status.error == "could not reach dedicated server: timed out"


# --- Reading saves in the background -----------------------------------------------------------


def test_a_save_read_in_the_background_leaves_the_last_one_in_use_meanwhile(tmp_path) -> None:
    saves = _FakeSaves()
    saves.states = {"a_autosave_0.sav": _state(3), "a_autosave_1.sav": _state(5)}
    _save(tmp_path, "a_autosave_0.sav", 1_000)
    live = _live(tmp_path, saves, background=True)
    assert _machines(live.current(wait=True)[0]) == 3

    saves.gate = threading.Event()
    _save(tmp_path, "a_autosave_1.sav", 2_000)
    during, summary = live.current()
    loading = live.sources().save

    assert _machines(during) == 3
    assert summary.count("loading a_autosave_1.sav…; still using save a_autosave_0.sav") == 1
    assert (loading.loading, loading.file) == ("a_autosave_1.sav", "a_autosave_0.sav")

    saves.gate.set()
    after, after_summary = live.current(wait=True)

    assert _machines(after) == 5
    assert "loading" not in after_summary
    assert live.sources().save.loading is None


def test_a_background_read_no_longer_wanted_is_dropped(tmp_path) -> None:
    saves = _FakeSaves()
    saves.states = {"old.sav": _state(3), "new.sav": _state(5)}
    old = _save(tmp_path, "old.sav", 1_000)
    preferences = PreferencesStore(None)
    live = _live(tmp_path, saves, background=True, preferences=preferences)
    live.current(wait=True)

    saves.gate = threading.Event()
    _save(tmp_path, "new.sav", 2_000)
    live.current()  # starts reading new.sav
    live.select_save(SaveSelection("file", path=str(old)))
    back, summary = live.current()
    saves.gate.set()

    assert _machines(back) == 3
    assert "loading" not in summary
    assert _machines(live.current(wait=True)[0]) == 3


def test_a_parser_crash_in_the_background_is_reported_not_left_loading(tmp_path) -> None:
    saves = _FakeSaves()
    saves.states = {"odd.sav": IndexError("index out of range")}
    _save(tmp_path, "odd.sav", 1_000)
    live = _live(tmp_path, saves, background=True)

    _, summary = live.current(wait=True)

    assert "could not read odd.sav (IndexError: index out of range); no save loaded" in summary


# --- Picking the save --------------------------------------------------------------------------


def _save_games(tmp_path: Path) -> tuple[Path, Path]:
    """A `SaveGames` folder with a Steam and an Epic player's saves, two sessions in the first."""
    root = tmp_path / "SaveGames"
    steam, epic = root / "76561198311247541", root / "81890f28c1824d5cbe5263ecc580e937"
    _save(steam, "alfa_autosave_0.sav", 1_000)
    _save(steam, "alfa_manual.sav", 2_000)
    _save(steam, "beta_autosave_0.sav", 3_000)
    _save(epic, "gamma_autosave_0.sav", 4_000)
    _save(root, "ServerSettings.7777.sav", 9_000)  # the game's settings, not a game
    return root, steam


def _states(*names: str) -> _FakeSaves:
    saves = _FakeSaves()
    saves.states = {name: _state(index + 1) for index, name in enumerate(names)}
    return saves


def test_the_saves_under_save_games_are_listed_newest_first(tmp_path) -> None:
    root, _ = _save_games(tmp_path)
    live = _live(None, _FakeSaves(), save_root=str(root))

    listed = [(save.source, save.session_name, save.path.name) for save in live.list_saves()]

    assert listed == [
        ("epic", "gamma", "gamma_autosave_0.sav"),
        ("steam", "beta", "beta_autosave_0.sav"),
        ("steam", "alfa", "alfa_manual.sav"),
        ("steam", "alfa", "alfa_autosave_0.sav"),
    ]


def test_each_mode_reads_the_save_it_picks(tmp_path) -> None:
    root, steam = _save_games(tmp_path)
    names = ("alfa_autosave_0.sav", "alfa_manual.sav", "beta_autosave_0.sav")
    saves = _states(*names, "gamma_autosave_0.sav")
    live = _live(steam, saves, save_root=str(root))

    def in_use() -> str | None:
        live.current()
        return live.sources().save.file

    assert in_use() == "beta_autosave_0.sav"  # .env's folder, newest first
    live.choose_save("file", str(steam / "alfa_autosave_0.sav"))
    assert (in_use(), live.sources().save.mode) == ("alfa_autosave_0.sav", "file")
    live.choose_save("session", str(steam / "alfa_autosave_0.sav"))
    assert in_use() == "alfa_manual.sav"  # the newest of its session
    _save(steam, "alfa_autosave_1.sav", 5_000)
    saves.states["alfa_autosave_1.sav"] = _state(9)
    assert in_use() == "alfa_autosave_1.sav"  # and it follows the session's autosaves
    live.choose_save(
        "auto", str(root / "81890f28c1824d5cbe5263ecc580e937" / "gamma_autosave_0.sav")
    )
    assert in_use() == "gamma_autosave_0.sav"  # the newest in the Epic folder
    live.choose_save("auto", None)
    assert (in_use(), live.save_selection()) == ("alfa_autosave_1.sav", SaveSelection())


def test_only_a_listed_save_can_be_picked(tmp_path) -> None:
    root, steam = _save_games(tmp_path)
    elsewhere = _save(tmp_path / "elsewhere", "secret.sav", 1_000)
    live = _live(steam, _FakeSaves(), save_root=str(root))

    for path in (str(elsewhere), str(root / "ServerSettings.7777.sav"), "../../etc/passwd"):
        with pytest.raises(ValueError, match="not a listed save"):
            live.choose_save("file", path)
    with pytest.raises(ValueError, match="unknown mode"):
        live.choose_save("newest", str(steam / "alfa_manual.sav"))
    with pytest.raises(ValueError, match="needs a save"):
        live.choose_save("file", None)
    assert live.save_selection() == SaveSelection()


def test_a_pinned_save_that_is_gone_is_said_so(tmp_path) -> None:
    root, steam = _save_games(tmp_path)
    saves = _states("alfa_manual.sav")
    live = _live(steam, saves, save_root=str(root))
    live.choose_save("file", str(steam / "alfa_manual.sav"))
    live.current()

    (steam / "alfa_manual.sav").unlink()
    context, summary = live.current()

    assert context.existing_graph is None
    assert "alfa_manual.sav is gone; no save loaded" in summary


def test_the_choice_is_kept_for_the_next_run(tmp_path) -> None:
    root, steam = _save_games(tmp_path)
    settings = tmp_path / "data" / "settings.json"
    first = _live(steam, _FakeSaves(), save_root=str(root), preferences=PreferencesStore(settings))
    first.choose_save("file", str(steam / "alfa_manual.sav"))

    saves = _states("alfa_manual.sav")
    again = _live(steam, saves, save_root=str(root), preferences=PreferencesStore(settings))
    again.current()

    assert saves.loaded == ["alfa_manual.sav"]


# --- Configuring the server --------------------------------------------------------------------


def test_a_server_configured_in_the_page_wins_over_env_and_is_asked_at_once() -> None:
    server = _FakeServer()
    preferences = PreferencesStore(None)
    live = _live(None, _FakeSaves(), server, server=_SERVER, preferences=preferences)
    live.current()
    other = ServerConfig(host="10.0.0.2", port=7778, token="other")

    server.answer = GameState(phase="Phase_3", tech_tier=7)
    live.configure_server(other)
    context, summary = live.current()

    assert server.asked == [_SERVER, other]  # asked when configured, not again right after
    assert context.game_state == GameState(phase="Phase_3", tech_tier=7)
    assert live.sources().server.host == "10.0.0.2"
    assert preferences.get("server") == {"host": "10.0.0.2", "port": 7778, "token": "other"}


def test_disconnecting_leaves_the_save_alone_even_with_a_server_in_env() -> None:
    server = _FakeServer()
    live = _live(None, _FakeSaves(), server, server=_SERVER)
    live.current()

    live.configure_server(None)
    context, summary = live.current()

    assert context.game_state is None
    assert summary.endswith("no dedicated server configured")
    assert live.sources().server.configured is False
    assert len(server.asked) == 1


# --- What it's all built from ------------------------------------------------------------------


def test_the_sources_say_what_the_context_is_built_from(tmp_path) -> None:
    kb = KnowledgeBase(
        recipes=(
            Recipe("Recipe_A_C", "A", (), (), (), unlockable_by=("Schematic_1-1_C",)),
            Recipe("Recipe_B_C", "B", (), (), (), unlockable_by=("Schematic_2-1_C",)),
            Recipe("Recipe_C_C", "C", (), (), ()),
        ),
        buildings=(),
        technologies=(
            Technology("Schematic_1-1_C", "One", 1, kind="milestone"),
            Technology("Schematic_2-1_C", "Two", 2, kind="milestone"),
            Technology("Schematic_2-2_C", "Two more", 2, kind="milestone"),
        ),
    )
    saves = _FakeSaves()
    saves.states = {"a.sav": _state(2.5, unlocked=frozenset({"Schematic_1-1_C"}))}
    _save(tmp_path, "a.sav", 1_000)
    live = _live(tmp_path, saves, kb=kb, context_warning=lambda: "model context only 4096 tokens")

    sources = live.sources()

    assert sources.knowledge_base is True
    assert (sources.recipes.total, sources.recipes.enabled) == (3, 2)
    assert (sources.tech.known, sources.tech.tier) == (True, 1)
    assert (sources.tech.milestones_unlocked, sources.tech.milestones_total) == (1, 3)
    assert (sources.save.file, sources.save.session, sources.save.buildings) == ("a.sav", "test", 1)
    assert sources.save.saved_at == 1_000
    assert (sources.save.machines, sources.save.recipes_running, sources.save.mode) == (
        2.5,
        1,
        "auto",
    )
    assert sources.context_window_warning == "model context only 4096 tokens"
    assert sources.summary() == (
        "3 recipes | no resource node data | save a.sav: 1 buildings, 2.5 effective machines "
        "running 1 recipes | no dedicated server configured | model context only 4096 tokens"
    )


def test_without_a_save_progress_is_unknown_and_every_recipe_usable() -> None:
    kb = KnowledgeBase(
        recipes=(Recipe("Recipe_A_C", "A", (), (), (), unlockable_by=("Schematic_1-1_C",)),),
        buildings=(),
        technologies=(Technology("Schematic_1-1_C", "One", 1, kind="milestone"),),
    )

    sources = _live(None, _FakeSaves(), kb=kb).sources()

    assert (sources.recipes.total, sources.recipes.enabled) == (1, 1)
    assert (sources.tech.known, sources.tech.tier) == (False, None)


# --- Picking recipes ---------------------------------------------------------------------------

_KB = KnowledgeBase(
    recipes=(
        Recipe("Recipe_A_C", "A", (), (), (), unlockable_by=("Schematic_1-1_C",)),
        Recipe("Recipe_B_C", "B", (), (), (), unlockable_by=("Schematic_2-1_C",)),
        Recipe("Recipe_C_C", "C", (), (), ()),
    ),
    buildings=(),
    technologies=(
        Technology("Schematic_1-1_C", "One", 1, kind="milestone"),
        Technology("Schematic_2-1_C", "Two", 2, kind="milestone"),
    ),
)


def _session_save(directory: Path, name: str, session: str, unlocked: set[str]) -> SaveState:
    _save(directory, name, 1_000)
    return replace(_state(1, unlocked=frozenset(unlocked)), header=_header(session))


def _picking(tmp_path: Path, preferences: PreferencesStore | None = None) -> tuple:
    saves = _FakeSaves()
    saves.states = {"a.sav": _session_save(tmp_path, "a.sav", "alfa", {"Schematic_1-1_C"})}
    live = _live(tmp_path, saves, kb=_KB, preferences=preferences or PreferencesStore(None))
    return live, saves


def _enabled(live: LiveContext) -> dict[str, bool]:
    return {state.recipe.recipe_id: state.enabled for state in live.recipe_states()}


def test_by_default_plans_use_the_unlocked_recipes() -> None:
    live, _ = _picking(Path(tempfile.mkdtemp()))

    assert _enabled(live) == {"Recipe_A_C": True, "Recipe_B_C": False, "Recipe_C_C": True}
    assert live.current()[0].enabled_recipe_ids is None  # the default: planning may fall back
    states = {state.recipe.recipe_id: state for state in live.recipe_states()}
    assert states["Recipe_B_C"].unlock_with.technology_id == "Schematic_2-1_C"
    assert states["Recipe_A_C"].unlock_with is None


def test_picked_recipes_are_what_plans_use_and_reset_goes_back(tmp_path) -> None:
    live, _ = _picking(tmp_path)

    live.set_recipes({"Recipe_A_C": False, "Recipe_B_C": True})
    context, _ = live.current()
    picked = live.sources().recipes

    assert context.enabled_recipe_ids == frozenset({"Recipe_B_C", "Recipe_C_C"})
    assert (picked.enabled, picked.locked_enabled, picked.customized) == (2, 1, True)

    live.reset_recipes()
    assert live.current()[0].enabled_recipe_ids is None
    assert live.sources().recipes.customized is False


def test_an_unknown_recipe_changes_nothing(tmp_path) -> None:
    live, _ = _picking(tmp_path)

    with pytest.raises(ValueError, match="no recipe 'Recipe_Z_C'"):
        live.set_recipes({"Recipe_A_C": False, "Recipe_Z_C": True})

    assert _enabled(live)["Recipe_A_C"] is True


def test_the_pick_is_the_difference_so_a_recipe_unlocked_later_comes_in_ticked(tmp_path) -> None:
    preferences = PreferencesStore(tmp_path / "settings.json")
    live, saves = _picking(tmp_path, preferences)
    live.set_recipes({"Recipe_C_C": False})

    assert preferences.get("recipes") == {"alfa": {"enabled": [], "disabled": ["Recipe_C_C"]}}
    later = replace(
        saves.states["a.sav"],
        unlocked_technology_ids=frozenset({"Schematic_1-1_C", "Schematic_2-1_C"}),
    )
    saves.states["a.sav"] = later
    os.utime(tmp_path / "a.sav", (2_000, 2_000))

    assert _enabled(live) == {"Recipe_A_C": True, "Recipe_B_C": True, "Recipe_C_C": False}


def test_each_session_keeps_its_own_pick(tmp_path) -> None:
    preferences = PreferencesStore(tmp_path / "settings.json")
    live, saves = _picking(tmp_path, preferences)
    live.set_recipes({"Recipe_C_C": False})

    saves.states["b.sav"] = _session_save(tmp_path, "b.sav", "beta", {"Schematic_1-1_C"})
    os.utime(tmp_path / "b.sav", (3_000, 3_000))

    assert _enabled(live)["Recipe_C_C"] is True  # beta's pick is its own: the default
    assert live.current()[0].enabled_recipe_ids is None
