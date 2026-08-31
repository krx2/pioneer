"""Tests for the header+chunks orchestration, against a fully hand-built `.sav`-shaped buffer —
no real save file involved. See test_real_saves.py for the bonus real-file check."""

import struct

import pytest
from tests.save_parser.byte_builders import body_bytes, chunk_bytes, header_bytes

from pioneer.save_parser.loader import load_body_from_bytes


def _fake_save(payload: bytes = b"pretend this is level data" * 20) -> bytes:
    body = body_bytes(payload)
    return header_bytes(save_name="loader_test") + chunk_bytes(body)


def test_load_body_from_bytes_returns_header_and_body() -> None:
    header, body = load_body_from_bytes(_fake_save())

    assert header.save_name == "loader_test"
    assert body[8:] == b"pretend this is level data" * 20


def test_load_body_splits_across_multiple_chunks() -> None:
    payload = b"x" * 500
    body = body_bytes(payload)
    # Split the body across two hand-built chunks, same as a real multi-chunk save.
    midpoint = len(body) // 2
    data = header_bytes() + chunk_bytes(body[:midpoint]) + chunk_bytes(body[midpoint:])

    _, decompressed_body = load_body_from_bytes(data)

    assert decompressed_body == body


def test_corrupted_total_size_raises() -> None:
    body = bytearray(body_bytes(b"some payload"))
    struct.pack_into("<q", body, 0, 999999)  # lie about the declared size
    data = header_bytes() + chunk_bytes(bytes(body))

    with pytest.raises(ValueError, match="mismatch"):
        load_body_from_bytes(data)
