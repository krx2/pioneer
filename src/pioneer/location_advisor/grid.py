"""Finding what lies near a point without measuring the distance to everything."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator

from pioneer.contracts import Coordinates


class SpatialGrid[T]:
    """Things bucketed by `position` into square x/y cells of side `radius`: everything within
    `radius` of a point lies in that point's cell or one of the eight around it."""

    def __init__(
        self, things: Iterable[T], radius: float, position: Callable[[T], Coordinates]
    ) -> None:
        self._size = max(radius, 1.0)
        self._position = position
        self._cells: dict[tuple[int, int], list[T]] = defaultdict(list)
        for thing in things:
            self._cells[self._cell(position(thing))].append(thing)

    def around(self, point: Coordinates) -> Iterator[T]:
        column, row = self._cell(point)
        for d_column in (-1, 0, 1):
            for d_row in (-1, 0, 1):
                yield from self._cells.get((column + d_column, row + d_row), ())

    def _cell(self, point: Coordinates) -> tuple[int, int]:
        return math.floor(point.x / self._size), math.floor(point.y / self._size)
