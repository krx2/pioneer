"""Tests for the low-level binary cursor, against hand-built byte buffers — no save file
involved."""

import struct

import pytest

from pioneer.save_parser.binary_reader import ByteReader


def test_read_int32_little_endian() -> None:
    reader = ByteReader(struct.pack("<i", -12345))
    assert reader.read_int32() == -12345
    assert reader.remaining() == 0


def test_read_uint32() -> None:
    reader = ByteReader(struct.pack("<I", 0x9E2A83C1))
    assert reader.read_uint32() == 0x9E2A83C1


def test_read_int64() -> None:
    reader = ByteReader(struct.pack("<q", 638207885677380000))
    assert reader.read_int64() == 638207885677380000


def test_read_float() -> None:
    reader = ByteReader(struct.pack("<f", 3.5))
    assert reader.read_float() == pytest.approx(3.5)


def test_read_byte() -> None:
    reader = ByteReader(bytes([0x2A]))
    assert reader.read_byte() == 42


def test_read_bytes_advances_offset() -> None:
    reader = ByteReader(b"hello world")
    assert reader.read_bytes(5) == b"hello"
    assert reader.remaining() == 6


def test_read_fstring_empty_has_no_data() -> None:
    reader = ByteReader(struct.pack("<i", 0))
    assert reader.read_fstring() == ""
    assert reader.remaining() == 0


def test_read_fstring_ascii_positive_length() -> None:
    encoded = b"hello\x00"
    reader = ByteReader(struct.pack("<i", len(encoded)) + encoded)
    assert reader.read_fstring() == "hello"


def test_read_fstring_utf16_negative_length() -> None:
    encoded = "cześć".encode("utf-16-le") + b"\x00\x00"
    char_count = len(encoded) // 2
    reader = ByteReader(struct.pack("<i", -char_count) + encoded)
    assert reader.read_fstring() == "cześć"


def test_multiple_reads_advance_sequentially() -> None:
    data = struct.pack("<ii", 1, 2) + b"\x2a"
    reader = ByteReader(data)
    assert reader.read_int32() == 1
    assert reader.read_int32() == 2
    assert reader.read_byte() == 0x2A
