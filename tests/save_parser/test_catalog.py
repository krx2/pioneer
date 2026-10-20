"""Tests for listing saves, on a `SaveGames` tree built in a temporary folder out of the headers of
the real saves committed under fixtures/ — only as much of each as listing reads."""

import os
from pathlib import Path

import pytest

from pioneer.save_parser import discover_saves, read_save_header, save_source, saves_in

_FIXTURES = Path(__file__).parent / "fixtures"


def _copy(fixture: str, to: Path, *, mtime: int) -> Path:
    to.parent.mkdir(parents=True, exist_ok=True)
    to.write_bytes((_FIXTURES / fixture).read_bytes()[: 64 * 1024])
    os.utime(to, (mtime, mtime))
    return to


def _junk(to: Path, *, mtime: int = 9_000) -> Path:
    to.parent.mkdir(parents=True, exist_ok=True)
    to.write_bytes(b"\x01\x02")
    os.utime(to, (mtime, mtime))
    return to


@pytest.fixture
def save_games(tmp_path: Path) -> Path:
    root = tmp_path / "SaveGames"
    _copy("stal_mielec.sav", root / "76561198311247541" / "stal mielec_autosave_0.sav", mtime=1_000)
    _copy("alfa.sav", root / "81890f28c1824d5cbe5263ecc580e937" / "Alfa_new.sav", mtime=3_000)
    _copy("tak.sav", root / "server" / "tak_autosave_1.sav", mtime=2_000)
    # The game's own settings, a blueprint and a broken file: none of them games.
    _junk(root / "ServerSettings.7777.sav")
    _copy("tak.sav", root / "CurrentSettingsSaveName.sav", mtime=9_000)
    _copy("tak.sav", root / "blueprints" / "tak" / "Hub.sav", mtime=9_000)
    _junk(root / "76561198311247541" / "torn.sav")
    return root


def test_every_game_save_is_listed_newest_first_with_where_it_came_from(save_games) -> None:
    saves = discover_saves(save_games)

    assert [(s.source, s.session_name, s.save_name, s.path.name) for s in saves] == [
        ("epic", "Alfa", "Alfa_new", "Alfa_new.sav"),
        ("server", "tak", "tak_new", "tak_autosave_1.sav"),
        ("steam", "stal mielec", "stal mielec_280726-003607", "stal mielec_autosave_0.sav"),
    ]
    alfa = saves[0]
    assert alfa.folder == save_games / "81890f28c1824d5cbe5263ecc580e937"
    assert (alfa.play_duration_seconds, alfa.modified, alfa.size_bytes) == (45838, 3_000, 65536)


def test_one_folder_lists_its_own_saves(save_games) -> None:
    steam = save_games / "76561198311247541"

    assert [save.path.name for save in saves_in(steam)] == ["stal mielec_autosave_0.sav"]
    assert saves_in(steam)[0].source == "steam"


def test_a_missing_folder_lists_nothing(tmp_path) -> None:
    assert discover_saves(tmp_path / "missing") == ()
    assert saves_in(tmp_path / "missing") == ()


@pytest.mark.parametrize(
    ("folder", "source"),
    [
        ("76561198311247541", "steam"),
        ("81890f28c1824d5cbe5263ecc580e937", "epic"),
        ("server", "server"),
        ("Server", "server"),
        ("7656119831124754", "other"),  # 16 digits
        ("my saves", "other"),
    ],
)
def test_the_source_is_told_from_the_folder_name(folder: str, source: str) -> None:
    assert save_source(folder) == source


def test_a_header_is_read_from_the_start_of_the_file_alone(tmp_path) -> None:
    short = _copy("stal_mielec.sav", tmp_path / "short.sav", mtime=1_000)

    header = read_save_header(short)

    assert (header.session_name, header.play_duration_seconds) == ("stal mielec", 134294)
