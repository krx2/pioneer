"""Bonus confidence check against real, committed save files — not the suite that decides whether
object_table.py is "done" (test_object_table.py, hand-built fixtures, is). Uses the *production*
default thresholds (no override), proving those defaults actually distinguish the real object
table from everything else in a genuine, messy save body. Expected values here were confirmed by
hand during development — see object_table.py's module docstring."""

from pathlib import Path

import pytest

from pioneer.save_parser.loader import load_body_from_file
from pioneer.save_parser.object_table import find_and_read_object_table

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("filename", "expected_object_count", "expected_min_actors"),
    [
        pytest.param("stal_mielec.sav", 29211, 10000, id="stal_mielec"),
        pytest.param("wielka_polska_niesmiertelna.sav", 55012, 18000, id="wielka_polska"),
    ],
)
def test_object_table_matches_known_counts(
    filename: str, expected_object_count: int, expected_min_actors: int
) -> None:
    _, body = load_body_from_file(_FIXTURES_DIR / filename)

    objects = find_and_read_object_table(body)

    assert len(objects) == expected_object_count
    actors = [o for o in objects if o.is_actor]
    assert len(actors) >= expected_min_actors
    # Every actor decoded a real position -- proves the transform fields weren't misread.
    assert all(o.position is not None for o in actors)
    # Every component decoded with no position -- proves the OuterPathName branch was taken.
    assert all(o.position is None for o in objects if not o.is_actor)


def test_finds_the_trading_post_at_its_known_position() -> None:
    # There is exactly one Trading Post in a Satisfactory save, and its class/position here were
    # confirmed by hand against the raw bytes during development.
    _, body = load_body_from_file(_FIXTURES_DIR / "stal_mielec.sav")

    objects = find_and_read_object_table(body)
    trading_posts = [o for o in objects if o.class_name.endswith("Build_TradingPost_C")]

    assert len(trading_posts) == 1
    post = trading_posts[0]
    assert post.position is not None
    assert post.position.x == pytest.approx(-254373.671875)
    assert post.position.y == pytest.approx(-44028.76953125)
    assert post.position.z == pytest.approx(-292.38409423828125)


def test_finds_expected_building_variety_in_developed_save() -> None:
    _, body = load_body_from_file(_FIXTURES_DIR / "wielka_polska_niesmiertelna.sav")

    objects = find_and_read_object_table(body)
    building_class_names = {
        o.class_name.rsplit(".", 1)[-1] for o in objects if "/Buildable/" in o.class_name
    }

    # A save with trains, trucks, and manufacturing should show real variety, not a degenerate
    # single-class result a parsing bug would produce.
    assert "Build_ConstructorMk1_C" in building_class_names
    assert "Build_AssemblerMk1_C" in building_class_names
    assert len(building_class_names) > 50
