"""What the player picked in the web UI's panels — which save to read, the dedicated server, which
recipes planning may use — kept in `data/settings.json`, so the CLI and the web UI share them.

A section this file has wins over `.env`, which is never written. Written atomically. A file that
can't be read, or a section of a shape this version doesn't understand, is ignored with a warning:
a mangled settings file shouldn't stop the assistant, and the next choice made overwrites it.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from pioneer.common import write_text_atomic

_log = logging.getLogger(__name__)

SaveMode = Literal["auto", "session", "file"]
"""How the save to read is picked: the newest in a folder (the game rotates its autosaves through
`_autosave_0/1/2`, so this follows them), the newest of one session, or one file, pinned."""

SAVE = "save"
SERVER = "server"
RECIPES = "recipes"


class PreferencesStore:
    """Sections of JSON, by name. Thread-safe. With no `path`, choices live in memory only."""

    def __init__(self, path: Path | str | None) -> None:
        self._path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._values = self._read()

    def __contains__(self, section: str) -> bool:
        with self._lock:
            return section in self._values

    def get(self, section: str, default: Any = None) -> Any:
        """A copy: changing it changes nothing until it's `put` back."""
        with self._lock:
            return copy.deepcopy(self._values.get(section, default))

    def put(self, section: str, value: Any) -> None:
        with self._lock:
            self._values[section] = copy.deepcopy(value)
            self._write()

    def remove(self, section: str) -> None:
        with self._lock:
            if section in self._values:
                del self._values[section]
                self._write()

    def _read(self) -> dict[str, Any]:
        if self._path is None or not self._path.exists():
            return {}
        try:
            values = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            _log.warning("ignoring %s: %s", self._path, error)
            return {}
        if not isinstance(values, dict):
            _log.warning("ignoring %s: not a JSON object", self._path)
            return {}
        return values

    def _write(self) -> None:
        if self._path is not None:
            write_text_atomic(self._path, json.dumps(self._values, ensure_ascii=False, indent=2))


@dataclass(frozen=True)
class SaveSelection:
    """Which save to read. `auto` without a `directory` is the `.env` / default folder."""

    mode: SaveMode = "auto"
    directory: str | None = None
    """`auto` and `session`: the folder searched."""
    session: str | None = None
    """`session`: the session name, as the save's header has it."""
    path: str | None = None
    """`file`: the save itself."""

    def to_json(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}

    @classmethod
    def from_json(cls, value: Any) -> SaveSelection | None:
        """`None` for anything that isn't a complete selection."""
        if not isinstance(value, dict) or value.get("mode") not in get_args(SaveMode):
            return None
        fields = {key: value.get(key) for key in ("directory", "session", "path")}
        if not all(field is None or isinstance(field, str) for field in fields.values()):
            return None
        selection = cls(mode=value["mode"], **fields)
        complete = {
            "auto": True,
            "session": selection.directory is not None and selection.session is not None,
            "file": selection.path is not None,
        }
        return selection if complete[selection.mode] else None


@dataclass(frozen=True)
class ServerConfig:
    """Where the dedicated server is, and the API token to ask it with."""

    host: str
    port: int
    token: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"host": self.host, "port": self.port, "token": self.token}

    @classmethod
    def from_json(cls, value: Any) -> ServerConfig | None:
        if not isinstance(value, dict):
            return None
        host, port, token = value.get("host"), value.get("port"), value.get("token")
        if not isinstance(host, str) or not host or isinstance(port, bool):
            return None
        if not isinstance(port, int) or not 0 < port < 65536:
            return None
        if token is not None and not isinstance(token, str):
            return None
        return cls(host=host, port=port, token=token or None)


@dataclass(frozen=True)
class RecipeChoice:
    """The player's changes to which recipes planning may use, against the default of "every
    recipe the save has unlocked": kept as the difference, so a recipe unlocked later in the game
    comes in ticked on its own."""

    enabled: frozenset[str] = frozenset()
    """Recipes the player ticked that the game hasn't unlocked yet."""
    disabled: frozenset[str] = frozenset()
    """Unlocked recipes the player unticked."""

    def to_json(self) -> dict[str, list[str]]:
        return {"enabled": sorted(self.enabled), "disabled": sorted(self.disabled)}

    @classmethod
    def from_json(cls, value: Any) -> RecipeChoice | None:
        if not isinstance(value, dict):
            return None
        enabled, disabled = value.get("enabled", []), value.get("disabled", [])
        if not all(isinstance(ids, list) for ids in (enabled, disabled)):
            return None
        if not all(isinstance(i, str) for i in [*enabled, *disabled]):
            return None
        return cls(enabled=frozenset(enabled), disabled=frozenset(disabled))
