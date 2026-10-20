"""The game's own map, drawn under the markers of every map page: the picture, and where in the
world it lies.

`docs/map/map.jpg` is the official map of the game's world from its wiki — the developers' own
artwork, like the icons in `docs/icons/` — 2000 px square. Without the file, the map pages draw the
plain grid they always did.

**Calibration.** Save and resource node positions are centimetres in the game's world, x growing
east and y south — as on the picture, whose top left is the world's north-west corner. The picture
spans the world's bounds as the community's map tools use them: x from -324 698.83 to 425 301.83
and y from -375 000 to 375 000, 7.5 km each way. tests/test_map_image.py checks that against the
shipped node data: deposits land on land, none off the map — and the picture flipped, or shifted by
100 m, does markedly worse.
"""

from __future__ import annotations

from pathlib import Path

MAP_DIR = Path(__file__).parent.parent.parent / "docs" / "map"
MAP_IMAGE = MAP_DIR / "map.jpg"

WORLD_MIN_X = -324_698.832031
WORLD_MAX_X = 425_301.832031
WORLD_MIN_Y = -375_000.0
WORLD_MAX_Y = 375_000.0
WORLD_BOUNDS = (WORLD_MIN_X, WORLD_MIN_Y, WORLD_MAX_X, WORLD_MAX_Y)
"""(min x, min y, max x, max y) of the world the picture shows, in save centimetres."""


def map_image() -> Path | None:
    """The map picture, if it's there."""
    return MAP_IMAGE if MAP_IMAGE.is_file() else None
