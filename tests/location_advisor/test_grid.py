"""Tests for the spatial grid, against hand-placed points."""

from pioneer.contracts import Coordinates
from pioneer.location_advisor.grid import SpatialGrid


def _point(x: float, y: float) -> Coordinates:
    return Coordinates(x=x, y=y)


def test_everything_within_the_radius_is_around_a_point() -> None:
    points = [_point(0, 0), _point(90, 0), _point(-99, 99), _point(150, 0), _point(0, 250)]
    grid = SpatialGrid(points, 100.0, lambda point: point)

    near = set(grid.around(_point(0, 0)))

    assert {_point(0, 0), _point(90, 0), _point(-99, 99)} <= near
    assert _point(0, 250) not in near


def test_things_are_found_by_the_position_they_are_given() -> None:
    named = [("near", _point(10, 10)), ("far", _point(1000, 1000))]
    grid = SpatialGrid(named, 50.0, lambda entry: entry[1])

    assert [name for name, _ in grid.around(_point(0, 0))] == ["near"]
