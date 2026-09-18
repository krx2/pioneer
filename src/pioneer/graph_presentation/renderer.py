"""Renders a `ProductionGraph` as an interactive D3.js node/flow diagram (implementation.md
Stage 13).

Split in two halves, per architecture.md §3's deterministic/LLM-path split applied to rendering:
`graph_to_d3_data` is a pure, fully unit-testable transform (contract in, JSON-shaped dict out) —
including a longest-path `layer` per node, so depth in the graph is known independent of any
rendering choice, even though the current layout doesn't use it; `render_page` wraps that data in
a standalone HTML page that loads D3 from a CDN and lets it settle into a plain force-directed
layout -- charge, link, and collision forces only, nothing pinning a node to a corner or a line --
so the shape it settles into is whatever its own connectivity naturally suggests. Also
distinguishes existing vs. new nodes, as required by the Stage 13 contract — and existing nodes an
expansion extends, labelled with how many of their machines are new.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Collection, Iterable, Mapping
from typing import Any

from pioneer.contracts import ProductionGraph

_D3_CDN_URL = "https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"

_CLASS_ID_AFFIX = re.compile(r"^(?:Desc|Recipe|Build|BP|Schematic|Research)_|_C$")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def readable_id(class_id: str) -> str:
    """`Desc_IronPlate_C` -> `Iron Plate`. What a label falls back to for an id the knowledge base
    has no name for -- a modded save, or none loaded at all. The player is reading a diagram of
    their factory, so even the fallback should name the thing rather than show them a class id."""
    words = _CAMEL_BOUNDARY.sub(" ", _CLASS_ID_AFFIX.sub("", class_id)).replace("_", " ")
    return " ".join(words.split()) or class_id


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


def graph_to_d3_data(
    graph: ProductionGraph,
    names: Mapping[str, str] | None = None,
    icons: Mapping[str, str] | None = None,
    raw_resources: Collection[str] | None = None,
) -> dict[str, Any]:
    """Machine nodes come straight from `graph.nodes`. A flow endpoint that isn't one of them —
    `None` (material entering from outside, or leaving as the final output), or an id no node in
    the graph has — gets a synthesized boundary node instead of a dangling edge, so every link in
    the output has two real endpoints: D3's `forceLink` throws on a link to an unknown node, which
    blanks the whole diagram. `names` maps recipe and item ids to the names labels show; an id
    without one falls back to `readable_id`, never to the raw class id. `icons` maps ids to icon
    URLs: a machine node shows its recipe's (else its building's), a boundary node its item's.

    An input is `raw` when its item is one of `raw_resources` — what miners and extractors take
    out of the ground — and otherwise a part the player already makes, drawn like an existing
    stage. Without `raw_resources` every input counts as raw, as none can be told apart."""

    def name(class_id: str) -> str:
        return (names or {}).get(class_id) or readable_id(class_id)

    def icon(*class_ids: str) -> str | None:
        return next((icons[i] for i in class_ids if icons and i in icons), None)

    nodes: dict[str, dict[str, Any]] = {}
    for node in graph.nodes:
        added = node.machine_count - node.existing_machine_count
        extended = node.is_existing and node.existing_machine_count > 0 and added > 0
        label = f"{name(node.recipe_id)} ×{node.machine_count:g}"
        nodes[node.node_id] = {
            "id": node.node_id,
            "label": f"{label} (+{added:g} new)" if extended else label,
            "buildingId": node.building_id,
            "existing": node.is_existing,
            "extended": extended,
            "kind": "machine",
            "icon": icon(node.recipe_id, node.building_id),
        }
    machine_ids = set(nodes)

    links: list[dict[str, Any]] = []
    for flow in graph.flows:
        source = (
            flow.source_node_id
            if flow.source_node_id in machine_ids
            else _boundary_id("in", flow.item_id)
        )
        target = (
            flow.target_node_id
            if flow.target_node_id in machine_ids
            else _boundary_id("out", flow.item_id)
        )
        if source not in nodes:
            nodes[source] = {
                "id": source,
                "label": f"{name(flow.item_id)} (input)",
                "buildingId": None,
                "existing": True,
                "kind": "boundary-in",
                "raw": raw_resources is None or flow.item_id in raw_resources,
                "icon": icon(flow.item_id),
            }
        if target not in nodes:
            nodes[target] = {
                "id": target,
                "label": f"{name(flow.item_id)} (output)",
                "buildingId": None,
                "existing": True,
                "kind": "boundary-out",
                "icon": icon(flow.item_id),
            }
        links.append(
            {
                "source": source,
                "target": target,
                "itemId": flow.item_id,
                "itemName": name(flow.item_id),
                "ratePerMinute": flow.amount_per_minute,
            }
        )

    layers = _compute_layers(nodes.keys(), links)
    for node_id, node in nodes.items():
        node["layer"] = layers[node_id]

    return {"nodes": list(nodes.values()), "links": links}


def render_page(
    graph: ProductionGraph,
    *,
    title: str = "Production Graph",
    names: Mapping[str, str] | None = None,
    icons: Mapping[str, str] | None = None,
    raw_resources: Collection[str] | None = None,
) -> str:
    data_json = json.dumps(graph_to_d3_data(graph, names, icons, raw_resources))
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
  <span><i class="dot raw"></i> raw resource</span>
  <span><i class="dot existing"></i> existing</span>
  <span><i class="dot extended"></i> extended</span>
  <span><i class="dot new"></i> new</span>
  <span><i class="dot output"></i> output</span>
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
html, body { margin:0; height:100%; background:#1f2427; font-family: system-ui, sans-serif; }
svg { width:100%; height:100%; display:block; }
/* Orange is what the player already has, blue is what this plan adds -- so an extended stage's
   edge alternates orange and blue (existing machines, more of them added): blue dashes laid over
   an orange edge. An input the plan takes in is grey if it comes out of the ground and orange if
   it's a part, which the player already makes; what the plan produces is green. Raw resources and
   output keep the dashed edge that marks a node as a boundary rather than a machine. */
.node-existing circle { fill:#e0812f; stroke:#f5bc84; }
.node-new circle { fill:#2f6fed; stroke:#9db8f7; }
.node-extended circle { fill:#e0812f; stroke:#e0812f; stroke-width:4px; }
.node.node-extended .dash { fill:none; stroke:#2f6fed; stroke-dasharray:1 1; }
.node-raw circle { fill:#5b6b7c; stroke:#aab6c2; stroke-dasharray:3 2; }
.node-output circle { fill:#33c17a; stroke:#8fe0b6; stroke-dasharray:3 2; }
.node text { fill:#edf1f3; font-size:11px; paint-order: stroke; stroke:#1f2427; stroke-width:3px; }
.link { stroke:#4a565e; stroke-opacity:0.8; }
.link-label { fill:#9fabb3; font-size:9px; }
#legend { position:fixed; top:12px; left:12px; display:flex; gap:16px; color:#d3dade;
  font-size:12px; background:#16191bcc; padding:8px 12px; border-radius:8px; }
#legend .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }
#legend .raw { background:#5b6b7c; border:1px dashed #aab6c2; }
#legend .existing { background:#e0812f; }
#legend .new { background:#2f6fed; }
#legend .extended { background:repeating-conic-gradient(#e0812f 0 45deg, #2f6fed 0 90deg); }
#legend .output { background:#33c17a; border:1px dashed #8fe0b6; }
/* A node with an icon keeps its colour as a ring around the icon. */
.node.has-icon circle { fill:#161a1d; stroke-width:3px; }
.node-existing.has-icon circle, .node-extended.has-icon circle { stroke:#e0812f; }
.node-new.has-icon circle { stroke:#2f6fed; }
.node-raw.has-icon circle { stroke:#aab6c2; }
.node-output.has-icon circle { stroke:#33c17a; }
"""

GRAPH_SCRIPT = """
const svg = d3.select("#graph");
let width = window.innerWidth, height = window.innerHeight;
const container = svg.append("g");
svg.call(d3.zoom().scaleExtent([0.2, 4]).on("zoom", (event) => {
  container.attr("transform", event.transform);
}));

const NODE_RADIUS = 10;
const ICON_RADIUS = 17;  // a node with an icon: room for a 24px icon inside its ring
const ICON_SIZE = 24;
const radius = d => d.icon ? ICON_RADIUS : NODE_RADIUS;

// A plain, natural force-directed layout: charge repels every node from every other, forceLink
// pulls connected nodes to a comfortable resting distance, forceCollide keeps circles from
// overlapping, and a gentle centering force just keeps the whole cluster roughly in view. Nothing
// pins a node to a corner or a diagonal -- the shape that emerges is whatever the graph's own
// connectivity settles into, rather than one imposed on it.
data.nodes.forEach(d => {
  d.x = width / 2 + (Math.random() - 0.5) * 80;
  d.y = height / 2 + (Math.random() - 0.5) * 80;
});

const simulation = d3.forceSimulation(data.nodes)
  .force("link", d3.forceLink(data.links).id(d => d.id).distance(110).strength(0.5))
  .force("charge", d3.forceManyBody().strength(-320))
  .force("collide", d3.forceCollide(d => radius(d) + NODE_RADIUS * 1.5))
  .force("center", d3.forceCenter(width / 2, height / 2));

const link = container.append("g").selectAll("line")
  .data(data.links).join("line").attr("class", "link").attr("stroke-width", 1.5);

const linkLabel = container.append("g").selectAll("text")
  .data(data.links).join("text").attr("class", "link-label")
  .text(d => `${d.itemName} ${+d.ratePerMinute.toFixed(2)}/min`);

function nodeKind(d) {
  if (d.kind === "boundary-out") return "node-output";
  // An input: grey if it comes out of the ground, else a part the player already makes.
  if (d.kind === "boundary-in") return d.raw ? "node-raw" : "node-existing";
  if (d.extended) return "node-extended";
  return d.existing ? "node-existing" : "node-new";
}
const nodeClass = d => `node ${nodeKind(d)}${d.icon ? " has-icon" : ""}`;

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

// Machine and boundary (raw input / final output) nodes are drawn the same size: the machine
// count and flow rate already carried in the label distinguish them, so a bigger circle for
// machines was just visual noise, not information.
node.append("circle").attr("r", radius);
// An extended node's edge: blue dashes over its orange one. pathLength splits the circle into 12
// equal parts whatever its radius, so the two colours take turns evenly and meet where they start.
node.filter(d => d.extended).append("circle").attr("class", "dash").attr("r", radius)
  .attr("pathLength", 12);
node.filter(d => d.icon).append("image").attr("href", d => d.icon)
  .attr("x", -ICON_SIZE / 2).attr("y", -ICON_SIZE / 2)
  .attr("width", ICON_SIZE).attr("height", ICON_SIZE);
node.append("text").attr("text-anchor", "middle").attr("dy", d => radius(d) + 16)
  .text(d => d.label);

simulation.on("tick", () => {
  link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
      .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
  linkLabel
    .attr("x", d => (d.source.x + d.target.x) / 2)
    .attr("y", d => (d.source.y + d.target.y) / 2);
  node.attr("transform", d => `translate(${d.x},${d.y})`);
});

// Re-center whenever the element this page is shown in actually changes size -- a resized browser
// window, but just as often a host page resizing the iframe/panel this graph lives in -- so the
// cluster keeps settling around the middle of whatever panel it's shown in, rather than drifting
// off toward wherever the center used to be.
function resize() {
  const rect = svg.node().getBoundingClientRect();
  const newWidth = rect.width || window.innerWidth;
  const newHeight = rect.height || window.innerHeight;
  if (newWidth === width && newHeight === height) return;
  width = newWidth;
  height = newHeight;
  simulation.force("center", d3.forceCenter(width / 2, height / 2));
  simulation.alpha(0.4).restart();
}
new ResizeObserver(resize).observe(svg.node());
"""
