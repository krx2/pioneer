"""The save the assistant reads: the one the player's selection picks (see `preferences.SaveMode`),
`.env`'s folder deciding until they pick, reread whenever a different file, or a newer write of
the same one, is the one to read. A save that fails to parse leaves the last good one in use and
isn't retried until it changes on disk. With `background`, a save is read on a thread of its own,
since a real one takes seconds."""

from __future__ import annotations

import logging
import struct
import threading
import zlib
from collections.abc import Callable
from pathlib import Path

from pioneer.app.preferences import SAVE, PreferencesStore, SaveSelection
from pioneer.contracts import FactorySite
from pioneer.location_advisor import find_factory_sites
from pioneer.save_parser import (
    SaveFileInfo,
    SaveHeader,
    SaveState,
    discover_saves,
    find_latest_save,
    saves_in,
)

_log = logging.getLogger(__name__)
_KEPT_HEADERS = 2000

_FileKey = tuple[Path, int, int]
"""A save as of one write: its path, modification time and size."""
_Loaded = tuple[SaveState, tuple[FactorySite, ...]]


class SaveWatcher:
    """`refresh`, `wait`, `news` and the state they keep are used under `lock`, which a
    background read takes too to hand its save over."""

    def __init__(
        self,
        lock: threading.Lock,
        preferences: PreferencesStore,
        *,
        save_directory: str | None,
        save_root: str | None,
        load_save: Callable[[Path], SaveState],
        read_header: Callable[[Path], SaveHeader],
        background: bool,
    ) -> None:
        self._lock = lock
        self._loaded = threading.Condition(lock)
        """Notified whenever a background read ends."""
        self._preferences = preferences
        self._default_directory = save_directory
        self._root = save_root
        self._load_save = load_save
        self._read_header = read_header
        self._background = background
        self._headers_lock = threading.Lock()
        self._headers: dict[_FileKey, SaveHeader] = {}

        self.state: SaveState | None = None
        """The save in use: the last that was read whole."""
        self.path: Path | None = None
        self.saved_at: float | None = None
        """When the save in use was written, as a Unix time."""
        self.sites: tuple[FactorySite, ...] = ()
        self.error: str | None = None
        """Why the save that should be in use isn't: unreadable, or gone."""
        self._key: _FileKey | None = None
        """The save as last read -- or tried, if that failed."""
        self._loading: _FileKey | None = None
        """The save being read in the background."""
        self._news = False
        """A background read has ended since `news` was last asked."""

    @property
    def loading(self) -> str | None:
        """The save being read in the background, by file name."""
        return self._loading[0].name if self._loading else None

    @property
    def session_name(self) -> str:
        """The save in use's session -- "" with no save loaded."""
        return self.state.header.session_name if self.state else ""

    # --- Which save ------------------------------------------------------------------------------

    def selection(self) -> SaveSelection:
        return SaveSelection.from_json(self._preferences.get(SAVE)) or SaveSelection()

    def select(self, selection: SaveSelection | None) -> None:
        """Reads the save `selection` picks from now on; `None` goes back to `.env`'s folder."""
        if selection is None:
            self._preferences.remove(SAVE)
        else:
            self._preferences.put(SAVE, selection.to_json())

    def selection_for(self, mode: str, path: str | None) -> SaveSelection | None:
        """What picking the listed save at `path` (as `list_saves` gives it) in `mode` selects:
        it, the newest in its folder, or the newest of its session. No `path`: `None`, back to
        `.env`'s folder. Raises `ValueError` for a path `list_saves` doesn't list -- the page only
        ever names one it was given, and a request naming any other file is refused."""
        if path is None:
            if mode != "auto":
                raise ValueError(f"mode {mode!r} needs a save")
            return None
        save = next((save for save in self.list_saves() if str(save.path) == path), None)
        if save is None:
            raise ValueError("not a listed save")
        if mode == "auto":
            return SaveSelection("auto", directory=str(save.folder))
        if mode == "session":
            return SaveSelection("session", directory=str(save.folder), session=save.session_name)
        if mode == "file":
            return SaveSelection("file", path=str(save.path))
        raise ValueError(f"unknown mode {mode!r}")

    def list_saves(self) -> tuple[SaveFileInfo, ...]:
        """Every save under the game's `SaveGames` folder, newest first."""
        if not self._root:
            return ()
        return discover_saves(self._root, read_header=self._cached_header)

    # --- Reading it ------------------------------------------------------------------------------

    def refresh(self) -> bool:
        """Reads the save to read, if it isn't the one in use; whether that changed anything."""
        path, problem = self._save_to_read()
        if path is None:
            before = (self.state, self._key, self._loading, self.error)
            self._key = self._loading = self.state = self.path = None
            self.saved_at = None
            self.sites, self.error = (), problem
            self._loaded.notify_all()
            return before != (None, None, None, problem)
        try:
            stat = path.stat()
        except OSError:
            return False  # gone between finding and looking at it: try again next time
        key = (path, stat.st_mtime_ns, stat.st_size)
        if key == self._key:
            if self._loading is None:
                return False
            self._loading = None  # back to the save in use: what's being read is no longer wanted
            self._loaded.notify_all()
            return True
        if key == self._loading:
            return False
        if self._background:
            self._loading = key
            threading.Thread(
                target=self._read_in_background, args=(key,), name="save reader", daemon=True
            ).start()
            return True
        self._apply(key, self._read(path))
        return True

    def wait(self) -> None:
        """Until the save being read in the background, if any, is read."""
        if self._loading is not None:
            self._loaded.wait_for(lambda: self._loading is None)

    def news(self) -> bool:
        """Whether a background read has handed a save over since this was last asked."""
        news, self._news = self._news, False
        return news

    def _save_to_read(self) -> tuple[Path | None, str | None]:
        """The save the selection picks right now, or why there's none to read."""
        selection = self.selection()
        if selection.mode == "file" and selection.path:
            path = Path(selection.path)
            return (path, None) if path.is_file() else (None, f"{path.name} is gone")
        if selection.mode == "session" and selection.directory:
            saves = saves_in(selection.directory, read_header=self._cached_header)
            found = next((s for s in saves if s.session_name == selection.session), None)
            if found is None:
                return None, f"no save of session {selection.session!r} left"
            return found.path, None
        directory = selection.directory or self._default_directory
        return (find_latest_save(directory) if directory else None), None

    def _read(self, path: Path) -> _Loaded | str:
        """The save at `path` and the factories in it, or why it couldn't be read."""
        try:
            state = self._load_save(path)
        except (OSError, ValueError, struct.error, zlib.error) as error:
            _log.warning("could not parse %s: %s", path.name, error)
            return f"could not read {path.name} ({error})"
        return state, find_factory_sites(state.placements, state.links)

    def _read_in_background(self, key: _FileKey) -> None:
        try:
            outcome = self._read(key[0])
        except Exception as error:  # a parser bug mustn't leave the save "loading" for good
            outcome = f"could not read {key[0].name} ({type(error).__name__}: {error})"
        with self._lock:
            if self._loading != key:
                return  # another save was picked meanwhile
            self._loading = None
            self._apply(key, outcome)
            self._news = True
            self._loaded.notify_all()

    def _apply(self, key: _FileKey, outcome: _Loaded | str) -> None:
        self._key = key
        if isinstance(outcome, str):
            self.error = outcome  # the last good save stays in use
        else:
            self.state, self.sites = outcome
            self.path = key[0]
            self.saved_at = key[1] / 1e9
            self.error = None

    def _cached_header(self, path: Path) -> SaveHeader:
        """A save's header, read once per write of the file."""
        stat = path.stat()
        key = (path, stat.st_mtime_ns, stat.st_size)
        with self._headers_lock:
            header = self._headers.get(key)
        if header is None:
            header = self._read_header(path)
            with self._headers_lock:
                if len(self._headers) >= _KEPT_HEADERS:
                    self._headers.clear()
                self._headers[key] = header
        return header
