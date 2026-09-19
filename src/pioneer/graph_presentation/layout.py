"""Lays a production graph out in columns, material flowing left to right, with as few crossing
lines as a quick heuristic manages -- the layered (Sugiyama) method, in five steps:

1. **Cycles set aside.** A factory feeding some of its output back in (packaged fuel and its
   empty canisters, recycled rubber and plastic) has no left-to-right order. The edges a
   depth-first walk from the inputs finds pointing back are laid out reversed, and drawn as
   curves back afterwards.
2. **Columns.** Each node goes one column right of the furthest node feeding it. A node fed by
   nothing that feeds something -- an input from outside -- moves up to just left of the first
   node it feeds, so its line stays short.
3. **Waypoints.** An edge across several columns gets a waypoint in each column it passes, which
   step 4 orders like a node: a long line then weaves between the nodes instead of through them.
4. **Order within columns.** Sweeps right and back left each sort every column by the average
   position of its nodes' neighbours in the column before (the barycenter heuristic); the order
   with the fewest crossings seen is kept.
5. **Coordinates.** Columns stand `COLUMN_GAP` apart. Each column's nodes keep their order and at
   least `ROW_GAP` between them (a waypoint needs only `WAYPOINT_GAP`), shifted as a block
   towards where their neighbours are, so a chain of single stages runs in a straight line.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

COLUMN_GAP = 250.0
ROW_GAP = 115.0
"""Room for a node and the three lines of label under it."""
WAYPOINT_GAP = 30.0
_ORDER_SWEEPS = 24
_ALIGN_SWEEPS = 6

Edge = tuple[str, str]


@dataclass(frozen=True)
class Layout:
    positions: dict[str, tuple[float, float]]
    """Every node's centre."""
    waypoints: dict[Edge, list[tuple[float, float]]]
    """The points an edge passes between its ends, left to right -- none for a short one."""
    back_edges: frozenset[Edge]
    """The edges set aside to break cycles: they run right to left."""


def layered_layout(node_ids: Sequence[str], edges: Iterable[Edge]) -> Layout:
    nodes = list(dict.fromkeys(node_ids))
    known = set(nodes)
    unique = list(dict.fromkeys((s, t) for s, t in edges if s != t and s in known and t in known))
    back, discovered = _back_edges(nodes, unique)
    forward = [edge for edge in unique if edge not in back]
    column = _columns(nodes, forward + [(t, s) for s, t in back if (t, s) not in unique])

    # Waypoints: each forward edge becomes a chain of unit steps, one column at a time.
    rank = {node: float(discovered[node]) for node in nodes}
    layer_of = dict(column)
    steps: list[Edge] = []
    chains: dict[Edge, list[str]] = {}
    for source, target in forward:
        chain = [
            f"\x00{source}\x00{target}\x00{c}" for c in range(column[source] + 1, column[target])
        ]
        for waypoint, c in zip(chain, range(column[source] + 1, column[target]), strict=True):
            layer_of[waypoint] = c
            rank[waypoint] = rank[source] + 0.5
        chains[(source, target)] = chain
        path = [source, *chain, target]
        steps.extend(zip(path, path[1:], strict=False))

    layers = _order(layer_of, rank, steps)
    y = _align(layers, steps, waypoints=set(layer_of) - known)
    positions = {node: (layer_of[node] * COLUMN_GAP, y[node]) for node in nodes}
    return Layout(
        positions=positions,
        waypoints={
            edge: [(layer_of[w] * COLUMN_GAP, y[w]) for w in chain]
            for edge, chain in chains.items()
            if chain
        },
        back_edges=frozenset(back),
    )


def crossings(layers: Sequence[Sequence[str]], steps: Iterable[Edge]) -> int:
    """How many pairs of `steps` (each between neighbouring layers) cross."""
    position = {
        node: (index, i) for index, layer in enumerate(layers) for i, node in enumerate(layer)
    }
    between: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for source, target in steps:
        (upper, a), (lower, b) = position[source], position[target]
        if lower == upper + 1:
            between[upper].append((a, b))
    return sum(
        1
        for pairs in between.values()
        for i, (a1, b1) in enumerate(pairs)
        for a2, b2 in pairs[i + 1 :]
        if (a1 - a2) * (b1 - b2) < 0
    )


def _back_edges(nodes: Sequence[str], edges: Sequence[Edge]) -> tuple[set[Edge], dict[str, int]]:
    """The edges a depth-first walk -- from the nodes nothing feeds, first -- finds pointing back
    at a node still being walked, and the order it reached every node in."""
    successors: dict[str, list[str]] = defaultdict(list)
    fed = set()
    for source, target in edges:
        successors[source].append(target)
        fed.add(target)
    state: dict[str, int] = {}  # 1: being walked, 2: done
    discovered: dict[str, int] = {}
    back: set[Edge] = set()
    for root in [n for n in nodes if n not in fed] + list(nodes):
        if root in state:
            continue
        state[root], discovered[root] = 1, len(discovered)
        stack = [(root, iter(successors[root]))]
        while stack:
            node, rest = stack[-1]
            nxt = next(rest, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                back.add((node, nxt))
            elif nxt not in state:
                state[nxt], discovered[nxt] = 1, len(discovered)
                stack.append((nxt, iter(successors[nxt])))
    return back, discovered


def _columns(nodes: Sequence[str], edges: Sequence[Edge]) -> dict[str, int]:
    predecessors: dict[str, set[str]] = defaultdict(set)
    successors: dict[str, set[str]] = defaultdict(set)
    for source, target in edges:
        predecessors[target].add(source)
        successors[source].add(target)
    waiting = {node: len(predecessors[node]) for node in nodes}
    ready = [node for node in nodes if waiting[node] == 0]
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for nxt in sorted(successors[node], key=nodes.index):
            waiting[nxt] -= 1
            if waiting[nxt] == 0:
                ready.append(nxt)
    column: dict[str, int] = {}
    for node in order:
        column[node] = max((column[p] + 1 for p in predecessors[node]), default=0)
    for node in reversed(order):
        if not predecessors[node] and successors[node]:
            column[node] = max(0, min(column[s] for s in successors[node]) - 1)
    for node in nodes:  # only if a cycle were left: nothing is lost, it just starts a row
        column.setdefault(node, 0)
    return column


def _order(
    layer_of: dict[str, int], rank: dict[str, float], steps: Sequence[Edge]
) -> list[list[str]]:
    count = max(layer_of.values(), default=-1) + 1
    layers: list[list[str]] = [[] for _ in range(count)]
    for node in sorted(layer_of, key=lambda n: rank[n]):
        layers[layer_of[node]].append(node)
    before: dict[str, list[str]] = defaultdict(list)
    after: dict[str, list[str]] = defaultdict(list)
    for source, target in steps:
        after[source].append(target)
        before[target].append(source)

    best, fewest = [list(layer) for layer in layers], crossings(layers, steps)
    for sweep in range(_ORDER_SWEEPS):
        downward = sweep % 2 == 0
        indices = range(1, count) if downward else range(count - 2, -1, -1)
        for index in indices:
            neighbours = before if downward else after
            reference = layers[index - 1] if downward else layers[index + 1]
            place = {node: i for i, node in enumerate(reference)}
            current = {node: i for i, node in enumerate(layers[index])}

            def barycenter(node: str, place=place, current=current, neighbours=neighbours) -> float:
                linked = [place[n] for n in neighbours[node] if n in place]
                return sum(linked) / len(linked) if linked else current[node]

            layers[index].sort(key=barycenter)
        found = crossings(layers, steps)
        if found < fewest:
            best, fewest = [list(layer) for layer in layers], found
        if fewest == 0:
            break
    return best


def _align(
    layers: list[list[str]], steps: Sequence[Edge], *, waypoints: set[str]
) -> dict[str, float]:
    """Each node's height: every column packed in order, then moved as a block towards the mean
    height of its nodes' neighbours, sweeping right and left a few times."""
    neighbours: dict[str, list[str]] = defaultdict(list)
    for source, target in steps:
        neighbours[source].append(target)
        neighbours[target].append(source)

    def gap(node: str) -> float:
        return WAYPOINT_GAP if node in waypoints else ROW_GAP

    y: dict[str, float] = {}
    for layer in layers:  # centred to begin with
        top = -sum(gap(n) for n in layer) / 2
        for node in layer:
            y[node] = top + gap(node) / 2
            top += gap(node)

    for sweep in range(_ALIGN_SWEEPS):
        ordered = layers if sweep % 2 == 0 else layers[::-1]
        for layer in ordered:
            wanted = [
                sum(y[n] for n in neighbours[node]) / len(neighbours[node])
                if neighbours[node]
                else y[node]
                for node in layer
            ]
            packed: list[float] = []
            for i, node in enumerate(layer):
                floor = packed[-1] + (gap(layer[i - 1]) + gap(node)) / 2 if packed else wanted[0]
                packed.append(max(wanted[i], floor))
            shift = (
                sum(w - p for w, p in zip(wanted, packed, strict=True)) / len(layer) if layer else 0
            )
            for node, height in zip(layer, packed, strict=True):
                y[node] = height + shift
    return y
