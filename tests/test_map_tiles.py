"""Cutting the map picture into the tiles the map pages draw, and finding them again — and that the
shipped tiles are those of the shipped picture."""

import json
import math

import pytest

from pioneer.map_image import MAP_IMAGE, cut_tiles, level_sizes, map_tiles


def test_levels_halve_down_to_one_that_fits_in_two_by_two_tiles() -> None:
    assert level_sizes(5000, 5000, 512) == ((625, 625), (1250, 1250), (2500, 2500), (5000, 5000))
    assert level_sizes(1000, 600, 512) == ((1000, 600),)


def test_a_picture_is_cut_into_overlapping_tiles_found_again_by_their_manifest(tmp_path) -> None:
    image = pytest.importorskip("PIL.Image")
    picture = tmp_path / "map.png"
    image.new("RGB", (300, 200), (10, 20, 30)).save(picture)
    out = tmp_path / "tiles"
    (out / "7").mkdir(parents=True)  # a level cut before, from a bigger picture
    (out / "notes").mkdir()

    cut = cut_tiles(picture, out, tile_size=64, overlap=1)
    found = map_tiles(out)

    assert found == cut
    assert cut.levels == ((75, 50), (150, 100), (300, 200))
    assert not (out / "7").exists()
    assert (out / "notes").exists()
    with image.open(found.path(2, 0, 0)) as tile:
        assert tile.size == (65, 65)  # into the neighbours to the right and below
    with image.open(found.path(2, 4, 3)) as tile:
        assert tile.size == (300 - 4 * 64, 200 - 3 * 64)  # the corner: no neighbour to reach into
    assert found.path(2, 5, 0) is None
    assert found.path(3, 0, 0) is None
    assert found.path(-1, 0, 0) is None


def test_without_tiles_or_with_a_manifest_that_cant_be_read_there_are_none(tmp_path) -> None:
    assert map_tiles(tmp_path) is None
    (tmp_path / "tiles.json").write_text("{not json", encoding="utf-8")
    assert map_tiles(tmp_path) is None
    (tmp_path / "tiles.json").write_text(json.dumps({"tile_size": 512}), encoding="utf-8")
    assert map_tiles(tmp_path) is None


@pytest.mark.skipif(
    not MAP_IMAGE.is_file() or map_tiles() is None, reason="docs/map has no picture or no tiles"
)
def test_the_shipped_tiles_are_cut_from_the_shipped_picture() -> None:
    image = pytest.importorskip("PIL.Image")
    tiles = map_tiles()
    with image.open(MAP_IMAGE) as picture:
        size = picture.size

    assert tiles.levels == level_sizes(*size, tiles.tile_size)
    for level, (width, height) in enumerate(tiles.levels):
        for column in range(math.ceil(width / tiles.tile_size)):
            for row in range(math.ceil(height / tiles.tile_size)):
                assert tiles.path(level, column, row) is not None, (level, column, row)
