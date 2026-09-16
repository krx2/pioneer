"""Stage 0 smoke tests: every module package imports cleanly and config loads.

Not a domain test suite — just proof the scaffolding (package layout, config mechanism) works.
"""

import importlib

import pytest

# Imported at collection time on purpose: `pioneer.config` loads a local `.env` into the process
# environment on first import, and that has to happen *before* `clean_env` clears the variables,
# or it would put them straight back mid-test.
from pioneer.config import Settings, default_save_directory

MODULE_NAMES = [
    "pioneer.contracts",
    "pioneer.knowledge_base",
    "pioneer.resource_db",
    "pioneer.verifier",
    "pioneer.save_parser",
    "pioneer.server_client",
    "pioneer.production_planner",
    "pioneer.expansion_advisor",
    "pioneer.location_advisor",
    "pioneer.anomaly_detector",
    "pioneer.qa_engine",
    "pioneer.chat_presentation",
    "pioneer.graph_presentation",
    "pioneer.map_presentation",
    "pioneer.verification_feedback",
    "pioneer.orchestrator",
    "pioneer.llm_client",
]

_SETTINGS_ENV_VARS = (
    "PIONEER_LLM_BASE_URL",
    "PIONEER_LLM_MODEL",
    "PIONEER_LLM_API_KEY",
    "PIONEER_SERVER_HOST",
    "PIONEER_SERVER_PORT",
    "PIONEER_SERVER_API_TOKEN",
    "PIONEER_SAVE_DIR",
)


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Anyone who has actually run the app has a filled-in `.env`, so these tests clear every
    variable `Settings` reads rather than assuming the environment starts out empty."""
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_settings_default_to_unset(clean_env: pytest.MonkeyPatch) -> None:
    settings = Settings.from_env()

    assert settings.llm_base_url is None
    assert settings.llm_model is None
    assert settings.llm_api_key is None
    assert settings.dedicated_server_host is None
    assert settings.dedicated_server_port is None
    assert settings.dedicated_server_api_token is None
    assert settings.save_directory == default_save_directory()


def test_settings_read_values_from_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("PIONEER_LLM_MODEL", "local-model")
    clean_env.setenv("PIONEER_SERVER_PORT", "7777")
    clean_env.setenv("PIONEER_SAVE_DIR", "D:/saves")

    settings = Settings.from_env()

    assert settings.llm_model == "local-model"
    assert settings.dedicated_server_port == 7777
    assert settings.save_directory == "D:/saves"
