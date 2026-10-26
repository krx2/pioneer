"""Renders a `ProductionGraph` as an interactive D3.js node/flow diagram.

`graph_to_d3_data` is a pure transform to JSON-shaped data, layout included (layout.py), so it can
be tested outside the browser; `render_page` wraps it in a standalone page that draws it with D3.
Existing, extended and new nodes are drawn apart.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from typing import Any

from pioneer.common import html_document, readable_id, static_text
from pioneer.contracts import ProductionGraph
from pioneer.graph_presentation.layout import layered_layout

_D3_CDN_URL = "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"


def _boundary_id(direction: str, item_id: str) -> str:
    return f"__{direction}__{item_id}"


def _compute_layers(node_ids: Iterable[str], links: list[dict[str, Any]]) -> dict[str, int]:
    """Longest-path layering: a node's layer is one more than its deepest predecessor's. Relaxed
    Bellman-Ford style rather than a topological sort, so a cyclic graph can't loop forever."""
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
    """Nodes and links for D3. A flow endpoint that isn't a node (`None`, or an unknown id) gets a
    synthesized boundary node, since D3's `forceLink` throws on a link to an unknown node.

    `names` maps ids to labels (else `readable_id`); `icons` maps ids to icon URLs. An input is
    `raw` when its item is in `raw_resources`, else a part the player already makes; without
    `raw_resources` every input is raw. `products` maps a recipe to its main product and one
    machine's rate of it, for the node's three label `lines`. `x`/`y` and each link's `points`
    come from layout.py."""

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
    body = f"""<svg id="graph"></svg>
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
</script>"""
    head = f'<script src="{_D3_CDN_URL}"></script>\n<style>{STYLE}</style>'
    return html_document(title, body, head=head)


STYLE = static_text(__file__, "graph.css")

GRAPH_SCRIPT = static_text(__file__, "graph.js")
