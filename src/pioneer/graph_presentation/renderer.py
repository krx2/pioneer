"""Renders a `ProductionGraph` as an interactive D3.js node/flow diagram (implementation.md
Stage 13).

Split in two halves, per architecture.md §3's deterministic/LLM-path split applied to rendering:
`graph_to_d3_data` is a pure, fully unit-testable transform (contract in, JSON-shaped dict out) —
including a longest-path `layer` per node, and every node's place in a left-to-right layout with
few crossing lines (layout.py), worked out here rather than in the browser so it can be tested;
`render_page` wraps that data in a standalone HTML page that loads D3 from a CDN to draw it, zoom
and pan it, and let the player drag a node aside. Also distinguishes existing vs. new nodes, as
required by the Stage 13 contract — and existing nodes an expansion extends, labelled with how
many of their machines are new.
"""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from typing import Any

from pioneer.contracts import ProductionGraph
from pioneer.graph_presentation.layout import layered_layout

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
    products: Mapping[str, tuple[str, float]] | None = None,
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
    stage. Without `raw_resources` every input counts as raw, as none can be told apart.

    Every node's `lines` are the three shown under it: what it makes, the building and how many
    (a boundary: where the item comes from or goes), and how much a minute. `products` maps a
    recipe to its main product and what one machine makes of it a minute; without an entry the
    first line is the recipe's name and the third what the node's flows carry away. `x`/`y` and
    each link's `points` place it all left to right with few crossings -- see layout.py."""

    def name(class_id: str) -> str:
        return (names or {}).get(class_id) or readable_id(class_id)

    def icon(*class_ids: str) -> str | None:
        return next((icons[i] for i in class_ids if icons and i in icons), None)

    nodes: dict[str, dict[str, Any]] = {}
    for node in graph.nodes:
        added = node.machine_count - node.existing_machine_count
        extended = node.is_existing and node.existing_machine_count > 0 and added > 0
        # A save's counts are clock speeds summed: 4.6666667 machines reads as 4.67.
        count = f"×{_amount(node.machine_count)}" + (
            f" (+{_amount(added)} new)" if extended else ""
        )
        product = (products or {}).get(node.recipe_id)
        nodes[node.node_id] = {
            "id": node.node_id,
            "label": f"{name(node.recipe_id)} {count}",
            "lines": [
                name(product[0]) if product else name(node.recipe_id),
                f"{name(node.building_id)} {count}",
                f"{_amount(product[1] * node.machine_count * node.production_boost)}/min"
                if product
                else "",
            ],
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
                "lines": [name(flow.item_id), _source_of(flow.item_id, raw_resources), ""],
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
                "lines": [name(flow.item_id), "output", ""],
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

    carried: dict[str, float] = defaultdict(float)  # what a node's flows take out, or bring in
    for link in links:
        carried[link["source"]] += link["ratePerMinute"]
        if nodes[link["target"]]["kind"] == "boundary-out":
            carried[link["target"]] += link["ratePerMinute"]
    for node_id, node in nodes.items():
        if not node["lines"][2] and carried[node_id] > 0:
            node["lines"][2] = f"{_amount(carried[node_id])}/min"

    layout = layered_layout(list(nodes), [(link["source"], link["target"]) for link in links])
    for node_id, node in nodes.items():
        node["x"], node["y"] = layout.positions[node_id]
    for link in links:
        edge = (link["source"], link["target"])
        link["points"] = [list(point) for point in layout.waypoints.get(edge, [])]
        link["back"] = edge in layout.back_edges

    return {"nodes": list(nodes.values()), "links": links}


def _amount(value: float) -> str:
    """`4.6666667` -> `4.67`, `70.0` -> `70`."""
    return f"{round(value, 2):g}"


def _source_of(item_id: str, raw_resources: Collection[str] | None) -> str:
    return "raw resource" if raw_resources is None or item_id in raw_resources else "brought in"


def render_page(
    graph: ProductionGraph,
    *,
    title: str = "Production Graph",
    names: Mapping[str, str] | None = None,
    icons: Mapping[str, str] | None = None,
    raw_resources: Collection[str] | None = None,
    products: Mapping[str, tuple[str, float]] | None = None,
) -> str:
    data_json = json.dumps(graph_to_d3_data(graph, names, icons, raw_resources, products))
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
.node .line-0 { font-weight:600; }
.node .line-1 { fill:#b9c3c9; }
.node .line-2 { fill:#d3dade; }
.link { fill:none; stroke:#56636c; stroke-width:1.6px; stroke-opacity:0.9; }
.link.back { stroke-dasharray:5 4; }
.arrow { fill:#56636c; }
.link-label { fill:#9fabb3; font-size:9.5px; paint-order: stroke; stroke:#1f2427;
  stroke-width:3px; }
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
const container = svg.append("g");
const zoom = d3.zoom().scaleExtent([0.15, 3]).on("zoom", (event) => {
  container.attr("transform", event.transform);
});
svg.call(zoom);

const NODE_RADIUS = 10;
const ICON_RADIUS = 17;  // a node with an icon: room for a 24px icon inside its ring
const ICON_SIZE = 24;
const LINE_HEIGHT = 13;
const radius = d => d.icon ? ICON_RADIUS : NODE_RADIUS;

// Positions come with the data: laid out in columns left to right, few lines crossing (see
// layout.py). Links pass through their waypoints; one flagged `back` closes a loop, so it runs
// right to left, curving under the two nodes it joins.
const byId = new Map(data.nodes.map(d => [d.id, d]));
const seen = new Map();
data.links.forEach(l => {
  l.source = byId.get(l.source);
  l.target = byId.get(l.target);
  const pair = `${l.source.id}\u0000${l.target.id}`;
  l.pairIndex = seen.get(pair) || 0;  // several items between the same two nodes: labels stack
  seen.set(pair, l.pairIndex + 1);
});

svg.append("defs").append("marker")
  .attr("id", "arrow").attr("viewBox", "0 0 10 10").attr("refX", 9).attr("refY", 5)
  .attr("markerWidth", 7).attr("markerHeight", 7).attr("orient", "auto-start-reverse")
  .append("path").attr("d", "M0,0 L10,5 L0,10 z").attr("class", "arrow");

const bump = d3.line().curve(d3.curveBumpX);

function linkPath(l) {
  const s = l.source, t = l.target;
  if (l.back) {
    const drop = 70 + Math.abs(s.x - t.x) * 0.12;
    const sy = s.y + radius(s), ty = t.y + radius(t);
    return `M${s.x},${sy} C${s.x},${sy + drop} ${t.x},${ty + drop} ${t.x},${ty + 2}`;
  }
  return bump([[s.x + radius(s), s.y], ...l.points, [t.x - radius(t) - 2, t.y]]);
}

const link = container.append("g").selectAll("path")
  .data(data.links).join("path")
  .attr("class", d => d.back ? "link back" : "link")
  .attr("marker-end", "url(#arrow)");

const linkLabel = container.append("g").selectAll("text")
  .data(data.links).join("text").attr("class", "link-label").attr("text-anchor", "middle")
  .text(d => `${d.itemName} ${+d.ratePerMinute.toFixed(2)}/min`);

function nodeKind(d) {
  if (d.kind === "boundary-out") return "node-output";
  // An input: grey if it comes out of the ground, else a part the player already makes.
  if (d.kind === "boundary-in") return d.raw ? "node-raw" : "node-existing";
  if (d.extended) return "node-extended";
  return d.existing ? "node-existing" : "node-new";
}
const nodeClass = d => `node ${nodeKind(d)}${d.icon ? " has-icon" : ""}`;

const node = container.append("g").selectAll("g")
  .data(data.nodes).join("g")
  .attr("class", nodeClass)
  .call(d3.drag().on("drag", (event, d) => { d.x = event.x; d.y = event.y; draw(); }));

node.append("title").text(d => d.label);
node.append("circle").attr("r", radius);
// An extended node's edge: blue dashes over its orange one. pathLength splits the circle into 12
// equal parts whatever its radius, so the two colours take turns evenly and meet where they start.
node.filter(d => d.extended).append("circle").attr("class", "dash").attr("r", radius)
  .attr("pathLength", 12);
node.filter(d => d.icon).append("image").attr("href", d => d.icon)
  .attr("x", -ICON_SIZE / 2).attr("y", -ICON_SIZE / 2)
  .attr("width", ICON_SIZE).attr("height", ICON_SIZE);
// Three lines under each node: what it makes, the building and how many, and how much a minute.
node.append("text").attr("text-anchor", "middle").attr("y", d => radius(d) + 14)
  .selectAll("tspan")
  .data(d => d.lines.filter(line => line).map((line, i) => ({line, i})))
  .join("tspan")
  .attr("x", 0).attr("dy", d => d.i ? LINE_HEIGHT : 0).attr("class", d => `line-${d.i}`)
  .text(d => d.line);

function draw() {
  node.attr("transform", d => `translate(${d.x},${d.y})`);
  link.attr("d", linkPath);
  const paths = link.nodes();
  linkLabel.each(function (d, i) {
    const middle = paths[i].getPointAtLength(paths[i].getTotalLength() / 2);
    d3.select(this).attr("x", middle.x).attr("y", middle.y - 5 + d.pairIndex * 11);
  });
}
draw();

// Fit the whole graph into whatever size the page is shown at -- a resized window, or just as
// often the panel this page sits in -- clear of the legend along the top. The extent comes from
// the layout itself (nodes, waypoints, room for the labels), not from measuring the drawing, which
// in a frame still loading can come back short and leave the edges cut off.
const LABEL_HALF_WIDTH = 80, LABEL_DEPTH = 64, LOOP_DEPTH = 110;
function extent() {
  const points = [...data.nodes.map(d => [d.x, d.y]), ...data.links.flatMap(l => l.points)];
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const loops = data.links.some(l => l.back) ? LOOP_DEPTH : 0;
  const x = Math.min(...xs) - LABEL_HALF_WIDTH, y = Math.min(...ys) - ICON_RADIUS - 8;
  return {
    x, y,
    width: Math.max(...xs) + LABEL_HALF_WIDTH - x,
    height: Math.max(...ys) + Math.max(LABEL_DEPTH, loops) - y,
  };
}
function fit() {
  if (!data.nodes.length) return;
  const rect = svg.node().getBoundingClientRect();
  const width = rect.width || window.innerWidth;
  const height = rect.height || window.innerHeight;
  const box = extent();
  const top = 48;
  const scale = Math.min(1.2, (width - 24) / box.width, (height - top - 12) / box.height);
  const x = (width - box.width * scale) / 2 - box.x * scale;
  const y = top + (height - top - box.height * scale) / 2 - box.y * scale;
  svg.call(zoom.transform, d3.zoomIdentity.translate(x, y).scale(scale));
}
fit();
new ResizeObserver(fit).observe(svg.node());
window.addEventListener("resize", fit);
"""
