"""Renders resource nodes, existing buildings, and recommended locations on the map
(implementation.md Stage 14).

Split the same way as `graph_presentation`: `build_markers` is a pure, unit-testable transform
(contracts in, marker specs out); `render_page` lays those markers out as an SVG, panned and
zoomed by `MAP_SCRIPT` moving the SVG's own `viewBox` -- no D3 needed, since it's just a fixed
set of markers rather than a force simulation. The SVG works in the game's own coordinates, so a
picture of the game's map (`MapBackground`) goes under the markers spanning the world it shows: as
tiles, when it's been cut into them (`MapTileSet`), the script drawing those of the part on screen
at the level sharp at its zoom; else as one `<image>`. Without a picture, a grid stands in for it.

Markers are drawn in the game's coordinates too, sized for the first view, and the script resizes
them as the view changes: on the game's map by how long a kilometre is on screen -- the square root
of it, within bounds, as the header's map does -- and on a grid by the square root of the zoom. So
up close they don't cover what they mark, and the whole map isn't buried under them.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pioneer.contracts import (
    FactorySite,
    NodeAssignment,
    PlacementRecord,
    Purity,
    RankedLocation,
    ResourceNode,
    SitePlan,
)

MarkerKind = Literal["resource", "existing_building", "recommended", "factory", "new_site"]

_CLASS_ID_AFFIX = re.compile(r"^(?:Desc|Recipe|Build|BP|Schematic|Research)_|_C$")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def readable_id(class_id: str) -> str:
    """`Desc_IronPlate_C` -> `Iron Plate`. What a label falls back to for an id the knowledge base
    has no name for -- the made-up geyser id the resource data carries, a modded save, or no
    knowledge base at all. A pin on the player's map should name the thing, not its class id.
    Deliberately a copy of `graph_presentation`'s: a module imports `contracts` and nothing else of
    this project's (tests/test_architecture.py), and five lines is a cheap price for that."""
    words = _CAMEL_BOUNDARY.sub(" ", _CLASS_ID_AFFIX.sub("", class_id)).replace("_", " ")
    return " ".join(words.split()) or class_id


_PURITY_COLOR = {
    Purity.IMPURE: "#a9702f",
    Purity.NORMAL: "#8fa6bd",
    Purity.PURE: "#f2c94c",
}
_EXISTING_COLOR = "#6b7683"
_RECOMMENDED_COLOR = "#33c17a"
_FACTORY_COLOR = "#2f6fed"
_NEW_SITE_COLOR = "#f2a344"
_LABELLED = ("recommended", "factory", "new_site")


@dataclass(frozen=True)
class MapTileSet:
    """The map picture cut into tiles (see `pioneer.map_image`): tile `<column>_<row>` of level `n`
    is at `<url>/<n>/<column>_<row>.webp`, `size` px a side and `overlap` px more where it has a
    neighbour to the right or below."""

    url: str
    size: int
    overlap: int
    levels: tuple[tuple[int, int], ...]
    """Each level's (width, height) in px, the smallest first: every level shows the whole map."""

    def as_json(self) -> dict[str, object]:
        return {
            "url": self.url,
            "size": self.size,
            "overlap": self.overlap,
            "levels": [list(level) for level in self.levels],
        }


@dataclass(frozen=True)
class MapBackground:
    """A picture of the game's map, and the part of the world it shows, in game coordinates: one
    image at `href`, or the `tiles` it's cut into -- those, when there are both."""

    href: str | None
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    tiles: MapTileSet | None = None

    def svg(self) -> str:
        if self.tiles is not None:
            return '<g class="world-tiles"></g>'  # filled by MAP_SCRIPT with the tiles on screen
        width, height = self.max_x - self.min_x, self.max_y - self.min_y
        return (
            f'<image class="world" href="{html.escape(self.href or "")}" x="{self.min_x}" '
            f'y="{self.min_y}" width="{width}" height="{height}" preserveAspectRatio="none" />'
        )


@dataclass(frozen=True)
class MapMarker:
    x: float
    y: float
    kind: MarkerKind
    label: str
    color: str
    rank: int | None = None
    icon: str | None = None


def build_markers(
    resource_nodes: tuple[ResourceNode, ...] = (),
    placements: tuple[PlacementRecord, ...] = (),
    ranked_locations: tuple[RankedLocation, ...] = (),
    names: Mapping[str, str] | None = None,
    factory_sites: tuple[FactorySite, ...] = (),
    icons: Mapping[str, str] | None = None,
    site_plan: SitePlan | None = None,
) -> tuple[MapMarker, ...]:
    """`names` maps item, recipe and building ids to the names labels show; an id without one
    falls back to `readable_id`, never to the raw class id. A factory site is labelled with its id
    and the recipes most of its buildings run. `icons` maps ids to icon URLs: a deposit and a
    recommended site show the resource's, a building its own, a factory its main recipe's.

    With a `site_plan`, a recommended deposit it works is labelled with how — extractor, clock
    speed, power shards, what it gives and to which site — rather than ranked, and each of the
    plan's sites is a marker of its own, labelled with what it builds, first what it's for."""

    def name(class_id: str) -> str:
        return (names or {}).get(class_id) or readable_id(class_id)

    def icon(class_id: str | None) -> str | None:
        return (icons or {}).get(class_id) if class_id is not None else None

    markers = [
        MapMarker(
            x=node.position.x,
            y=node.position.y,
            kind="resource",
            label=f"{name(node.item_id)} ({node.purity.value})",
            color=_PURITY_COLOR[node.purity],
            icon=icon(node.item_id),
        )
        for node in resource_nodes
    ]
    markers += [
        MapMarker(
            x=placement.position.x,
            y=placement.position.y,
            kind="existing_building",
            label=name(placement.recipe_id or placement.building_id),
            color=_EXISTING_COLOR,
            icon=icon(placement.building_id),
        )
        for placement in placements
    ]
    # Which resource a recommended deposit holds comes from the node it points at: on a real
    # save's map the resource pins are past `label_limit` and so unlabelled, leaving a green
    # "#1 pure" that never says what the player would be mining there.
    resource_of = {node.node_id: node.item_id for node in resource_nodes}
    worked = {d.resource_node_id: d for d in site_plan.deposits} if site_plan else {}
    markers += [
        MapMarker(
            x=location.position.x,
            y=location.position.y,
            kind="recommended",
            label=_deposit_label(worked[location.resource_node_id], name)
            if location.resource_node_id in worked
            else f"#{rank} {_recommended_resource(location, resource_of, name)}"
            f"{location.purity.value}, score {location.score:.2f}",
            color=_RECOMMENDED_COLOR,
            rank=None if location.resource_node_id in worked else rank,
            icon=icon(resource_of.get(location.resource_node_id)),
        )
        for rank, location in enumerate(
            sorted(ranked_locations, key=lambda loc: -loc.score), start=1
        )
    ]
    for site in site_plan.sites if site_plan else ():
        mined = sorted({d.item_id for d in site_plan.deposits if d.site_id == site.site_id})
        made = [stage.recipe_id for stage in site.stages][:3]
        markers.append(
            MapMarker(
                x=site.position.x,
                y=site.position.y,
                kind="new_site",
                label=f"{site.site_id}: "
                + (
                    ", ".join(name(recipe_id) for recipe_id in made)
                    if made
                    else ", ".join(name(item_id) for item_id in mined) + ", sent on"
                ),
                color=_NEW_SITE_COLOR,
                icon=icon(made[0]) if made else icon(next(iter(mined), None)),
            )
        )
    markers += [
        MapMarker(
            x=site.position.x,
            y=site.position.y,
            kind="factory",
            label=f"{site.site_id}: {_main_recipes(site, name)}",
            color=_FACTORY_COLOR,
            icon=icon(next(iter(_recipe_counts(site)), None)),
        )
        for site in factory_sites
    ]
    return tuple(markers)


def _recommended_resource(
    location: RankedLocation, resource_of: Mapping[str, str], name: Callable[[str], str]
) -> str:
    """`"Iron Ore, "` for a recommended deposit whose node the map was given, else `""` -- the
    ranking alone doesn't carry the item, and a map drawn without the nodes can't name it."""
    item_id = resource_of.get(location.resource_node_id)
    return f"{name(item_id)}, " if item_id is not None else ""


def _deposit_label(deposit: NodeAssignment, name: Callable[[str], str]) -> str:
    """`Iron Ore, normal: Miner Mk.3 at 167%, 2 shards, 400/min to new_site_1`."""
    shards = f", {deposit.power_shards} shard{'s' if deposit.power_shards != 1 else ''}"
    return (
        f"{name(deposit.item_id)}, {deposit.purity.value}: {name(deposit.extractor_id)} at "
        f"{deposit.clock_speed * 100:.0f}%{shards if deposit.power_shards else ''}, "
        f"{deposit.rate_per_minute:.0f}/min to {deposit.site_id}"
    )


def _recipe_counts(site: FactorySite) -> dict[str, int]:
    """How many of the site's buildings run each recipe, most common first."""
    counts = Counter(p.recipe_id for p in site.placements if p.recipe_id is not None)
    return dict(counts.most_common())


def _main_recipes(site: FactorySite, name: Callable[[str], str], shown: int = 3) -> str:
    return ", ".join(name(recipe_id) for recipe_id in list(_recipe_counts(site))[:shown])


def compute_view_box(
    markers: tuple[MapMarker, ...], *, padding: float = 300.0
) -> tuple[float, float, float, float]:
    """Bounding box over every marker, padded so pins near the edge aren't clipped. Falls back to
    a fixed unit box when there's nothing to show, so `render_page` never divides by zero."""
    if not markers:
        return (0.0, 0.0, 1000.0, 1000.0)
    xs = [m.x for m in markers]
    ys = [m.y for m in markers]
    min_x, max_x = min(xs) - padding, max(xs) + padding
    min_y, max_y = min(ys) - padding, max(ys) + padding
    return (min_x, min_y, max_x - min_x, max_y - min_y)


_MARKER_RADIUS_FACTOR = {
    "resource": 0.7,
    "existing_building": 0.5,
    "recommended": 0.8,
    "factory": 1.1,
    "new_site": 1.3,
}
"""Marker radius as a fraction of `unit` (see `render_page`) — sized relative to the view box
rather than a fixed pixel count, since game coordinates can range from small fixture numbers up
to the real map's much larger scale."""


def _svg_marker(marker: MapMarker, *, unit: float, show_label: bool) -> str:
    """A marker is a dot (a square for a building) in its kind's colour. With an icon, that colour
    becomes a ring around the icon on a dark ground, and a rank moves from the middle of the dot
    to a small disc on its upper right."""
    radius = _MARKER_RADIUS_FACTOR[marker.kind] * unit
    if marker.icon is not None:
        fill, stroke, stroke_width = "#161a1d", marker.color, unit * 0.14
    else:
        fill, stroke, stroke_width = marker.color, "#1f2427", max(unit * 0.06, 0.5)
    shape = (
        f'<rect x="{marker.x - radius}" y="{marker.y - radius}" width="{radius * 2}" '
        f'height="{radius * 2}" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{stroke_width}" />'
        if marker.kind in ("existing_building", "factory", "new_site")
        else f'<circle cx="{marker.x}" cy="{marker.y}" r="{radius}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{stroke_width}" />'
    )
    if marker.icon is not None:
        side = radius * 1.5
        shape += (
            f'<image href="{html.escape(marker.icon)}" x="{marker.x - side / 2}" '
            f'y="{marker.y - side / 2}" width="{side}" height="{side}" />'
        )
    if marker.rank is None:
        badge = ""
    elif marker.icon is None:
        badge = (
            f'<text x="{marker.x}" y="{marker.y + unit * 0.13}" text-anchor="middle" '
            f'class="badge" style="font-size:{unit:g}px">{marker.rank}</text>'
        )
    else:
        bx, by = marker.x + radius * 0.8, marker.y - radius * 0.8
        badge = (
            f'<circle cx="{bx}" cy="{by}" r="{unit * 0.42}" fill="{marker.color}" />'
            f'<text x="{bx}" y="{by + unit * 0.21}" text-anchor="middle" class="badge" '
            f'style="font-size:{unit * 0.6:g}px">{marker.rank}</text>'
        )
    label_style = f"font-size:{unit:g}px;stroke-width:{unit * 0.25:g}px"
    # A recommended location's position is typically the same resource node it recommends (per
    # Location Advisor), so its label goes *above* the marker rather than below — otherwise it'd
    # collide with the resource node's own label sitting right underneath at the same coordinates.
    above = marker.kind in _LABELLED
    label_y = marker.y - radius - unit if above else marker.y + radius + unit
    label = (
        f'<text x="{marker.x}" y="{label_y}" text-anchor="middle" class="label" '
        f'style="{label_style}">{html.escape(marker.label)}</text>'
        if show_label
        else ""
    )
    return (
        f'<g class="marker marker-{marker.kind}" data-x="{marker.x}" data-y="{marker.y}">'
        f"<title>{html.escape(marker.label)}</title>"
        f"{shape}{badge}{label}"
        f"</g>"
    )


def render_page(
    resource_nodes: tuple[ResourceNode, ...] = (),
    placements: tuple[PlacementRecord, ...] = (),
    ranked_locations: tuple[RankedLocation, ...] = (),
    *,
    title: str = "Factory Map",
    names: Mapping[str, str] | None = None,
    label_limit: int = 40,
    factory_sites: tuple[FactorySite, ...] = (),
    icons: Mapping[str, str] | None = None,
    background: MapBackground | None = None,
    site_plan: SitePlan | None = None,
) -> str:
    """Every marker keeps its label as a hover tooltip; past `label_limit` markers only the
    recommended sites and factories are also labelled on the map — a real map's hundreds of nodes
    would otherwise bury the pins under text. With a `background`, the game's map lies under the
    markers, and zooming out goes as far as all of it. With a `site_plan`, its sites are marked,
    a line runs from each deposit to the site it feeds, and one between sites for what one sends
    another, labelled with what and how much."""
    markers = build_markers(
        resource_nodes, placements, ranked_locations, names, factory_sites, icons, site_plan
    )
    label_everything = len(markers) <= label_limit
    min_x, min_y, w, h = compute_view_box(markers)
    unit = max(w, h) / 45
    grid = background.svg() if background else _svg_grid(min_x, min_y, w, h, unit=unit)
    world_width = background.max_x - background.min_x if background else 0
    world = (
        {
            "x": background.min_x,
            "y": background.min_y,
            "w": background.max_x - background.min_x,
            "h": background.max_y - background.min_y,
        }
        if background
        else None
    )
    tiles = background.tiles.as_json() if background and background.tiles else None
    markers_svg = "".join(
        _svg_marker(m, unit=unit, show_label=label_everything or m.kind in _LABELLED)
        for m in markers
    )
    links = _svg_links(site_plan, unit, names) if site_plan else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
</head>
<body>
<svg id="map" viewBox="{min_x} {min_y} {w} {h}" preserveAspectRatio="xMidYMid meet">
{grid}
{links}
{markers_svg}
</svg>
<div id="legend">
  <span><i class="dot" style="background:{_PURITY_COLOR[Purity.PURE]}"></i> pure node</span>
  <span><i class="dot" style="background:{_PURITY_COLOR[Purity.NORMAL]}"></i> normal node</span>
  <span><i class="dot" style="background:{_PURITY_COLOR[Purity.IMPURE]}"></i> impure node</span>
  <span><i class="sq" style="background:{_EXISTING_COLOR}"></i> existing building</span>
  <span><i class="dot" style="background:{_RECOMMENDED_COLOR}"></i> recommended</span>
  <span><i class="sq" style="background:{_FACTORY_COLOR}"></i> factory to extend</span>
  {
        f'<span><i class="sq" style="background:{_NEW_SITE_COLOR}"></i> site to build</span>'
        if site_plan
        else ""
    }
</div>
<script>
const initialView = {{x: {min_x}, y: {min_y}, w: {w}, h: {h}}};
const worldWidth = {world_width};
const markerUnit = {unit};
const world = {json.dumps(world)};
const tiles = {json.dumps(tiles)};
{MAP_SCRIPT}
</script>
</body>
</html>
"""


def _svg_links(site_plan: SitePlan, unit: float, names: Mapping[str, str] | None = None) -> str:
    """A line from each deposit to the site it feeds, and between sites, one for each thing one
    sends another — labelled halfway along with what, how much and where to."""
    at = {site.site_id: site.position for site in site_plan.sites}
    lines = [
        f'<line class="haul" x1="{d.position.x}" y1="{d.position.y}" '
        f'x2="{at[d.site_id].x}" y2="{at[d.site_id].y}" />'
        for d in site_plan.deposits
        if d.site_id in at
    ]
    sent: dict[tuple[str, str], list[str]] = {}
    for transfer in site_plan.transfers:
        item = (names or {}).get(transfer.item_id) or readable_id(transfer.item_id)
        sent.setdefault((transfer.from_site_id, transfer.to_site_id), []).append(
            f"{item} {transfer.rate_per_minute:.0f}/min"
        )
    for (source, target), what in sent.items():
        a, b = at[source], at[target]
        mx, my = (a.x + b.x) / 2, (a.y + b.y) / 2
        text = html.escape(f"{', '.join(what)} to {target}")
        lines.append(f'<line class="sends" x1="{a.x}" y1="{a.y}" x2="{b.x}" y2="{b.y}" />')
        lines.append(
            f'<g class="marker marker-sends" data-x="{mx}" data-y="{my}"><title>{text}</title>'
            f'<text x="{mx}" y="{my}" text-anchor="middle" class="label sends-label" '
            f'style="font-size:{unit * 0.8:g}px;stroke-width:{unit * 0.25:g}px">{text}</text></g>'
        )
    return f'<g class="links">{"".join(lines)}</g>'


def _svg_grid(min_x: float, min_y: float, w: float, h: float, *, unit: float) -> str:
    step = unit * 5
    stroke_width = max(unit * 0.03, 0.25)
    lines = []
    x = min_x - (min_x % step)
    while x <= min_x + w:
        lines.append(f'<line x1="{x}" y1="{min_y}" x2="{x}" y2="{min_y + h}" class="grid-line" />')
        x += step
    y = min_y - (min_y % step)
    while y <= min_y + h:
        lines.append(f'<line x1="{min_x}" y1="{y}" x2="{min_x + w}" y2="{y}" class="grid-line" />')
        y += step
    return f'<g style="stroke-width:{stroke_width:g}">{"".join(lines)}</g>'


STYLE = """
:root { color-scheme: dark; }
html, body { margin:0; height:100%; background:#1f2427; font-family: system-ui, sans-serif; }
svg { width:100%; height:100%; display:block; cursor:grab; touch-action:none; user-select:none; }
svg image { -webkit-user-drag:none; user-drag:none; }
svg.dragging { cursor:grabbing; }
.grid-line { stroke:#2c3439; }
.label { fill:#d3dade; paint-order: stroke; stroke:#1f2427; }
.badge { fill:#1f2427; font-weight:bold; }
.haul { stroke:#33c17a; stroke-width:2px; vector-effect:non-scaling-stroke; opacity:0.85; }
.sends { stroke:#f2a344; stroke-width:4px; vector-effect:non-scaling-stroke; opacity:0.9; }
.sends-label { fill:#f2c98f; }
#legend {
  position:fixed; top:12px; left:12px; display:flex; flex-wrap:wrap; gap:14px;
  color:#d3dade; font-size:12px; background:#16191bcc; padding:8px 12px;
  border-radius:8px; max-width: 90vw;
}
#legend .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }
#legend .sq { display:inline-block; width:10px; height:10px; margin-right:6px; }
"""

MAP_SCRIPT = """
const SVG_NS = "http://www.w3.org/2000/svg";
const svg = document.getElementById("map");
let view = {...initialView};
const maxWidth = Math.max(initialView.w * 6, worldWidth * 1.2);
const MAX_MAGNIFICATION = 4;  // screen pixels a pixel of the sharpest tiles may be stretched over
const markers = [...svg.querySelectorAll(".marker")].map(
  (group) => [group, Number(group.dataset.x), Number(group.dataset.y)]);
const tileLayer = tiles && world ? svg.querySelector(".world-tiles") : null;
const tileLevels = [];  // a group of images a level, the coarsest first: the sharper over it
const tileImages = new Map();  // "level/column_row" -> its image, on screen or not

function minWidth() {
  // As far in as the markers allow, but not past where the map picture stays sharp.
  const rect = svg.getBoundingClientRect();
  if (!tileLayer || !rect.width) return initialView.w * 0.04;
  const sharpest = world.w / tiles.levels[tiles.levels.length - 1][0];
  return Math.max(initialView.w * 0.04,
    Math.min(sharpest * rect.width / MAX_MAGNIFICATION, initialView.w));
}

function onScreen() {
  // The view box is fitted into the window ("meet"): one way, the screen shows more than it.
  const rect = svg.getBoundingClientRect();
  const scale = Math.max(view.w / rect.width, view.h / rect.height);
  const w = rect.width * scale;
  const h = rect.height * scale;
  return {x: view.x - (w - view.w) / 2, y: view.y - (h - view.h) / 2, w, h, pixels: rect.width};
}

function drawTiles() {
  // Every level up to the one sharp at this zoom, for the part of the world on screen.
  const shown = onScreen();
  const wanted = (world.w / shown.w) * shown.pixels * (window.devicePixelRatio || 1);
  let sharp = tiles.levels.findIndex(([width]) => width >= wanted);
  if (sharp < 0) sharp = tiles.levels.length - 1;
  const keep = new Set();
  for (let level = 0; level <= sharp; level++) {
    if (!tileLevels[level]) {
      tileLevels[level] = document.createElementNS(SVG_NS, "g");
      tileLayer.append(tileLevels[level]);
    }
    const [width, height] = tiles.levels[level];
    const perX = world.w / width;  // world units a pixel of this level
    const perY = world.h / height;
    const span = (from, to, origin, per, count) => [
      Math.max(0, Math.floor((from - origin) / (per * tiles.size))),
      Math.min(count - 1, Math.floor((to - origin) / (per * tiles.size))),
    ];
    const [left, right] = span(shown.x, shown.x + shown.w, world.x, perX,
      Math.ceil(width / tiles.size));
    const [top, bottom] = span(shown.y, shown.y + shown.h, world.y, perY,
      Math.ceil(height / tiles.size));
    for (let column = left; column <= right; column++) {
      for (let row = top; row <= bottom; row++) {
        const key = `${level}/${column}_${row}`;
        keep.add(key);
        if (tileImages.has(key)) continue;
        const image = document.createElementNS(SVG_NS, "image");
        const across = Math.min(tiles.size + tiles.overlap, width - column * tiles.size);
        const down = Math.min(tiles.size + tiles.overlap, height - row * tiles.size);
        image.setAttribute("href", `${tiles.url}/${key}.webp`);
        image.setAttribute("x", world.x + column * tiles.size * perX);
        image.setAttribute("y", world.y + row * tiles.size * perY);
        image.setAttribute("width", across * perX);
        image.setAttribute("height", down * perY);
        image.setAttribute("preserveAspectRatio", "none");
        image.dataset.level = level;
        tileImages.set(key, image);
      }
    }
  }
  for (const [key, image] of tileImages) {
    if (!keep.has(key)) image.remove();
    else if (!image.isConnected) tileLevels[image.dataset.level].append(image);
  }
}

function markerScale() {
  // On the game's map, markers are as big as a kilometre is long on screen allows -- the square
  // root of it, within bounds, as on the header's map; on a grid, they're sized for the first
  // view and grow by the square root of the zoom. Either way, close up they don't cover the map.
  const perPixel = onScreen().w / onScreen().pixels;  // world units a screen pixel
  if (world) {
    const pixelsPerKm = 100000 / perPixel;
    const size = Math.min(Math.max(Math.sqrt(pixelsPerKm / 400), 0.6), 1.2);
    return (16 * size * perPixel) / markerUnit;  // a marker unit 16 screen pixels at full size
  }
  const zoom = initialView.w / view.w;
  return Math.min(Math.max(Math.sqrt(zoom), 0.5), 1.6) / zoom;
}

function applyView() {
  svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
  const scale = markerScale();
  for (const [group, x, y] of markers) {
    group.setAttribute("transform", `translate(${x} ${y}) scale(${scale}) translate(${-x} ${-y})`);
  }
  if (tileLayer) drawTiles();
}

function toSvgPoint(clientX, clientY) {
  const rect = svg.getBoundingClientRect();
  return {
    x: view.x + (clientX - rect.left) * (view.w / rect.width),
    y: view.y + (clientY - rect.top) * (view.h / rect.height),
  };
}

svg.addEventListener("wheel", (event) => {
  event.preventDefault();
  const point = toSvgPoint(event.clientX, event.clientY);
  const factor = event.deltaY > 0 ? 1.15 : 1 / 1.15;
  const newWidth = Math.min(Math.max(view.w * factor, minWidth()), maxWidth);
  const newHeight = newWidth * (initialView.h / initialView.w);
  view.x = point.x - (point.x - view.x) * (newWidth / view.w);
  view.y = point.y - (point.y - view.y) * (newHeight / view.h);
  view.w = newWidth;
  view.h = newHeight;
  applyView();
}, {passive: false});

let drag = null;
// The browser's own dragging of an image (a tile, an icon) or selecting of text would take the
// press over from the map's drag.
svg.addEventListener("dragstart", (event) => event.preventDefault());
svg.addEventListener("mousedown", (event) => { if (event.button === 0) event.preventDefault(); });
svg.addEventListener("pointerdown", (event) => {
  drag = {startX: event.clientX, startY: event.clientY, viewX: view.x, viewY: view.y};
  svg.setPointerCapture(event.pointerId);
  svg.classList.add("dragging");
});
svg.addEventListener("pointermove", (event) => {
  if (!drag) return;
  const rect = svg.getBoundingClientRect();
  view.x = drag.viewX - (event.clientX - drag.startX) * (view.w / rect.width);
  view.y = drag.viewY - (event.clientY - drag.startY) * (view.h / rect.height);
  applyView();
});
function endDrag() { drag = null; svg.classList.remove("dragging"); }
svg.addEventListener("pointerup", endDrag);
svg.addEventListener("pointercancel", endDrag);
svg.addEventListener("pointerleave", endDrag);
svg.addEventListener("dblclick", () => { view = {...initialView}; applyView(); });
new ResizeObserver(() => applyView()).observe(svg);
"""
