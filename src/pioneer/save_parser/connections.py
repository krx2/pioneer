"""Which buildings the player's belts and pipes join, read off the save's connection components.

Every belt end, splitter port, machine input and pipe socket is an object of its own, named after
its building and port (`...Build_ConstructorMk1_C_2147422360.Output0`); one joined to another
names it in `mConnectedComponent`. Direction isn't saved, but the port's name says it: `Input…`,
`Output…`, and a belt's two ends `ConveyorAny0`/`ConveyorAny1`. Hypertubes and the snap points of
poles and wall holes are left out.

- Belts are followed downstream from every building output that isn't part of a belt line, through
  splitters, mergers and storage containers, to the input of anything else. Each (building,
  building) pair is a `TransportLink` with the belts of the shortest route between them.
- Pipes carry fluid either way, so each network links every building putting fluid in to every
  one taking it out. An unnamed socket (`FGPipeConnectionFactory`) is an extractor's output or a
  generator's input.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from pioneer.contracts import Carrier, TransportLink
from pioneer.save_parser.entities import EntitySpan
from pioneer.save_parser.object_table import RawObjectHeader
from pioneer.save_parser.properties import read_level_object_reference

_PORT_CARRIERS: dict[str, Carrier] = {
    "FGFactoryConnectionComponent": "belt",
    "FGPipeConnectionComponent": "pipe",
    "FGPipeConnectionFactory": "pipe",
}
_BELT_LINE = ("Build_ConveyorBelt", "Build_ConveyorLift")
_SPLITTER_OR_MERGER = ("Build_ConveyorAttachment",)
_STORAGE = ("Build_StorageContainer",)
_PIPE_PART = ("Build_Pipeline", "Build_PipeStorageTank", "Build_IndustrialTank", "Build_Valve")
_FLUID_EXTRACTOR = ("Build_WaterPump", "Build_OilPump", "Build_FrackingExtractor")
_INSTANCE_SUFFIX = re.compile(r"_\d+$")


@dataclass(frozen=True)
class Port:
    """One connection component: its own path, the building it belongs to, its name there, and
    the port it's joined to, if any."""

    path: str
    owner: str
    name: str
    carrier: Carrier
    connected_to: str | None


def read_ports(
    headers: Sequence[RawObjectHeader], body: bytes, spans: Sequence[EntitySpan]
) -> tuple[Port, ...]:
    """Every belt and pipe port in the save, with what it's joined to. `spans[i]` must be the
    span for `headers[i]`."""
    ports = []
    for header, span in zip(headers, spans, strict=True):
        carrier = _PORT_CARRIERS.get(header.class_name.rsplit(".", 1)[-1])
        if header.is_actor or carrier is None:
            continue
        owner, _, name = header.path_name.rpartition(".")
        connected = read_level_object_reference(
            body, "mConnectedComponent", start=span.start, end=span.end
        )
        ports.append(Port(header.path_name, owner, name, carrier, connected))
    return tuple(ports)


def trace_links(ports: Sequence[Port], building_of: Mapping[str, str]) -> tuple[TransportLink, ...]:
    """Every (building, building) pair the belts and pipes join.
    `building_of` names each building's class (`Build_ConstructorMk1_C`) by its path; a port of
    one it doesn't know is told by its path instead."""

    def kind(owner: str) -> str:
        return building_of.get(owner) or _INSTANCE_SUFFIX.sub("", owner.rsplit(".", 1)[-1])

    belts = [port for port in ports if port.carrier == "belt"]
    pipes = [port for port in ports if port.carrier == "pipe"]
    return (*_belt_links(belts, kind), *_pipe_links(pipes, kind))


def _belt_links(ports: Sequence[Port], kind: Callable[[str], str]) -> list[TransportLink]:
    by_path = {port.path: port for port in ports}
    by_owner: dict[str, list[Port]] = defaultdict(list)
    for port in ports:
        by_owner[port.owner].append(port)

    links: dict[tuple[str, str], TransportLink] = {}
    for owner, owned in by_owner.items():
        role = kind(owner)
        if role.startswith(_BELT_LINE + _SPLITTER_OR_MERGER):
            continue
        if role.startswith(_STORAGE) and any(_is_input(p) and p.connected_to for p in owned):
            continue  # what reaches a fed container is followed through it from upstream
        for port in owned:
            if _is_output(port) and port.connected_to:
                for target, via in _downstream(owner, port, by_path, by_owner, kind):
                    links.setdefault((owner, target), TransportLink(owner, target, "belt", via))
    return list(links.values())


def _downstream(
    source: str,
    start: Port,
    by_path: Mapping[str, Port],
    by_owner: Mapping[str, Sequence[Port]],
    kind: Callable[[str], str],
) -> Iterable[tuple[str, tuple[str, ...]]]:
    """The buildings items put out at `start` can reach, each with the belts and lifts on the
    shortest route there -- breadth first, so the first route found is the shortest."""
    reached: dict[str, tuple[str, ...]] = {}
    queue: deque[tuple[Port, tuple[str, ...]]] = deque([(start, ())])
    seen: set[str] = set()
    while queue:
        port, via = queue.popleft()
        entered = by_path.get(port.connected_to or "")
        if entered is None or entered.path in seen:
            continue
        seen.add(entered.path)
        owner, role = entered.owner, kind(entered.owner)
        if role.startswith(_BELT_LINE):
            queue.extend(
                (other, (*via, owner))
                for other in by_owner[owner]
                if other.path != entered.path and other.name.startswith("ConveyorAny")
            )
        elif role.startswith(_SPLITTER_OR_MERGER + _STORAGE):
            if not _is_input(entered):
                continue
            if role.startswith(_STORAGE) and owner != source:
                reached.setdefault(owner, via)
            queue.extend((other, via) for other in by_owner[owner] if _is_output(other))
        elif _is_input(entered) and owner != source:
            reached.setdefault(owner, via)
    return reached.items()


def _pipe_links(ports: Sequence[Port], kind: Callable[[str], str]) -> list[TransportLink]:
    parent = {port.path: port.path for port in ports}

    def root(path: str) -> str:
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    def join(a: str, b: str) -> None:
        parent[root(a)] = root(b)

    by_owner: dict[str, list[Port]] = defaultdict(list)
    for port in ports:
        by_owner[port.owner].append(port)
        if port.connected_to in parent:
            join(port.path, port.connected_to)
    for owner, owned in by_owner.items():
        if kind(owner).startswith(_PIPE_PART):
            for port in owned[1:]:
                join(owned[0].path, port.path)

    into: dict[str, set[str]] = defaultdict(set)
    out_of: dict[str, set[str]] = defaultdict(set)
    for port in ports:
        role = kind(port.owner)
        if role.startswith(_PIPE_PART):
            continue
        puts_out = "Output" in port.name or (
            "Input" not in port.name and role.startswith(_FLUID_EXTRACTOR)
        )
        (into if puts_out else out_of)[root(port.path)].add(port.owner)
    return [
        TransportLink(source, target, "pipe")
        for network, sources in into.items()
        for source in sorted(sources)
        for target in sorted(out_of.get(network, ()))
        if source != target
    ]


def _is_input(port: Port) -> bool:
    return port.name.startswith("Input")


def _is_output(port: Port) -> bool:
    return port.name.startswith("Output")
