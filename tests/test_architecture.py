"""The dependency rule from implementation.md, checked rather than trusted: a module may import
`pioneer.contracts` and nothing else of this project's — that's what keeps each one independently
testable. The exceptions below are the ones the plan names: the Orchestrator *is* the integration,
Stage 15 scoring may call the finished Verifier, the transports implement other modules' Protocols,
and the app, web UI and launcher wire everything together.
"""

import ast
from pathlib import Path

import pytest

_SOURCE_ROOT = Path(__file__).parent.parent / "src" / "pioneer"

_ALLOWED_IMPORTS: dict[str, set[str]] = {
    "orchestrator": {
        "anomaly_detector",
        "expansion_advisor",
        "knowledge_base",
        "location_advisor",
        "production_planner",
        "qa_engine",
        "verification_feedback",
        "verifier",
    },
    # Stage 15 may call the finished Verifier, and it measures an answer against retrieved text
    # with the same notion of a word that retrieval uses.
    "verification_feedback": {"qa_engine", "verifier"},
    "llm_client": {"orchestrator", "qa_engine", "verification_feedback"},
    "app": {
        "config",
        "knowledge_base",
        "llm_client",
        "location_advisor",
        "orchestrator",
        "qa_engine",
        "resource_db",
        "save_parser",
        "server_client",
    },
    "web": {
        "app",
        "chat_presentation",
        "config",
        "graph_presentation",
        "llm_client",
        "map_presentation",
        "orchestrator",
        "verification_feedback",
    },
    "startup": {"app", "config", "web"},
}


def _module_owner(path: Path) -> str:
    """Which module a file belongs to: its package, or its own name for a top-level file."""
    relative = path.relative_to(_SOURCE_ROOT)
    return relative.parts[0] if len(relative.parts) > 1 else relative.stem


def _imported_modules(path: Path) -> set[str]:
    """The `pioneer.<module>` names `path` imports, by module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pioneer"):
            names.add(node.module.split(".")[1] if "." in node.module else "")
        elif isinstance(node, ast.Import):
            names.update(
                alias.name.split(".")[1]
                for alias in node.names
                if alias.name.startswith("pioneer.")
            )
    return names - {""}


@pytest.mark.parametrize(
    "source_file", sorted(_SOURCE_ROOT.rglob("*.py")), ids=lambda path: path.stem
)
def test_a_module_imports_only_contracts_and_what_the_plan_allows(source_file: Path) -> None:
    owner = _module_owner(source_file)
    allowed = {"contracts", owner} | _ALLOWED_IMPORTS.get(owner, set())

    forbidden = _imported_modules(source_file) - allowed

    assert not forbidden, f"{source_file.relative_to(_SOURCE_ROOT)} imports {sorted(forbidden)}"


def test_the_contracts_package_depends_on_nothing_else() -> None:
    for source_file in (_SOURCE_ROOT / "contracts").glob("*.py"):
        assert _imported_modules(source_file) <= {"contracts"}


def test_every_module_is_covered_by_the_rule() -> None:
    """A new module package without tests of its own would slip past the parametrization above."""
    packages = {
        path.parent.name for path in _SOURCE_ROOT.rglob("*.py") if path.name != "__init__.py"
    }
    assert "contracts" in packages
    assert set(_ALLOWED_IMPORTS) <= packages | {"app", "startup"}
