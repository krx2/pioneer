"""Tests for `app.LiveContext`, against save files that are just empty stand-ins, a fake save
loader, a fake server query and a fake clock — no real save parsing, no network."""

import os
from pathlib import Path

from pioneer.app import LiveContext
from pioneer.contracts import (
    Coordinates,
    GameState,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
)
from pioneer.save_parser import SaveHeader, SaveState


def _state(machines: float) -> SaveState:
    node = ProductionNode(
        node_id="save_Recipe_IngotIron_C",
        recipe_id="Recipe_IngotIron_C",
        building_id="Build_SmelterMk1_C",
        machine_count=machines,
        is_existing=True,
        existing_machine_count=machines,
    )
    return SaveState(
        header=SaveHeader(
            save_header_version=14,
            save_version=60,
            build_version=1,
            save_name="",
            map_name="Persistent_Level",
            map_options="",
            session_name="test",
            play_duration_seconds=0,
        ),
        placements=(
            PlacementRecord(building_id="Build_SmelterMk1_C", position=Coordinates(x=0, y=0)),
        ),
        graph=ProductionGraph(nodes=(node,), flows=()),
    )


def _save(directory: Path, name: str, mtime: int) -> Path:
    path = directory / name
    path.write_bytes(b"save")
    os.utime(path, (mtime, mtime))
    return path


class _FakeSaves:
    def __init__(self) -> None:
        self.loaded: list[str] = []
        self.states: dict[str, SaveState | Exception] = {}

    def __call__(self, path: Path) -> SaveState:
        self.loaded.append(path.name)
        state = self.states[path.name]
        if isinstance(state, Exception):
            raise state
        return state


class _FakeServer:
    def __init__(self) -> None:
        self.asked = 0
        self.answer: tuple[GameState | None, str] = (None, "no dedicated server configured")

    def __call__(self) -> tuple[GameState | None, str]:
        self.asked += 1
        return self.answer


def _live(directory: Path | None, saves: _FakeSaves, server: _FakeServer, clock: list[float]):
    return LiveContext(
        None,
        (),
        save_directory=str(directory) if directory is not None else None,
        load_save=saves,
        query_server=server,
        server_ttl_seconds=60,
        clock=lambda: clock[0],
    )


def _machines(context) -> float | None:
    graph = context.existing_graph
    return None if graph is None else graph.nodes[0].machine_count


def test_a_newer_save_is_read_and_an_unchanged_one_is_not(tmp_path) -> None:
    saves, server = _FakeSaves(), _FakeServer()
    saves.states = {"a_autosave_0.sav": _state(3), "a_autosave_1.sav": _state(5)}
    _save(tmp_path, "a_autosave_0.sav", 1_000)
    live = _live(tmp_path, saves, server, [0.0])

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
    saves, server = _FakeSaves(), _FakeServer()
    saves.states = {"a.sav": _state(3)}
    path = _save(tmp_path, "a.sav", 1_000)
    live = _live(tmp_path, saves, server, [0.0])
    live.current()

    saves.states["a.sav"] = _state(4)
    os.utime(path, (2_000, 2_000))

    assert _machines(live.current()[0]) == 4


def test_a_save_that_fails_to_parse_keeps_the_last_good_one_until_it_changes(tmp_path) -> None:
    saves, server = _FakeSaves(), _FakeServer()
    saves.states = {"good.sav": _state(3), "torn.sav": ValueError("chunk tag mismatch")}
    _save(tmp_path, "good.sav", 1_000)
    live = _live(tmp_path, saves, server, [0.0])
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

    saves.states["torn.sav"] = _state(7)
    os.utime(torn, (3_000, 3_000))
    assert _machines(live.current()[0]) == 7


def test_without_a_save_nothing_is_known_about_the_factory(tmp_path) -> None:
    saves, server = _FakeSaves(), _FakeServer()

    for directory in (None, tmp_path / "missing", tmp_path):
        context, summary = _live(directory, saves, server, [0.0]).current()
        assert context.existing_graph is None
        assert context.existing_placements == ()
        assert "no save loaded" in summary
    assert saves.loaded == []


def test_the_server_is_asked_again_only_once_its_answer_is_stale(tmp_path) -> None:
    saves, server = _FakeSaves(), _FakeServer()
    clock = [0.0]
    live = _live(None, saves, server, clock)

    live.current()
    clock[0] = 59.0
    live.current()
    server.answer = (GameState(phase="Phase_2", tech_tier=5), "server: Phase_2, tech tier 5")
    clock[0] = 61.0
    context, summary = live.current()

    assert server.asked == 2
    assert context.game_state == GameState(phase="Phase_2", tech_tier=5)
    assert summary.endswith("server: Phase_2, tech tier 5")
