"""Renders a `ProductionGraph` as an interactive D3.js node/flow diagram (implementation.md
Stage 13).

Split in two halves, per architecture.md §3's deterministic/LLM-path split applied to rendering:
`graph_to_d3_data` is a pure, fully unit-testable transform (contract in, JSON-shaped dict out) —
including a longest-path `layer` per node, so depth in the graph is known independent of any
rendering choice; `render_page` wraps that data in a standalone HTML page that loads D3 from a CDN
and lays the graph out on the diagonal of a square board: raw inputs (layer 0) pinned near the
top-left corner, the final output (deepest layer) pinned near the bottom-right corner, everything
else free to spread across the square rather than being squeezed into a single column or row.
Also distinguishes existing vs. new nodes, as required by the Stage 13 contract.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable
from typing import Any

from pioneer.contracts import ProductionGraph

_D3_CDN_URL = "https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"


def _boundary_id(direction: str, item_id: str) -> str:
    return f"__{direction}__{item_id}"


def _compute_layers(node_ids: Iterable[str], links: list[dict[str, Any]]) -> dict[str, int]:
    """Longest-path layering: a node's layer is one more than the deepest of its predecessors',
    so material always flows left-to-right — raw-input boundary nodes (no predecessors) land in
    layer 0, and every downstream node lands strictly to the right of everything that feeds it.
    Relaxed Bellman-Ford style instead of a topological sort so a malformed (cyclic) fixture can't
    hang this in an infinite loop — it just stops improving after at most `len(node_ids)` passes.
    """
    predecessors: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for link in links:
        predecessors.setdefault(link["target"], set()).add(link["source"])

    layer = dict.fromkeys(predecessors, 0)
    for _ in range(len(predecessors)):
        changed = False
        for node_id, preds in predecessors.items():
            if not preds:
                continue
            candidate = max(layer[p] for p in preds) + 1
            if candidate > layer[node_id]:
                layer[node_id] = candidate
                changed = True
        if not changed:
            break
    return layer


def graph_to_d3_data(graph: ProductionGraph) -> dict[str, Any]:
    """Machine nodes come straight from `graph.nodes`. Flows with no `source_node_id` (raw input)
    or no `target_node_id` (final output) get a synthesized boundary node instead of a dangling
    edge, so every link in the output has two real endpoints — D3 can't draw an edge to nothing."""
    nodes: dict[str, dict[str, Any]] = {
        node.node_id: {
            "id": node.node_id,
            "label": f"{node.recipe_id} ×{node.machine_count:g}",
            "buildingId": node.building_id,
            "existing": node.is_existing,
            "kind": "machine",
        }
        for node in graph.nodes
    }

    links: list[dict[str, Any]] = []
    for flow in graph.flows:
        source = flow.source_node_id or _boundary_id("in", flow.item_id)
        target = flow.target_node_id or _boundary_id("out", flow.item_id)
        if flow.source_node_id is None and source not in nodes:
            nodes[source] = {
                "id": source,
                "label": f"{flow.item_id} (raw input)",
                "buildingId": None,
                "existing": True,
                "kind": "boundary-in",
            }
        if flow.target_node_id is None and target not in nodes:
            nodes[target] = {
                "id": target,
                "label": f"{flow.item_id} (output)",
                "buildingId": None,
                "existing": True,
                "kind": "boundary-out",
            }
        links.append(
            {
                "source": source,
                "target": target,
                "itemId": flow.item_id,
                "ratePerMinute": flow.amount_per_minute,
            }
        )

    layers = _compute_layers(nodes.keys(), links)
    for node_id, node in nodes.items():
        node["layer"] = layers[node_id]

    return {"nodes": list(nodes.values()), "links": links}


def render_page(graph: ProductionGraph, *, title: str = "Production Graph") -> str:
    data_json = json.dumps(graph_to_d3_data(graph))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<script src="{_D3_CDN_URL}"></script>
<style>{STYLE}</style>
</head>
<body>
<svg id="graph"></svg>
<div id="legend">
  <span><i class="dot existing"></i> existing</span>
  <span><i class="dot new"></i> new</span>
  <span><i class="dot boundary"></i> raw / output</span>
</div>
<script>
const data = {data_json};
{GRAPH_SCRIPT}
</script>
</body>
</html>
"""


STYLE = """
:root { color-scheme: dark; }
html, body { margin:0; height:100%; background:#0b0f14; font-family: system-ui, sans-serif; }
svg { width:100%; height:100%; display:block; }
.node-existing circle { fill:#5b6b7c; stroke:#aab6c2; }
.node-new circle { fill:#2f6fed; stroke:#9db8f7; }
.node-boundary circle { fill:#0b0f14; stroke:#6b7683; stroke-dasharray:3 2; }
.node text { fill:#e8eaed; font-size:11px; paint-order: stroke; stroke:#0b0f14; stroke-width:3px; }
.link { stroke:#3a4552; stroke-opacity:0.8; }
.link-label { fill:#9aa4af; font-size:9px; }
#legend { position:fixed; top:12px; left:12px; display:flex; gap:16px; color:#c7ced6;
  font-size:12px; background:#131a22cc; padding:8px 12px; border-radius:8px; }
#legend .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }
#legend .existing { background:#5b6b7c; }
#legend .new { background:#2f6fed; }
#legend .boundary { background:#0b0f14; border:1px dashed #6b7683; }
"""

GRAPH_SCRIPT = """
const svg = d3.select("#graph");
const width = window.innerWidth, height = window.innerHeight;
const container = svg.append("g");
svg.call(d3.zoom().scaleExtent([0.2, 4]).on("zoom", (event) => {
  container.attr("transform", event.transform);
}));

// Lay the DAG out on the diagonal of a square board: raw inputs (layer 0) pinned near the
// top-left corner, the final output (the deepest layer) pinned near the bottom-right corner,
// everything in between free to spread across the full square rather than being squeezed into a
// single column or row.
const margin = 60;
const boardSide = Math.min(width, height) - margin * 2;
const maxLayer = d3.max(data.nodes, d => d.layer) || 0;

data.nodes.forEach(d => {
  const t = maxLayer > 0 ? d.layer / maxLayer : 0;
  d.diagTarget = margin + t * boardSide;
  d.x = d.diagTarget + (Math.random() - 0.5) * boardSide * 0.4;
  d.y = d.diagTarget + (Math.random() - 0.5) * boardSide * 0.4;
});

// Custom force: pulls only the projection of a node's position onto the (1,1) diagonal toward
// its target — moving x and y by the same amount, each tick — and leaves the perpendicular
// component (how far a node sits off the diagonal) entirely alone. That's what lets same-layer
// nodes fan out and use the square instead of collapsing onto a single point on the line.
function diagonalPull(strengthFn) {
  let nodes;
  function force(alpha) {
    for (const d of nodes) {
      const delta = (d.diagTarget - (d.x + d.y) / 2) * strengthFn(d) * alpha;
      d.vx += delta;
      d.vy += delta;
    }
  }
  force.initialize = (_nodes) => { nodes = _nodes; };
  return force;
}

// Endpoints are pinned firmly to their corner; everything else only gets a gentle nudge to stay
// roughly on-course, so charge/collide are free to spread it across the board.
const diagonalStrength = (d) => (d.layer === 0 || d.layer === maxLayer ? 0.85 : 0.1);

const simulation = d3.forceSimulation(data.nodes)
  .force("link", d3.forceLink(data.links).id(d => d.id).strength(0.1))
  .force("charge", d3.forceManyBody().strength(-160))
  .force("collide", d3.forceCollide(36))
  .force("diagonal", diagonalPull(diagonalStrength));

const link = container.append("g").selectAll("line")
  .data(data.links).join("line").attr("class", "link").attr("stroke-width", 1.5);

const linkLabel = container.append("g").selectAll("text")
  .data(data.links).join("text").attr("class", "link-label")
  .text(d => `${d.itemId} ${d.ratePerMinute}/min`);

function nodeClass(d) {
  if (d.kind !== "machine") return "node node-boundary";
  return d.existing ? "node node-existing" : "node node-new";
}

function dragStart(event, d) {
  if (!event.active) simulation.alphaTarget(0.3).restart();
  d.fx = d.x; d.fy = d.y;
}
function dragMove(event, d) { d.fx = event.x; d.fy = event.y; }
function dragEnd(event, d) {
  if (!event.active) simulation.alphaTarget(0);
  d.fx = null; d.fy = null;
}

const node = container.append("g").selectAll("g")
  .data(data.nodes).join("g")
  .attr("class", nodeClass)
  .call(d3.drag().on("start", dragStart).on("drag", dragMove).on("end", dragEnd));

node.append("circle").attr("r", d => d.kind === "machine" ? 22 : 10);
node.append("text").attr("text-anchor", "middle")
  .attr("dy", d => d.kind === "machine" ? 38 : 22)
  .text(d => d.label);

simulation.on("tick", () => {
  data.nodes.forEach(d => {
    d.x = Math.max(margin, Math.min(margin + boardSide, d.x));
    d.y = Math.max(margin, Math.min(margin + boardSide, d.y));
  });
  link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
      .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
  linkLabel
    .attr("x", d => (d.source.x + d.target.x) / 2)
    .attr("y", d => (d.source.y + d.target.y) / 2);
  node.attr("transform", d => `translate(${d.x},${d.y})`);
});
"""
