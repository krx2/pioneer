"""Renders resource nodes, existing buildings and recommended locations on the map.

`build_markers` is a pure transform to marker specs; `render_page` lays them out as an SVG in the
game's own coordinates, panned and zoomed by `MAP_SCRIPT` through its `viewBox`. Under the markers
lies the game's map (`MapBackground`), as tiles when it's been cut into them, else a grid.
Markers are resized with the zoom, so up close they don't cover what they mark.
"""

from __future__ import annotations

import html
import json
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pioneer.common import html_document, readable_id, static_path, static_text
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
"""Marker radius as a fraction of `unit` (see `render_page`), so markers scale with the view box."""


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
    map_view = MAP_VIEW_SCRIPT.read_text(encoding="utf-8")
    view_box = f"{min_x} {min_y} {w} {h}"
    body = f"""<svg id="map" viewBox="{view_box}" preserveAspectRatio="xMidYMid meet">
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
{map_view}
const initialView = {{x: {min_x}, y: {min_y}, w: {w}, h: {h}}};
const worldWidth = {world_width};
const markerUnit = {unit};
const world = {json.dumps(world)};
const tiles = {json.dumps(tiles)};
{MAP_SCRIPT}
</script>"""
    return html_document(title, body, head=f"<style>{STYLE}</style>")


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


STYLE = static_text(__file__, "map.css")

MAP_SCRIPT = static_text(__file__, "map.js")

MAP_VIEW_SCRIPT = static_path(__file__, "map-view.js")
"""Panning, zooming and the map's tiles, which the web UI's Map panel uses too."""
