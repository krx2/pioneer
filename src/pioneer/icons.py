"""Item and building icons, taken from the game's own files: what the map, the production graph and
the chat show next to an item or building.

The game keeps its icons inside its UE5 IoStore archives, which FModel reads and umodel doesn't
(see README for the export). `python -m pioneer.icons <FModel export folder>` then picks out the
icon of every item, building and belt/pipe tier the knowledge base knows, scales it down to
`ICON_SIZE` px and writes it to `docs/icons/<class id>.png`, committed with the rest of the game
data. Which texture is a class's icon comes from its `mSmallIcon` in `docs/en-US.json`; a placed
building (`Build_X_C`) has none of its own, so it takes its build-menu descriptor's (`Desc_X_C`).

Only that import needs Pillow (a dev dependency). At runtime nothing here is used but `ICON_DIR`:
the web app serves whatever is in it, and anything without an icon is drawn the way it was before.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pioneer.knowledge_base import KnowledgeBase, load_from_dict

_PROJECT_ROOT = Path(__file__).parent.parent.parent
ICON_DIR = _PROJECT_ROOT / "docs" / "icons"
_DOCS_JSON = _PROJECT_ROOT / "docs" / "en-US.json"
ICON_SIZE = 96
"""Pixels a side: sharp at the sizes the pages draw icons, on a high-density screen or zoomed in."""

_TEXTURE = re.compile(r"^\w+ /Game/(?P<path>[^.]+)\.")
"""`Texture2D /Game/FactoryGame/.../UI/IconDesc_IronPlates_256.IconDesc_IronPlates_256`."""
_ICON_FIELDS = ("mSmallIcon", "mPersistentBigIcon")


@dataclass(frozen=True)
class ImportReport:
    imported: tuple[str, ...]
    missing: tuple[tuple[str, str | None], ...]
    """Class ids left without an icon, each with the texture it wanted (`None`: it names none)."""


def icon_textures(raw_docs: list[dict[str, Any]]) -> dict[str, str]:
    """Every class's icon texture as a game path, `FactoryGame/Resource/Parts/IronPlate/UI/
    IconDesc_IronPlates_256`, by class id."""
    textures: dict[str, str] = {}
    for group in raw_docs:
        for entry in group.get("Classes", []):
            for field in _ICON_FIELDS:
                match = _TEXTURE.match(entry.get(field) or "")
                if match:
                    textures[entry["ClassName"]] = match["path"]
                    break
    return textures


def texture_for(class_id: str, textures: dict[str, str]) -> str | None:
    if class_id in textures:
        return textures[class_id]
    if class_id.startswith("Build_"):
        return textures.get("Desc_" + class_id.removeprefix("Build_"))
    return None


def wanted_ids(kb: KnowledgeBase) -> tuple[str, ...]:
    """Everything a page can name: items, buildings, and the belt and pipe tiers."""
    ids = [item.item_id for item in kb.items]
    ids += [building.building_id for building in kb.buildings]
    ids += [tier.building_id for tier in kb.transport_tiers]
    return tuple(dict.fromkeys(ids))


def import_icons(
    export_dir: Path,
    class_ids: Iterable[str],
    textures: dict[str, str],
    out_dir: Path,
    *,
    size: int = ICON_SIZE,
) -> ImportReport:
    """Finds each class's texture among the PNGs under `export_dir` and writes it, scaled to fit
    `size` px, to `out_dir/<class id>.png`. An icon already there that this export lacks is kept."""
    from PIL import Image  # only this maintainer step needs Pillow

    exported = _index_pngs(export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    imported: list[str] = []
    missing: list[tuple[str, str | None]] = []
    for class_id in class_ids:
        texture = texture_for(class_id, textures)
        source = _find(texture, exported) if texture is not None else None
        if source is None:
            missing.append((class_id, texture))
            continue
        with Image.open(source) as image:
            icon = image.convert("RGBA")
        icon.thumbnail((size, size), Image.Resampling.LANCZOS)
        icon.save(out_dir / f"{class_id}.png", optimize=True)
        imported.append(class_id)
    return ImportReport(imported=tuple(imported), missing=tuple(missing))


def _index_pngs(export_dir: Path) -> dict[str, list[Path]]:
    """Every PNG under `export_dir` by lower-cased file stem. FModel mirrors the game's folders
    (`/Game/` becomes `FactoryGame/Content/`), but which folder the player points this at, and
    how deep, is up to them — so textures are found by name, the folders only settling a tie."""
    index: dict[str, list[Path]] = defaultdict(list)
    for path in export_dir.rglob("*.png"):
        index[path.stem.lower()].append(path)
    return index


def _find(texture: str, exported: dict[str, list[Path]]) -> Path | None:
    """The PNG named like `texture` that shares the most trailing folders with its game path --
    all of them in a full FModel export, fewer when only part of that tree was exported. A tie
    goes to the first path alphabetically, not whichever the file system happened to list
    first, so the same export gives the same icons everywhere."""
    wanted = texture.lower().split("/")

    def shared(path: Path) -> int:
        parts = [part.lower() for part in path.with_suffix("").parts]
        count = 0
        for have, want in zip(reversed(parts), reversed(wanted), strict=False):
            if have != want:
                break
            count += 1
        return count

    candidates = sorted(exported.get(wanted[-1], []))
    return max(candidates, key=shared) if candidates else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pioneer.icons",
        description="Import item and building icons from an FModel texture export.",
    )
    parser.add_argument("export_dir", type=Path, help="FModel's Output/Exports folder")
    parser.add_argument("--docs", type=Path, default=_DOCS_JSON, help="the game's Docs export")
    parser.add_argument("--out", type=Path, default=ICON_DIR, help="where the icons go")
    args = parser.parse_args(argv)
    if not args.export_dir.is_dir():
        print(f"{args.export_dir} is not a folder", file=sys.stderr)
        return 1

    with open(args.docs, encoding="utf-16") as f:
        raw_docs = json.load(f)
    report = import_icons(
        args.export_dir,
        wanted_ids(load_from_dict(raw_docs)),
        icon_textures(raw_docs),
        args.out,
    )
    print(f"{len(report.imported)} icons written to {args.out}")
    if report.missing:
        print(f"{len(report.missing)} not found; export these folders too to get them:")
        folders: dict[str, int] = defaultdict(int)
        for _, texture in report.missing:
            folders[texture.rsplit("/", 1)[0] if texture else "(no icon in the game data)"] += 1
        for folder, count in sorted(folders.items()):
            print(f"  {folder} ({count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
