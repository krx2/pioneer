"""Fixtures for the tests that run on the real game data and the committed saves: each is read once
a run, however many tests use it. What's read is frozen contract data, so sharing it is safe."""

from collections.abc import Callable
from functools import cache
from pathlib import Path

import pytest

from pioneer.knowledge_base import KnowledgeBase, load_from_file
from pioneer.save_parser import SaveState, load_save_state

DOCS_JSON = Path(__file__).parent.parent / "assets" / "en-US.json"
SAVES = Path(__file__).parent / "save_parser" / "fixtures"


@pytest.fixture(scope="session")
def real_kb() -> KnowledgeBase:
    """The knowledge base from the game's own export, `assets/en-US.json`."""
    if not DOCS_JSON.exists():
        pytest.skip("assets/en-US.json not present")
    return load_from_file(DOCS_JSON)


@pytest.fixture(scope="session")
def real_save() -> Callable[[str], SaveState]:
    """`real_save("stal_mielec")`: that committed save, parsed once a run."""
    return cache(lambda name: load_save_state(SAVES / f"{name}.sav"))
