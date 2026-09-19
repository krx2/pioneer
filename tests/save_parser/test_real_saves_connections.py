"""The belts and pipes of the real fixture saves, traced end to end (see connections.py)."""

from pathlib import Path

import pytest

from pioneer.save_parser.loader import load_save_state

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("filename", "belt_links", "pipe_links"),
    [
        pytest.param("stal_mielec.sav", 2641, 368, id="stal_mielec"),
        pytest.param("wielka_polska_niesmiertelna.sav", 8325, 3402, id="wielka_polska"),
        pytest.param("alfa.sav", 57, 0, id="alfa"),
        pytest.param("tak.sav", 147, 10, id="tak"),
    ],
)
def test_every_link_joins_buildings_of_the_save_along_its_belts(
    filename: str, belt_links: int, pipe_links: int
) -> None:
    state = load_save_state(_FIXTURES_DIR / filename)
    placed = {placement.object_id: placement for placement in state.placements}

    assert sum(link.carrier == "belt" for link in state.links) == belt_links
    assert sum(link.carrier == "pipe" for link in state.links) == pipe_links
    for link in state.links:
        assert link.source_id in placed and link.target_id in placed
        assert all(
            placed[belt].building_id.startswith(("Build_ConveyorBelt", "Build_ConveyorLift"))
            for belt in link.via
        )


def test_a_screw_constructor_takes_rods_and_feeds_reinforced_plates() -> None:
    state = load_save_state(_FIXTURES_DIR / "stal_mielec.sav")
    placed = {placement.object_id: placement for placement in state.placements}
    screws = "Persistent_Level:PersistentLevel.Build_ConstructorMk1_C_2146791074"

    fed_by = {placed[link.source_id].recipe_id for link in state.links if link.target_id == screws}
    feeds = {placed[link.target_id].recipe_id for link in state.links if link.source_id == screws}

    assert placed[screws].recipe_id == "Recipe_Screw_C"
    assert fed_by == {"Recipe_IronRod_C"}
    assert feeds == {"Recipe_IronPlateReinforced_C"}
