"""Tests for `app.preferences`: the store on a temporary file, and each section's shape."""

import json

import pytest

from pioneer.app import PreferencesStore, RecipeChoice, SaveSelection, ServerConfig


def test_choices_are_written_and_read_back(tmp_path) -> None:
    path = tmp_path / "data" / "settings.json"
    store = PreferencesStore(path)

    store.put("save", {"mode": "file", "path": "C:/saves/a.sav"})
    store.put("server", None)

    again = PreferencesStore(path)
    assert again.get("save") == {"mode": "file", "path": "C:/saves/a.sav"}
    assert "server" in again and again.get("server") is None  # set, to nothing: disconnected
    assert "recipes" not in again
    assert json.loads(path.read_text(encoding="utf-8"))["save"]["mode"] == "file"
    assert [p.name for p in path.parent.iterdir()] == ["settings.json"]  # no temporary left


def test_removing_a_choice_goes_back_to_the_default(tmp_path) -> None:
    path = tmp_path / "settings.json"
    store = PreferencesStore(path)
    store.put("save", {"mode": "auto"})

    store.remove("save")
    store.remove("never set")

    assert "save" not in PreferencesStore(path)


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", "\x00\x01"])
def test_a_mangled_file_is_ignored_and_then_written_over(tmp_path, content: str) -> None:
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")

    with pytest.warns(UserWarning, match="ignoring"):
        store = PreferencesStore(path)
    assert store.get("save") is None

    store.put("save", {"mode": "auto"})
    assert PreferencesStore(path).get("save") == {"mode": "auto"}


def test_without_a_file_choices_live_in_memory() -> None:
    store = PreferencesStore(None)

    store.put("save", {"mode": "auto"})

    assert store.get("save") == {"mode": "auto"}


def test_a_save_selection_round_trips_and_an_incomplete_one_is_refused() -> None:
    selection = SaveSelection("session", directory="C:/SaveGames/123", session="alfa")

    assert SaveSelection.from_json(selection.to_json()) == selection
    assert SaveSelection.from_json({"mode": "auto"}) == SaveSelection()
    for broken in (
        None,
        [],
        {"mode": "newest"},
        {"mode": "file"},
        {"mode": "session", "directory": "C:/x"},
        {"mode": "file", "path": 7},
    ):
        assert SaveSelection.from_json(broken) is None


def test_a_server_config_round_trips_and_a_broken_one_is_refused() -> None:
    server = ServerConfig(host="10.0.0.2", port=7777, token="secret")

    assert ServerConfig.from_json(server.to_json()) == server
    assert ServerConfig.from_json({"host": "h", "port": 7777, "token": ""}).token is None
    for broken in (
        None,
        {"host": "", "port": 7777},
        {"host": "h", "port": "7777"},
        {"host": "h", "port": 0},
        {"host": "h", "port": True},
        {"host": "h", "port": 7777, "token": 5},
    ):
        assert ServerConfig.from_json(broken) is None


def test_a_recipe_choice_round_trips_and_a_broken_one_is_refused() -> None:
    choice = RecipeChoice(enabled=frozenset({"Recipe_B_C"}), disabled=frozenset({"Recipe_A_C"}))

    assert RecipeChoice.from_json(choice.to_json()) == choice
    assert RecipeChoice.from_json({}) == RecipeChoice()
    for broken in (None, {"enabled": "Recipe_A_C"}, {"disabled": [1]}):
        assert RecipeChoice.from_json(broken) is None
