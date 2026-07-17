"""Runtime configuration, loaded from environment variables.

Environment-specific values (LLM endpoint, dedicated server address) live only in environment
variables — optionally via a local `.env` file, which is gitignored. Never hardcode a secret here
or anywhere else. See `.env.example` for the full list of variables.

The LLM is expected to run locally (e.g. Ollama, llama.cpp, vLLM) behind an OpenAI-compatible
endpoint, so `llm_base_url` / `llm_model` are the primary settings. `llm_api_key` is optional and
only needed if the local server is configured to require one — most local setups leave it unset.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    llm_base_url: str | None
    llm_model: str | None
    llm_api_key: str | None
    dedicated_server_host: str | None
    dedicated_server_port: int | None

    @classmethod
    def from_env(cls) -> Settings:
        port = os.environ.get("PIONEER_SERVER_PORT")
        return cls(
            llm_base_url=os.environ.get("PIONEER_LLM_BASE_URL"),
            llm_model=os.environ.get("PIONEER_LLM_MODEL"),
            llm_api_key=os.environ.get("PIONEER_LLM_API_KEY"),
            dedicated_server_host=os.environ.get("PIONEER_SERVER_HOST"),
            dedicated_server_port=int(port) if port else None,
        )


settings = Settings.from_env()
