"""Map presentation module (implementation.md Stage 14).

Renders resource nodes, existing buildings, and recommended locations on the static map. Depends
only on `pioneer.contracts`; tested against fixture data.
"""

from pioneer.map_presentation.renderer import (
    MapMarker,
    build_markers,
    compute_view_box,
    render_page,
)

__all__ = ["MapMarker", "build_markers", "compute_view_box", "render_page"]
