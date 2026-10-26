"""Runtime configuration from environment variables, optionally via a gitignored `.env` file (see
`.env.example`). Never hardcode a secret or a machine-specific path, here or anywhere else.

The LLM runs locally behind an OpenAI-compatible endpoint (`llm_base_url`, `llm_model`);
`llm_api_key` is only for servers configured to require one.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    llm_base_url: str | None
    llm_model: str | None
    llm_api_key: str | None
    dedicated_server_host: str | None
    dedicated_server_port: int | None
    dedicated_server_api_token: str | None
    """Bearer token for the Dedicated Server HTTPS API (see `server_client.access`): an
    application token from the server console's `server.GenerateAPIToken`. The web UI's Server
    panel can get one with the admin password instead, and keeps it in `data/settings.json`."""
    dedicated_server_exe: str | None
    """Where the dedicated server's `FactoryServer.exe` lives — the folder or the file itself.
    Only `pioneer.startup` uses it, to start the server along with everything else."""
    save_directory: str | None
    """Where to look for `.sav` files; the newest one wins (see `save_parser.find_latest_save`).
    Defaults to the game's own dedicated-server save location, so a normal Windows install needs
    no configuration at all. A save picked in the web UI's Save panel takes its place (see
    `app.preferences`)."""
    save_root: str | None = None
    """The game's `SaveGames` folder, whose saves the web UI's Save panel lists: local Steam and
    Epic saves and the ones pulled from a dedicated server, each in a folder of its own. Defaults
    to the parent of the default `save_directory`."""
    llm_judge: bool = False
    """Whether the web UI asks the model for LLM-as-a-judge verdicts on its own answers — one
    extra model call per answer and per suggested build site."""

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            llm_base_url=os.environ.get("PIONEER_LLM_BASE_URL"),
            llm_model=os.environ.get("PIONEER_LLM_MODEL"),
            llm_api_key=os.environ.get("PIONEER_LLM_API_KEY"),
            dedicated_server_host=os.environ.get("PIONEER_SERVER_HOST"),
            dedicated_server_port=_port(os.environ.get("PIONEER_SERVER_PORT")),
            dedicated_server_api_token=os.environ.get("PIONEER_SERVER_API_TOKEN"),
            dedicated_server_exe=os.environ.get("PIONEER_SERVER_EXE") or None,
            save_directory=os.environ.get("PIONEER_SAVE_DIR") or default_save_directory(),
            save_root=os.environ.get("PIONEER_SAVE_ROOT") or default_save_root(),
            llm_judge=_flag(os.environ.get("PIONEER_LLM_JUDGE")),
        )


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _port(value: str | None) -> int | None:
    """A TCP port, or `None` when unset or invalid — warned about rather than raised: this runs at
    import time, and a typo in `.env` shouldn't stop the assistant."""
    if not value or not value.strip():
        return None
    try:
        port = int(value)
    except ValueError:
        port = 0
    if not 0 < port < 65536:
        _log.warning("PIONEER_SERVER_PORT=%r is not a port number -- ignoring it", value)
        return None
    return port


def default_save_directory() -> str | None:
    """`%LOCALAPPDATA%/FactoryGame/Saved/SaveGames/server` — where the game stores saves pulled
    from a dedicated server. `None` off Windows, or if `LOCALAPPDATA` isn't set; set
    `PIONEER_SAVE_DIR` then."""
    root = default_save_root()
    return str(Path(root) / "server") if root else None


def default_save_root() -> str | None:
    """`%LOCALAPPDATA%/FactoryGame/Saved/SaveGames`, the folder every kind of save lives under.
    `None` off Windows, or if `LOCALAPPDATA` isn't set; set `PIONEER_SAVE_ROOT` then."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return str(Path(local_app_data) / "FactoryGame" / "Saved" / "SaveGames")


settings = Settings.from_env()
