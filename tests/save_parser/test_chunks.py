"""Tests for chunk decompression, against hand-built chunk streams (`byte_builders.chunk_bytes`)
— no real save file involved. See test_real_saves.py for the bonus real-file check (294 real
chunks from an actual save)."""

import pytest
from tests.save_parser.byte_builders import chunk_bytes

from pioneer.save_parser.chunks import decompress_all


def test_single_chunk_round_trips() -> None:
    payload = b"hello satisfactory world" * 100
    data = chunk_bytes(payload)

    assert decompress_all(data, 0) == payload


def test_multiple_chunks_concatenate_in_order() -> None:
    first = b"first chunk payload" * 50
    second = b"second chunk payload" * 50
    data = chunk_bytes(first) + chunk_bytes(second)

    assert decompress_all(data, 0) == first + second


def test_start_offset_skips_preceding_bytes() -> None:
    prefix = b"pretend this is the header"
    payload = b"chunk after a header"
    data = prefix + chunk_bytes(payload)

    assert decompress_all(data, len(prefix)) == payload


def test_wrong_tag_raises() -> None:
    corrupted = b"\x00\x00\x00\x00" + chunk_bytes(b"data")[4:]

    with pytest.raises(ValueError, match="tag"):
        decompress_all(corrupted, 0)


def test_size_mismatch_raises() -> None:
    good = chunk_bytes(b"some payload data")
    # The framing is: tag(4) + archive_header(4) + max_chunk_size(8) + compressor_num(1) +
    # compressed_size_summary(8) + uncompressed_size_summary(8) + compressed_size(8) +
    # uncompressed_size(8) -- so uncompressed_size sits at bytes [41:49]. Corrupt it so it
    # disagrees with what zlib actually produces.
    corrupted = good[:41] + b"\xff\xff\xff\xff\xff\xff\xff\xff" + good[49:]

    with pytest.raises(ValueError, match="expected"):
        decompress_all(corrupted, 0)
