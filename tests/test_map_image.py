"""Checks the map picture's calibration against the shipped resource node data: every deposit is
somewhere on the game's land, so where the calibration puts it on the picture should be land too —
not the sea, and not the black outside the world."""

import pytest

from pioneer.app import load_resource_nodes
from pioneer.map_image import MAP_IMAGE, WORLD_BOUNDS

Image = pytest.importorskip("PIL.Image")

pytestmark = pytest.mark.skipif(not MAP_IMAGE.is_file(), reason="docs/map/map.jpg not present")


def _where(picture, x: float, y: float, bounds=WORLD_BOUNDS, *, flip: bool = False) -> str:
    min_x, min_y, max_x, max_y = bounds
    width, height = picture.size
    column = (x - min_x) / (max_x - min_x) * width
    row = (y - min_y) / (max_y - min_y) * height
    if flip:
        row = height - row
    if not (0 <= column < width and 0 <= row < height):
        return "off"
    red, green, blue = picture.getpixel((int(column), int(row)))
    if max(red, green, blue) < 40:
        return "off"  # the black outside the world
    return "water" if blue > red + 25 else "land"


def _land_share(picture, nodes, bounds=WORLD_BOUNDS, *, flip: bool = False) -> tuple[float, int]:
    seen = [_where(picture, n.position.x, n.position.y, bounds, flip=flip) for n in nodes]
    return seen.count("land") / len(seen), seen.count("off")


@pytest.fixture(scope="module")
def picture():
    return Image.open(MAP_IMAGE).convert("RGB")


@pytest.fixture(scope="module")
def deposits():
    """Solid deposits: ore lies on land (water and wells may sit in lakes and swamps)."""
    nodes = [n for n in load_resource_nodes() if n.item_id.startswith("Desc_Ore")]
    assert len(nodes) > 200
    return nodes


def test_deposits_land_on_the_maps_land(picture, deposits) -> None:
    share, off = _land_share(picture, deposits)

    assert share > 0.95
    assert off == 0


def test_the_calibration_beats_its_neighbours(picture, deposits) -> None:
    best, _ = _land_share(picture, deposits)
    min_x, min_y, max_x, max_y = WORLD_BOUNDS

    flipped, _ = _land_share(picture, deposits, flip=True)
    shifted = [
        _land_share(picture, deposits, (min_x + dx, min_y + dy, max_x + dx, max_y + dy))[0]
        for dx, dy in ((10_000, 0), (-10_000, 0), (0, 10_000), (0, -10_000))
    ]

    assert flipped < best - 0.2
    assert all(share < best for share in shifted)
