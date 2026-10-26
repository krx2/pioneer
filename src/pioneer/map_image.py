"""The game's own map, drawn under the markers of every map page: the picture, the tiles it's cut
into, and where in the world it lies.

`assets/map/map.jpg` is the official 5000 px map from the game's wiki. `python -m pioneer.map_image
[picture]` cuts it into levels, each half the size of the one above, and every level into
`TILE_SIZE` px tiles reaching `TILE_OVERLAP` px into their neighbours, so no seam shows. Only that
needs Pillow (a dev dependency).

Positions are centimetres in the game's world, x east and y south; the picture spans the bounds
the community's map tools use, its top left the world's north-west corner.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

MAP_DIR = Path(__file__).parent.parent.parent / "assets" / "map"
MAP_IMAGE = MAP_DIR / "map.jpg"
TILE_DIR = MAP_DIR / "tiles"
TILE_SIZE = 512
TILE_OVERLAP = 1
_TILE_QUALITY = 85
_MANIFEST = "tiles.json"

WORLD_MIN_X = -324_698.832031
WORLD_MAX_X = 425_301.832031
WORLD_MIN_Y = -375_000.0
WORLD_MAX_Y = 375_000.0
WORLD_BOUNDS = (WORLD_MIN_X, WORLD_MIN_Y, WORLD_MAX_X, WORLD_MAX_Y)
"""(min x, min y, max x, max y) of the world the picture shows, in save centimetres."""


def map_image() -> Path | None:
    """The map picture, if it's there."""
    return MAP_IMAGE if MAP_IMAGE.is_file() else None


@dataclass(frozen=True)
class MapTiles:
    """The tiles the map picture was cut into."""

    directory: Path
    tile_size: int
    overlap: int
    levels: tuple[tuple[int, int], ...]
    """Each level's (width, height) in px, the smallest first: every level shows the whole
    picture, `tile_size` px a tile."""

    def path(self, level: int, column: int, row: int) -> Path | None:
        """Where a tile is — `None` for one the levels don't have, or that isn't there."""
        if not 0 <= level < len(self.levels):
            return None
        width, height = self.levels[level]
        if not (0 <= column < math.ceil(width / self.tile_size)):
            return None
        if not (0 <= row < math.ceil(height / self.tile_size)):
            return None
        path = self.directory / str(level) / f"{column}_{row}.webp"
        return path if path.is_file() else None


def map_tiles(directory: Path = TILE_DIR) -> MapTiles | None:
    """The tiles `python -m pioneer.map_image` cut, if they're there: `None` without them, or with
    a `tiles.json` that can't be read."""
    try:
        manifest = json.loads((directory / _MANIFEST).read_text(encoding="utf-8"))
        levels = tuple((int(width), int(height)) for width, height in manifest["levels"])
        tiles = MapTiles(directory, int(manifest["tile_size"]), int(manifest["overlap"]), levels)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return tiles if tiles.levels and tiles.tile_size > 0 else None


def level_sizes(width: int, height: int, tile_size: int = TILE_SIZE) -> tuple[tuple[int, int], ...]:
    """The size of every level of a `width` × `height` picture, the smallest first: halved from the
    picture itself until one fits in 2 × 2 tiles."""
    sizes = [(width, height)]
    while max(sizes[-1]) > 2 * tile_size:
        last_width, last_height = sizes[-1]
        sizes.append((max(1, round(last_width / 2)), max(1, round(last_height / 2))))
    return tuple(reversed(sizes))


def cut_tiles(
    picture: Path,
    directory: Path = TILE_DIR,
    *,
    tile_size: int = TILE_SIZE,
    overlap: int = TILE_OVERLAP,
) -> MapTiles:
    """Cuts `picture` into tiles in `directory`, replacing the levels cut there before."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None  # the game's own picture, not something off the internet
    with Image.open(picture) as opened:
        image = opened.convert("RGB")
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.iterdir():
        if old.is_dir() and old.name.isdigit():
            shutil.rmtree(old)
    levels = level_sizes(*image.size, tile_size)
    for level, (width, height) in enumerate(levels):
        scaled = image.resize((width, height), Image.LANCZOS) if level < len(levels) - 1 else image
        level_dir = directory / str(level)
        level_dir.mkdir()
        for column in range(math.ceil(width / tile_size)):
            for row in range(math.ceil(height / tile_size)):
                left, top = column * tile_size, row * tile_size
                box = (
                    left,
                    top,
                    min(width, left + tile_size + overlap),
                    min(height, top + tile_size + overlap),
                )
                scaled.crop(box).save(
                    level_dir / f"{column}_{row}.webp", "WEBP", quality=_TILE_QUALITY, method=6
                )
    manifest = {"tile_size": tile_size, "overlap": overlap, "levels": [list(s) for s in levels]}
    (directory / _MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return MapTiles(directory, tile_size, overlap, levels)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pioneer.map_image",
        description="Cut the map picture into the tiles the map pages draw.",
    )
    parser.add_argument("picture", nargs="?", type=Path, default=MAP_IMAGE)
    parser.add_argument("--out", type=Path, default=TILE_DIR)
    args = parser.parse_args(argv)
    if not args.picture.is_file():
        print(f"no picture at {args.picture}", file=sys.stderr)
        return 1
    tiles = cut_tiles(args.picture, args.out)
    files = list(tiles.directory.glob("*/*.webp"))
    size = sum(path.stat().st_size for path in files)
    sizes = ", ".join(f"{width} px" for width, _ in tiles.levels)
    print(f"{len(files)} tiles in {len(tiles.levels)} levels ({sizes}), {size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
