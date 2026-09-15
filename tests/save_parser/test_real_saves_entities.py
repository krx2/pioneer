"""Bonus confidence check against real, committed save files — not the suite that decides whether
entity framing is "done" (test_entities.py, hand-built fixtures, is). This is where the framing
claim actually earns its keep: it must walk tens of thousands of real entities and land exactly on
the section end the save itself declares, and attribute every recipe to a real manufacturer."""

from collections import Counter
from pathlib import Path

import pytest

from pioneer.knowledge_base.loader import load_from_file
from pioneer.save_parser.entities import find_entity_spans
from pioneer.save_parser.loader import load_body_from_file, load_save_state
from pioneer.save_parser.object_table import find_object_table
from pioneer.save_parser.properties import find_recipe_ids

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_DOCS_JSON = Path(__file__).parent.parent.parent / "docs" / "en-US.json"

_MANUFACTURER_IDS = frozenset(
    {
        "Build_ConstructorMk1_C",
        "Build_SmelterMk1_C",
        "Build_AssemblerMk1_C",
        "Build_FoundryMk1_C",
        "Build_ManufacturerMk1_C",
        "Build_OilRefinery_C",
        "Build_Packager_C",
        "Build_Blender_C",
        "Build_HadronCollider_C",
        "Build_ConverterMk1_C",
        "Build_QuantumEncoder_C",
    }
)


@pytest.fixture(scope="module")
def known_recipe_ids() -> frozenset[str]:
    kb = load_from_file(_DOCS_JSON)
    return frozenset(r.recipe_id for r in kb.recipes)


@pytest.mark.parametrize(
    ("filename", "expected_entities"),
    [
        pytest.param("stal_mielec.sav", 29211, id="stal_mielec"),
        pytest.param("wielka_polska_niesmiertelna.sav", 55012, id="wielka_polska"),
    ],
)
def test_frames_every_entity_and_lands_on_the_declared_section_end(
    filename: str, expected_entities: int
) -> None:
    # find_entity_spans raises if the walk and the declared length disagree, so reaching the
    # assertions at all is already the byte-exactness check.
    _, body = load_body_from_file(_FIXTURES_DIR / filename)
    table = find_object_table(body)

    spans = find_entity_spans(body, table)

    assert len(spans) == expected_entities == len(table.headers)
    assert all(span.end > span.start for span in spans)


@pytest.mark.parametrize(
    ("filename", "expected_recipes"),
    [
        pytest.param("stal_mielec.sav", 500, id="stal_mielec"),
        pytest.param("wielka_polska_niesmiertelna.sav", 906, id="wielka_polska"),
    ],
)
def test_every_recipe_is_attributed_to_exactly_one_manufacturer(
    filename: str, expected_recipes: int, known_recipe_ids: frozenset[str]
) -> None:
    _, body = load_body_from_file(_FIXTURES_DIR / filename)
    table = find_object_table(body)
    spans = find_entity_spans(body, table)

    unattributed = len(find_recipe_ids(body))
    attributed = [
        (header.class_name.rsplit(".", 1)[-1], find_recipe_ids(body, start=s.start, end=s.end))
        for header, s in zip(table.headers, spans, strict=True)
    ]
    carrying = [(cls, ids) for cls, ids in attributed if ids]

    # Every occurrence found body-wide lands in exactly one entity's span -- none lost, none double
    # counted across span boundaries.
    assert sum(len(ids) for _, ids in carrying) == unattributed == expected_recipes
    assert all(len(ids) == 1 for _, ids in carrying)
    assert {cls for cls, _ in carrying} <= _MANUFACTURER_IDS
    assert all(ids[0] in known_recipe_ids for _, ids in carrying)


def test_load_save_state_produces_a_usable_existing_factory_graph() -> None:
    state = load_save_state(_FIXTURES_DIR / "stal_mielec.sav")

    assert state.header.session_name == "stal mielec"
    assert len(state.placements) >= 7000

    with_recipes = [p for p in state.placements if p.recipe_id is not None]
    assert len(with_recipes) == 500
    assert all(p.building_id in _MANUFACTURER_IDS for p in with_recipes)

    # One node per distinct recipe, machine counts summing back to the buildings that carry one.
    assert state.graph.nodes
    assert sum(n.machine_count for n in state.graph.nodes) == len(with_recipes)
    assert len({n.recipe_id for n in state.graph.nodes}) == len(state.graph.nodes)
    assert all(n.is_existing for n in state.graph.nodes)
    assert state.graph.flows == ()

    by_recipe = Counter(p.recipe_id for p in with_recipes)
    for node in state.graph.nodes:
        assert node.machine_count == by_recipe[node.recipe_id]
