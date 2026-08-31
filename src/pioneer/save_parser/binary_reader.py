"""Low-level binary reading primitives for the `.sav` format.

All numeric types in the format are little-endian (per the reverse-engineered format references —
see `header.py`'s module docstring for links). `ByteReader` is a thin mutable cursor over a
`bytes` buffer; every reader in `save_parser` reads through one instance of it rather than
threading raw offsets by hand.
"""

from __future__ import annotations

import struct


class ByteReader:
    def __init__(self, data: bytes, offset: int = 0) -> None:
        self.data = data
        self.offset = offset

    def remaining(self) -> int:
        return len(self.data) - self.offset

    def read_bytes(self, count: int) -> bytes:
        value = self.data[self.offset : self.offset + count]
        self.offset += count
        return value

    def read_byte(self) -> int:
        value = self.data[self.offset]
        self.offset += 1
        return value

    def read_int32(self) -> int:
        (value,) = struct.unpack_from("<i", self.data, self.offset)
        self.offset += 4
        return value

    def read_uint32(self) -> int:
        (value,) = struct.unpack_from("<I", self.data, self.offset)
        self.offset += 4
        return value

    def read_int64(self) -> int:
        (value,) = struct.unpack_from("<q", self.data, self.offset)
        self.offset += 8
        return value

    def read_float(self) -> float:
        (value,) = struct.unpack_from("<f", self.data, self.offset)
        self.offset += 4
        return value

    def read_floats(self, count: int) -> tuple[float, ...]:
        values = struct.unpack_from(f"<{count}f", self.data, self.offset)
        self.offset += count * 4
        return values

    def read_fstring(self) -> str:
        """UE `FString`: int32 length prefix, then the string data, null-terminated.

        Positive length = ASCII, one byte per char. Negative length = UTF-16LE, two bytes per
        char (magnitude counts UTF-16 code units, including the terminator). Length 0 = empty
        string, no data follows at all.
        """
        length = self.read_int32()
        if length == 0:
            return ""
        if length > 0:
            raw = self.read_bytes(length)
            return raw[:-1].decode("ascii")
        char_count = -length
        raw = self.read_bytes(char_count * 2)
        return raw[:-2].decode("utf-16-le")
