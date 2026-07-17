"""Stage 0 smoke tests: every module package imports cleanly and config loads.

Not a domain test suite — just proof the scaffolding (package layout, config mechanism) works.
"""

import importlib

import pytest

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
]


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)


def test_settings_load_from_env() -> None:
    from pioneer.config import Settings

    settings = Settings.from_env()

    assert settings.llm_base_url is None
    assert settings.llm_model is None
    assert settings.llm_api_key is None
    assert settings.dedicated_server_host is None
    assert settings.dedicated_server_port is None
