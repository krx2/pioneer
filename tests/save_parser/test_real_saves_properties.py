"""Bonus confidence check against real, committed save files — not the suite that decides whether
properties.py is "done" (test_properties.py, hand-built fixtures, is). Cross-checks every
`mCurrentRecipe` occurrence against the real Knowledge Base loaded from `docs/en-US.json`, per
properties.py's own module docstring claim: every occurrence in both fixtures resolves."""

from pathlib import Path

import pytest

from pioneer.knowledge_base.loader import load_from_file
from pioneer.save_parser.loader import load_body_from_file
from pioneer.save_parser.properties import find_recipe_ids

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_DOCS_JSON = Path(__file__).parent.parent.parent / "docs" / "en-US.json"


@pytest.fixture(scope="module")
def known_recipe_ids() -> frozenset[str]:
    kb = load_from_file(_DOCS_JSON)
    return frozenset(r.recipe_id for r in kb.recipes)


@pytest.mark.parametrize(
    ("filename", "expected_min_occurrences"),
    [
        pytest.param("stal_mielec.sav", 500, id="stal_mielec"),
        pytest.param("wielka_polska_niesmiertelna.sav", 900, id="wielka_polska"),
        pytest.param("alfa.sav", 20, id="alfa"),
        pytest.param("tak.sav", 63, id="tak"),
    ],
)
def test_every_current_recipe_resolves_to_a_real_recipe(
    filename: str, expected_min_occurrences: int, known_recipe_ids: frozenset[str]
) -> None:
    _, body = load_body_from_file(_FIXTURES_DIR / filename)

    recipe_ids = find_recipe_ids(body)

    assert len(recipe_ids) >= expected_min_occurrences
    unresolved = [r for r in recipe_ids if r not in known_recipe_ids]
    assert unresolved == []


def test_finds_a_plausible_variety_of_distinct_recipes() -> None:
    _, body = load_body_from_file(_FIXTURES_DIR / "stal_mielec.sav")

    recipe_ids = find_recipe_ids(body)

    # A real, developed factory runs many different recipes, not just one repeated everywhere.
    assert len(set(recipe_ids)) >= 20
