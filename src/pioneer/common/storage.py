"""Writing the stores' files, and stamping their records."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    """Writes `text` to `path` through a temporary file beside it, so a crash mid-write leaves the
    old file whole. Creates the folder when it's missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def utc_timestamp(timespec: str = "seconds") -> str:
    """Now in UTC, ISO 8601 to `timespec` (see `datetime.isoformat`)."""
    return datetime.now(UTC).isoformat(timespec=timespec)
