"""Tests for the stores' file writing and timestamps, on a temporary folder."""

import os
from datetime import datetime

import pytest

from pioneer.common import utc_timestamp, write_text_atomic


def test_write_text_atomic_creates_the_folder_and_replaces_the_file(tmp_path) -> None:
    path = tmp_path / "nested" / "settings.json"

    write_text_atomic(path, "first")
    write_text_atomic(path, "second")

    assert path.read_text(encoding="utf-8") == "second"
    assert [p.name for p in path.parent.iterdir()] == ["settings.json"]


def test_a_failed_write_leaves_the_old_file_whole_and_no_temporary_behind(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "conversation.json"
    write_text_atomic(path, "old")

    def crash(source: str, target: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError, match="disk full"):
        write_text_atomic(path, "new")

    assert path.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["conversation.json"]


def test_utc_timestamp_is_iso_8601_in_utc_to_the_asked_precision() -> None:
    stamp = utc_timestamp("milliseconds")

    assert datetime.fromisoformat(stamp).utcoffset().total_seconds() == 0
    assert len(stamp.split("T")[1].split("+")[0]) == len("12:34:56.789")
    assert "." not in utc_timestamp()
