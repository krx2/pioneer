"""Graph presentation module (implementation.md Stage 13).

Renders a `ProductionGraph` as an interactive D3.js node/flow diagram. Depends only on
`pioneer.contracts`; tested against fixture graphs copied from Production Planner examples.
"""

from pioneer.graph_presentation.renderer import graph_to_d3_data, render_page

__all__ = ["graph_to_d3_data", "render_page"]
