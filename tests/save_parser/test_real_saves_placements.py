"""Bonus confidence check against real, committed save files — not the suite that decides whether
placements.py is "done" (test_placements.py, hand-built fixtures, is)."""

from pathlib import Path

import pytest

from pioneer.save_parser.loader import load_body_from_file
from pioneer.save_parser.object_table import find_and_read_object_table
from pioneer.save_parser.placements import to_placement_records

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("filename", "expected_min_buildings"),
    [
        pytest.param("stal_mielec.sav", 7000, id="stal_mielec"),
        pytest.param("wielka_polska_niesmiertelna.sav", 13000, id="wielka_polska"),
    ],
)
def test_finds_plausible_number_of_buildings(filename: str, expected_min_buildings: int) -> None:
    _, body = load_body_from_file(_FIXTURES_DIR / filename)
    objects = find_and_read_object_table(body)

    records = to_placement_records(objects)

    assert len(records) >= expected_min_buildings
    assert all(r.building_id for r in records)
    assert all(r.recipe_id is None for r in records)


def test_trading_post_is_a_placement_record() -> None:
    _, body = load_body_from_file(_FIXTURES_DIR / "stal_mielec.sav")
    objects = find_and_read_object_table(body)

    records = to_placement_records(objects)
    trading_posts = [r for r in records if r.building_id == "Build_TradingPost_C"]

    assert len(trading_posts) == 1
    assert trading_posts[0].position.x == pytest.approx(-254373.671875)
