"""Which saves there are: every game save under the game's `SaveGames` folder, read only as far as
its header, so that listing dozens of them stays quick.

The game keeps each kind of save in a folder of its own under `SaveGames`: local Steam saves under
the player's 17-digit Steam id, local Epic saves under the 32-hex-digit Epic account id, and saves
pulled from a dedicated server under `server`. The files lying directly in `SaveGames`
(`ServerSettings.7777.sav`, `ServerManager_V2.sav`, `CurrentSettingsSaveName.sav`) are the game's
own settings, not games, and `blueprints/` holds blueprints: both are left out, and so is any file
whose header doesn't parse.
"""

from __future__ import annotations

import re
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pioneer.save_parser.header import SaveHeader, parse_header_fields

SaveSource = Literal["steam", "epic", "server", "other"]

_HEADER_BYTES = 64 * 1024
"""Far more than a header takes: its strings are a save, map and session name and the map's
options."""
_STEAM_ID = re.compile(r"\d{17}")
_EPIC_ID = re.compile(r"[0-9a-fA-F]{32}")
_NOT_SAVES = frozenset({"blueprints"})

ReadHeader = Callable[[Path], SaveHeader]


@dataclass(frozen=True)
class SaveFileInfo:
    path: Path
    source: SaveSource
    folder: Path
    """The folder under `SaveGames` it was found in — what `source` was told from."""
    session_name: str
    save_name: str
    play_duration_seconds: int
    modified: float
    """The file's modification time, as a POSIX timestamp."""
    size_bytes: int


def read_save_header(path: Path | str) -> SaveHeader:
    """The header of the save at `path`, from its first few kilobytes."""
    with open(path, "rb") as file:
        return parse_header_fields(file.read(_HEADER_BYTES))


def save_source(folder_name: str) -> SaveSource:
    """Whose saves a folder under `SaveGames` holds, told from its name."""
    if folder_name.lower() == "server":
        return "server"
    if _STEAM_ID.fullmatch(folder_name):
        return "steam"
    if _EPIC_ID.fullmatch(folder_name):
        return "epic"
    return "other"


def discover_saves(
    root: Path | str, *, read_header: ReadHeader = read_save_header
) -> tuple[SaveFileInfo, ...]:
    """Every game save in the folders under `root` (the game's `SaveGames`), newest first. Empty
    when `root` isn't a folder."""
    root = Path(root)
    if not root.is_dir():
        return ()
    found: list[SaveFileInfo] = []
    for folder in root.iterdir():
        if folder.is_dir() and folder.name.lower() not in _NOT_SAVES:
            found.extend(saves_in(folder, read_header=read_header))
    return tuple(sorted(found, key=lambda save: save.modified, reverse=True))


def saves_in(
    folder: Path | str, *, read_header: ReadHeader = read_save_header
) -> tuple[SaveFileInfo, ...]:
    """Every game save in `folder` and below it, newest first."""
    folder = Path(folder)
    if not folder.is_dir():
        return ()
    source = save_source(folder.name)
    found: list[SaveFileInfo] = []
    for path in folder.rglob("*.sav"):
        try:
            stat = path.stat()
            header = read_header(path)
        except (OSError, ValueError, struct.error):
            continue  # gone since it was listed, or not a save this parser can read
        found.append(
            SaveFileInfo(
                path=path,
                source=source,
                folder=folder,
                session_name=header.session_name,
                save_name=header.save_name or path.stem,
                play_duration_seconds=header.play_duration_seconds,
                modified=stat.st_mtime,
                size_bytes=stat.st_size,
            )
        )
    return tuple(sorted(found, key=lambda save: save.modified, reverse=True))
