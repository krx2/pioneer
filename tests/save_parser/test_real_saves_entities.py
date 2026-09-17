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
        pytest.param("alfa.sav", 1919, id="alfa"),
        pytest.param("tak.sav", 5165, id="tak"),
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
        pytest.param("alfa.sav", 20, id="alfa"),
        pytest.param("tak.sav", 63, id="tak"),
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

    # One node per distinct recipe. Its machine_count is clock-scaled -- effective machines, see
    # production_graph.py -- so it sums the clock speeds of the buildings carrying that recipe.
    assert state.graph.nodes
    assert len({n.recipe_id for n in state.graph.nodes}) == len(state.graph.nodes)
    assert all(n.is_existing for n in state.graph.nodes)
    assert state.graph.flows == ()

    by_recipe = Counter(p.recipe_id for p in with_recipes)
    assert {n.recipe_id for n in state.graph.nodes} == set(by_recipe)
    for node in state.graph.nodes:
        clock_speeds = [p.clock_speed for p in with_recipes if p.recipe_id == node.recipe_id]
        assert len(clock_speeds) == by_recipe[node.recipe_id]
        assert abs(node.machine_count - sum(clock_speeds)) < 1e-9
    # This save really does run machines off 100% -- underclocked constructors, among others.
    assert any(p.clock_speed != 1.0 for p in with_recipes)


def test_generators_report_their_fuel_and_buildings_their_clock_speed() -> None:
    state = load_save_state(_FIXTURES_DIR / "stal_mielec.sav")

    fuels = Counter((p.building_id, p.fuel_item_id) for p in state.placements if p.fuel_item_id)
    assert fuels == {
        ("Build_GeneratorCoal_C", "Desc_Coal_C"): 36,
        ("Build_GeneratorFuel_C", "Desc_LiquidFuel_C"): 15,
        ("Build_GeneratorIntegratedBiomass_C", "Desc_GenericBiomass_C"): 2,
    }
    water_pumps = [p.clock_speed for p in state.placements if p.building_id == "Build_WaterPump_C"]
    assert water_pumps.count(0.75) == 18


def test_extractors_name_the_node_or_water_volume_they_extract_from() -> None:
    state = load_save_state(_FIXTURES_DIR / "stal_mielec.sav")

    for placement in state.placements:
        if placement.building_id in ("Build_MinerMk1_C", "Build_MinerMk2_C", "Build_OilPump_C"):
            assert placement.resource_node_id is not None
            assert placement.resource_node_id.startswith(
                "Persistent_Level:PersistentLevel.BP_ResourceNode"
            )
        elif placement.building_id == "Build_WaterPump_C":
            assert placement.resource_node_id is not None
            assert "FGWaterVolume" in placement.resource_node_id
        else:
            assert placement.resource_node_id is None
    assert sum(1 for p in state.placements if p.resource_node_id) == 61


def test_the_two_assemblers_put_on_standby_are_left_out_of_the_graph() -> None:
    state = load_save_state(_FIXTURES_DIR / "wielka_polska_niesmiertelna.sav")

    paused = [p for p in state.placements if p.is_paused]
    assert [p.building_id for p in paused] == ["Build_AssemblerMk1_C"] * 2
    for recipe_id in {p.recipe_id for p in paused}:
        running = [
            p.clock_speed for p in state.placements if p.recipe_id == recipe_id and not p.is_paused
        ]
        node = next(n for n in state.graph.nodes if n.recipe_id == recipe_id)
        assert abs(node.machine_count - sum(running)) < 1e-9
    assert all(p.production_boost == 1.0 for p in state.placements)  # no Somersloops slotted


@pytest.mark.parametrize(
    ("filename", "paused"),
    [
        pytest.param("alfa.sav", ["Build_ConstructorMk1_C"], id="alfa"),
        pytest.param("tak.sav", ["Build_AssemblerMk1_C", "Build_MinerMk1_C"], id="tak"),
    ],
)
def test_small_saves_report_what_the_player_put_on_standby(
    filename: str, paused: list[str]
) -> None:
    state = load_save_state(_FIXTURES_DIR / filename)

    assert sorted(p.building_id for p in state.placements if p.is_paused) == paused
    assert state.graph.nodes
    assert all(p.production_boost == 1.0 for p in state.placements)


@pytest.mark.parametrize(
    ("filename", "unlocked", "locked"),
    [
        pytest.param("tak.sav", "Schematic_3-4_C", "Research_Alien_ActiveSAM_C", id="tak"),
        pytest.param(
            "wielka_polska_niesmiertelna.sav", "Schematic_3-4_C", "Schematic_9-1_C", id="wielka"
        ),
    ],
)
def test_the_schematics_the_player_unlocked_are_read(
    filename: str, unlocked: str, locked: str, known_technology_ids: frozenset[str]
) -> None:
    state = load_save_state(_FIXTURES_DIR / filename)

    assert state.unlocked_technology_ids is not None
    assert "Schematic_StartingRecipes_C" in state.unlocked_technology_ids
    assert unlocked in state.unlocked_technology_ids
    assert locked not in state.unlocked_technology_ids
    # Anything the Knowledge Base doesn't list is a schematic it filters out on purpose.
    assert len(state.unlocked_technology_ids & known_technology_ids) > 20


@pytest.fixture(scope="module")
def known_technology_ids() -> frozenset[str]:
    return frozenset(t.technology_id for t in load_from_file(_DOCS_JSON).technologies)
