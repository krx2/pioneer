"""Map presentation module (implementation.md Stage 14).

Renders resource nodes, existing buildings, and recommended locations on the static map. Depends
only on `pioneer.contracts`; tested against fixture data.
"""

from pioneer.map_presentation.renderer import (
    MapBackground,
    MapMarker,
    MapTileSet,
    build_markers,
    compute_view_box,
    readable_id,
    render_page,
)

__all__ = [
    "MapBackground",
    "MapMarker",
    "MapTileSet",
    "build_markers",
    "compute_view_box",
    "readable_id",
    "render_page",
]
