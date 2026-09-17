"""Renders resource nodes, existing buildings, and recommended locations on the map
(implementation.md Stage 14).

Split the same way as `graph_presentation`: `build_markers` is a pure, unit-testable transform
(contracts in, marker specs out); `render_page` lays those markers out as an SVG, panned and
zoomed by `MAP_SCRIPT` moving the SVG's own `viewBox` -- no D3 needed, since it's just a fixed
set of markers rather than a force simulation. There's no real map texture yet (per Stage 3, full
map data can be filled in later) — the SVG background is a placeholder grid standing in for the
in-game map image.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pioneer.contracts import FactorySite, PlacementRecord, Purity, RankedLocation, ResourceNode

MarkerKind = Literal["resource", "existing_building", "recommended", "factory"]

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


@dataclass(frozen=True)
class MapMarker:
    x: float
    y: float
    kind: MarkerKind
    label: str
    color: str
    rank: int | None = None


def build_markers(
    resource_nodes: tuple[ResourceNode, ...] = (),
    placements: tuple[PlacementRecord, ...] = (),
    ranked_locations: tuple[RankedLocation, ...] = (),
    names: Mapping[str, str] | None = None,
    factory_sites: tuple[FactorySite, ...] = (),
) -> tuple[MapMarker, ...]:
    """`names` maps item, recipe and building ids to the names labels show; an id without one
    falls back to `readable_id`, never to the raw class id. A factory site is labelled with its id
    and the recipes most of its buildings run."""

    def name(class_id: str) -> str:
        return (names or {}).get(class_id) or readable_id(class_id)

    markers = [
        MapMarker(
            x=node.position.x,
            y=node.position.y,
            kind="resource",
            label=f"{name(node.item_id)} ({node.purity.value})",
            color=_PURITY_COLOR[node.purity],
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
        )
        for placement in placements
    ]
    # Which resource a recommended deposit holds comes from the node it points at: on a real
    # save's map the resource pins are past `label_limit` and so unlabelled, leaving a green
    # "#1 pure" that never says what the player would be mining there.
    resource_of = {node.node_id: node.item_id for node in resource_nodes}
    markers += [
        MapMarker(
            x=location.position.x,
            y=location.position.y,
            kind="recommended",
            label=f"#{rank} {_recommended_resource(location, resource_of, name)}"
            f"{location.purity.value}, score {location.score:.2f}",
            color=_RECOMMENDED_COLOR,
            rank=rank,
        )
        for rank, location in enumerate(
            sorted(ranked_locations, key=lambda loc: -loc.score), start=1
        )
    ]
    markers += [
        MapMarker(
            x=site.position.x,
            y=site.position.y,
            kind="factory",
            label=f"{site.site_id}: {_main_recipes(site, name)}",
            color=_FACTORY_COLOR,
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


def _main_recipes(site: FactorySite, name: Callable[[str], str], shown: int = 3) -> str:
    counts = Counter(p.recipe_id for p in site.placements if p.recipe_id is not None)
    return ", ".join(name(recipe_id) for recipe_id, _ in counts.most_common(shown))


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
}
"""Marker radius as a fraction of `unit` (see `render_page`) — sized relative to the view box
rather than a fixed pixel count, since game coordinates can range from small fixture numbers up
to the real map's much larger scale."""


def _svg_marker(marker: MapMarker, *, unit: float, show_label: bool) -> str:
    radius = _MARKER_RADIUS_FACTOR[marker.kind] * unit
    stroke_width = max(unit * 0.06, 0.5)
    shape = (
        f'<rect x="{marker.x - radius}" y="{marker.y - radius}" width="{radius * 2}" '
        f'height="{radius * 2}" fill="{marker.color}" stroke="#0b0f14" '
        f'stroke-width="{stroke_width}" />'
        if marker.kind in ("existing_building", "factory")
        else f'<circle cx="{marker.x}" cy="{marker.y}" r="{radius}" fill="{marker.color}" '
        f'stroke="#0b0f14" stroke-width="{stroke_width}" />'
    )
    badge = (
        f'<text x="{marker.x}" y="{marker.y + unit * 0.13}" text-anchor="middle" class="badge" '
        f'style="font-size:{unit:g}px">{marker.rank}</text>'
        if marker.rank is not None
        else ""
    )
    label_style = f"font-size:{unit:g}px;stroke-width:{unit * 0.25:g}px"
    # A recommended location's position is typically the same resource node it recommends (per
    # Location Advisor), so its label goes *above* the marker rather than below — otherwise it'd
    # collide with the resource node's own label sitting right underneath at the same coordinates.
    above = marker.kind in ("recommended", "factory")
    label_y = marker.y - radius - unit if above else marker.y + radius + unit
    label = (
        f'<text x="{marker.x}" y="{label_y}" text-anchor="middle" class="label" '
        f'style="{label_style}">{html.escape(marker.label)}</text>'
        if show_label
        else ""
    )
    return (
        f'<g class="marker marker-{marker.kind}">'
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
) -> str:
    """Every marker keeps its label as a hover tooltip; past `label_limit` markers only the
    recommended sites and factories are also labelled on the map — a real map's hundreds of nodes
    would otherwise bury the pins under text."""
    markers = build_markers(resource_nodes, placements, ranked_locations, names, factory_sites)
    label_everything = len(markers) <= label_limit
    min_x, min_y, w, h = compute_view_box(markers)
    unit = max(w, h) / 45
    grid = _svg_grid(min_x, min_y, w, h, unit=unit)
    markers_svg = "".join(
        _svg_marker(
            m, unit=unit, show_label=label_everything or m.kind in ("recommended", "factory")
        )
        for m in markers
    )
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
{markers_svg}
</svg>
<div id="legend">
  <span><i class="dot" style="background:{_PURITY_COLOR[Purity.PURE]}"></i> pure node</span>
  <span><i class="dot" style="background:{_PURITY_COLOR[Purity.NORMAL]}"></i> normal node</span>
  <span><i class="dot" style="background:{_PURITY_COLOR[Purity.IMPURE]}"></i> impure node</span>
  <span><i class="sq" style="background:{_EXISTING_COLOR}"></i> existing building</span>
  <span><i class="dot" style="background:{_RECOMMENDED_COLOR}"></i> recommended</span>
  <span><i class="sq" style="background:{_FACTORY_COLOR}"></i> factory to extend</span>
</div>
<script>
const initialView = {{x: {min_x}, y: {min_y}, w: {w}, h: {h}}};
{MAP_SCRIPT}
</script>
</body>
</html>
"""


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
html, body { margin:0; height:100%; background:#0b0f14; font-family: system-ui, sans-serif; }
svg { width:100%; height:100%; display:block; cursor:grab; touch-action:none; }
svg.dragging { cursor:grabbing; }
.grid-line { stroke:#1c2733; }
.label { fill:#c7ced6; paint-order: stroke; stroke:#0b0f14; }
.badge { fill:#0b0f14; font-weight:bold; }
#legend {
  position:fixed; top:12px; left:12px; display:flex; flex-wrap:wrap; gap:14px;
  color:#c7ced6; font-size:12px; background:#131a22cc; padding:8px 12px;
  border-radius:8px; max-width: 90vw;
}
#legend .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }
#legend .sq { display:inline-block; width:10px; height:10px; margin-right:6px; }
"""

MAP_SCRIPT = """
const svg = document.getElementById("map");
let view = {...initialView};
const minWidth = initialView.w * 0.04;
const maxWidth = initialView.w * 6;

function applyView() {
  svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
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
  const newWidth = Math.min(Math.max(view.w * factor, minWidth), maxWidth);
  const newHeight = newWidth * (initialView.h / initialView.w);
  view.x = point.x - (point.x - view.x) * (newWidth / view.w);
  view.y = point.y - (point.y - view.y) * (newHeight / view.h);
  view.w = newWidth;
  view.h = newHeight;
  applyView();
}, {passive: false});

let drag = null;
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
"""
