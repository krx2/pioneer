"""Bonus confidence check against real, committed save files (tests/save_parser/fixtures/) — not
the suite that decides whether this module is "done" (test_header.py / test_chunks.py /
test_loader.py, all hand-built fixtures, are). See header.py's module docstring: these are the
actual saves that validated the header field layout byte-for-byte in the first place."""

from pathlib import Path

import pytest

from pioneer.save_parser.loader import load_body_from_file

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

_SAVE_FILES = [
    pytest.param("stal_mielec.sav", id="stal_mielec"),
    pytest.param("wielka_polska_niesmiertelna.sav", id="wielka_polska_niesmiertelna"),
    pytest.param("alfa.sav", id="alfa"),
    pytest.param("tak.sav", id="tak"),
]


@pytest.mark.parametrize("filename", _SAVE_FILES)
def test_header_parses_to_sane_values(filename: str) -> None:
    header, _ = load_body_from_file(_FIXTURES_DIR / filename)

    assert header.save_header_version >= 10
    assert header.map_name == "Persistent_Level"
    assert header.play_duration_seconds > 0
    assert header.session_name != ""


@pytest.mark.parametrize("filename", _SAVE_FILES)
def test_full_chunk_stream_decompresses_and_self_validates(filename: str) -> None:
    # load_body_from_file already runs the TotalSize integrity check internally (raises on
    # mismatch) -- reaching this assertion at all is most of the proof. See loader.py.
    _, body = load_body_from_file(_FIXTURES_DIR / filename)

    assert len(body) > 8
