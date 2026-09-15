"""Tests for picking the newest save out of a directory, against temporary files — no real save
data involved, only filenames and modification times."""

import os
from pathlib import Path

from pioneer.save_parser.loader import find_latest_save


def _write(path: Path, *, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a real save")
    os.utime(path, (mtime, mtime))
    return path


def test_picks_the_most_recently_modified_save(tmp_path: Path) -> None:
    _write(tmp_path / "session_autosave_0.sav", mtime=1000)
    newest = _write(tmp_path / "session_autosave_1.sav", mtime=3000)
    _write(tmp_path / "session_autosave_2.sav", mtime=2000)

    assert find_latest_save(tmp_path) == newest


def test_highest_autosave_number_does_not_win_on_its_own(tmp_path: Path) -> None:
    """The game rotates autosave slots, so `_2` is often older than `_0` — mtime decides."""
    newest = _write(tmp_path / "session_autosave_0.sav", mtime=5000)
    _write(tmp_path / "session_autosave_2.sav", mtime=1000)

    assert find_latest_save(tmp_path) == newest


def test_searches_subdirectories(tmp_path: Path) -> None:
    _write(tmp_path / "old.sav", mtime=1000)
    nested = _write(tmp_path / "server" / "session" / "new.sav", mtime=9000)

    assert find_latest_save(tmp_path) == nested


def test_ignores_non_save_files(tmp_path: Path) -> None:
    save = _write(tmp_path / "session.sav", mtime=1000)
    _write(tmp_path / "session.sav.bak", mtime=9000)
    _write(tmp_path / "notes.txt", mtime=9000)

    assert find_latest_save(tmp_path) == save


def test_missing_directory_returns_none(tmp_path: Path) -> None:
    assert find_latest_save(tmp_path / "does-not-exist") is None


def test_directory_without_saves_returns_none(tmp_path: Path) -> None:
    _write(tmp_path / "readme.txt", mtime=1000)

    assert find_latest_save(tmp_path) is None
