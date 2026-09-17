"""Runtime configuration, loaded from environment variables.

Environment-specific values (LLM endpoint, dedicated server address, save location) live only in
environment variables — optionally via a local `.env` file, which is gitignored. Never hardcode a
secret, or a machine-specific path, here or anywhere else. See `.env.example` for the full list of
variables.

The LLM is expected to run locally (e.g. Ollama, llama.cpp, vLLM) behind an OpenAI-compatible
endpoint, so `llm_base_url` / `llm_model` are the primary settings. `llm_api_key` is optional and
only needed if the local server is configured to require one — most local setups leave it unset.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    llm_base_url: str | None
    llm_model: str | None
    llm_api_key: str | None
    dedicated_server_host: str | None
    dedicated_server_port: int | None
    dedicated_server_api_token: str | None
    """Bearer token for the Dedicated Server HTTPS API (see server_client's module docstring).
    Obtained out-of-band via the server's login functions — not something this project logs into
    on its own yet."""
    save_directory: str | None
    """Where to look for `.sav` files; the newest one wins (see `save_parser.find_latest_save`).
    Defaults to the game's own dedicated-server save location, so a normal Windows install needs
    no configuration at all."""
    llm_judge: bool = False
    """Whether the web UI asks the model for LLM-as-a-judge verdicts on its own answers
    (architecture.md §6) — one extra model call per answer and per suggested build site."""

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            llm_base_url=os.environ.get("PIONEER_LLM_BASE_URL"),
            llm_model=os.environ.get("PIONEER_LLM_MODEL"),
            llm_api_key=os.environ.get("PIONEER_LLM_API_KEY"),
            dedicated_server_host=os.environ.get("PIONEER_SERVER_HOST"),
            dedicated_server_port=_port(os.environ.get("PIONEER_SERVER_PORT")),
            dedicated_server_api_token=os.environ.get("PIONEER_SERVER_API_TOKEN"),
            save_directory=os.environ.get("PIONEER_SAVE_DIR") or default_save_directory(),
            llm_judge=_flag(os.environ.get("PIONEER_LLM_JUDGE")),
        )


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _port(value: str | None) -> int | None:
    """A TCP port, or `None` when unset — or set to something that isn't one, which is warned
    about rather than raised: this runs at import time, and a typo in `.env` shouldn't stop the
    assistant from answering without the server (architecture.md invariant #5)."""
    if not value or not value.strip():
        return None
    try:
        port = int(value)
    except ValueError:
        port = 0
    if not 0 < port < 65536:
        warnings.warn(
            f"PIONEER_SERVER_PORT={value!r} is not a port number -- ignoring it", stacklevel=2
        )
        return None
    return port


def default_save_directory() -> str | None:
    """`%LOCALAPPDATA%/FactoryGame/Saved/SaveGames/server` — where the game itself stores saves
    pulled from a dedicated server (the only play mode this project supports, per architecture.md
    §2). `None` off Windows, or if `LOCALAPPDATA` isn't set; set `PIONEER_SAVE_DIR` then."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return str(Path(local_app_data) / "FactoryGame" / "Saved" / "SaveGames" / "server")


settings = Settings.from_env()
