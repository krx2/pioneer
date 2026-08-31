"""Tests for the save header parser, against hand-built byte buffers (`byte_builders.header_bytes`)
— no real save file involved. See test_real_saves.py for the bonus real-file check."""

from tests.save_parser.byte_builders import PACKAGE_FILE_TAG, header_bytes

from pioneer.save_parser.header import parse_header


def test_parses_all_trusted_fields() -> None:
    data = header_bytes(
        save_header_version=14,
        save_version=60,
        build_version=495413,
        save_name="my_save",
        map_name="Persistent_Level",
        map_options="",
        session_name="my session",
        play_duration_seconds=134294,
    ) + PACKAGE_FILE_TAG.to_bytes(4, "little")

    parsed = parse_header(data)

    assert parsed.header.save_header_version == 14
    assert parsed.header.save_version == 60
    assert parsed.header.build_version == 495413
    assert parsed.header.save_name == "my_save"
    assert parsed.header.map_name == "Persistent_Level"
    assert parsed.header.session_name == "my session"
    assert parsed.header.play_duration_seconds == 134294


def test_body_offset_lands_exactly_on_the_magic_number() -> None:
    header = header_bytes()
    data = header + PACKAGE_FILE_TAG.to_bytes(4, "little") + b"rest of the chunk stream"

    parsed = parse_header(data)

    assert parsed.body_offset == len(header)
    assert data[parsed.body_offset : parsed.body_offset + 4] == PACKAGE_FILE_TAG.to_bytes(
        4, "little"
    )


def test_resync_tolerates_wrong_trailing_metadata_layout() -> None:
    """The whole point of resyncing on the magic number (see header.py's module docstring): no
    matter what garbage sits in the untrusted trailing metadata block, or how long it is, the
    parser still finds the real chunk stream start."""
    short = header_bytes(trailing_metadata=b"\x01\x02\x03")
    long = header_bytes(trailing_metadata=b"\xff" * 40)

    for header in (short, long):
        data = header + PACKAGE_FILE_TAG.to_bytes(4, "little") + b"chunk data..."
        parsed = parse_header(data)
        assert parsed.body_offset == len(header)


def test_save_header_version_below_14_has_no_save_name() -> None:
    data = header_bytes(save_header_version=10, save_name="ignored") + PACKAGE_FILE_TAG.to_bytes(
        4, "little"
    )

    parsed = parse_header(data)

    assert parsed.header.save_name == ""
    assert parsed.header.map_name == "Persistent_Level"


def test_save_header_version_below_3_has_no_play_duration() -> None:
    data = header_bytes(save_header_version=2) + PACKAGE_FILE_TAG.to_bytes(4, "little")

    parsed = parse_header(data)

    assert parsed.header.play_duration_seconds == 0
    assert parsed.header.session_name == ""
