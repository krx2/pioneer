"""Renders resource nodes, existing buildings, and recommended locations on the static map
(implementation.md Stage 14).

Split the same way as `graph_presentation`: `build_markers` is a pure, unit-testable transform
(contracts in, marker specs out); `render_page` lays those markers out as an SVG. There's no real
map texture yet (per Stage 3, full map data can be filled in later) — the SVG background is a
placeholder grid standing in for the in-game map image.
"""

from __future__ import annotations

import html
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pioneer.contracts import FactorySite, PlacementRecord, Purity, RankedLocation, ResourceNode

MarkerKind = Literal["resource", "existing_building", "recommended", "factory"]

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
    """`names` maps item, recipe and building ids to the names labels show; an id without one is
    shown as it is. A factory site is labelled with its id and the recipes most of its buildings
    run."""

    def name(class_id: str) -> str:
        return (names or {}).get(class_id, class_id)

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
    markers += [
        MapMarker(
            x=location.position.x,
            y=location.position.y,
            kind="recommended",
            label=f"#{rank} {location.purity.value}, score {location.score:.2f}",
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
<svg viewBox="{min_x} {min_y} {w} {h}" preserveAspectRatio="xMidYMid meet">
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
svg { width:100%; height:100%; display:block; }
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
