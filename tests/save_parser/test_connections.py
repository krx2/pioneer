"""Tests for tracing belts and pipes, against hand-joined ports named the way a save names them --
no save file. The real saves' own links are checked in test_real_saves_connections.py."""

from pioneer.contracts import TransportLink
from pioneer.save_parser.connections import Port, trace_links


def _trace(
    *joins: tuple[str, str], loose: tuple[str, ...] = ()
) -> dict[tuple[str, str], TransportLink]:
    """`joins` pair ports, `Owner_C_1.Port`, both ways round, as a save does; `loose` adds ports
    joined to nothing. A port is a pipe's when its path says so."""
    joined: dict[str, str] = {}
    for a, b in joins:
        joined[a], joined[b] = b, a
    ports = [
        Port(
            path=path,
            owner=path.rpartition(".")[0],
            name=path.rpartition(".")[2],
            carrier="pipe" if "Pipe" in path else "belt",
            connected_to=joined.get(path),
        )
        for path in sorted({*joined, *loose})
    ]
    return {(link.source_id, link.target_id): link for link in trace_links(ports, {})}


_SMELTER = "Build_SmelterMk1_C_1"
_PLATES = "Build_ConstructorMk1_C_2"
_RODS = "Build_ConstructorMk1_C_3"


def test_a_belt_line_links_an_output_to_the_input_at_its_other_end() -> None:
    links = _trace(
        (f"{_SMELTER}.Output2", "Build_ConveyorBeltMk1_C_10.ConveyorAny0"),
        ("Build_ConveyorBeltMk1_C_10.ConveyorAny1", "Build_ConveyorLiftMk1_C_11.ConveyorAny1"),
        ("Build_ConveyorLiftMk1_C_11.ConveyorAny0", f"{_PLATES}.Input0"),
    )

    assert links == {
        (_SMELTER, _PLATES): TransportLink(
            _SMELTER,
            _PLATES,
            "belt",
            via=("Build_ConveyorBeltMk1_C_10", "Build_ConveyorLiftMk1_C_11"),
        )
    }


def test_a_splitter_feeds_every_output_and_a_merger_joins_every_input() -> None:
    links = _trace(
        (f"{_SMELTER}.Output2", "Build_ConveyorBeltMk1_C_10.ConveyorAny0"),
        ("Build_ConveyorBeltMk1_C_10.ConveyorAny1", "Build_ConveyorAttachmentSplitter_C_20.Input1"),
        ("Build_ConveyorAttachmentSplitter_C_20.Output1", f"{_PLATES}.Input0"),
        (
            "Build_ConveyorAttachmentSplitter_C_20.Output2",
            "Build_ConveyorBeltMk1_C_12.ConveyorAny0",
        ),
        ("Build_ConveyorBeltMk1_C_12.ConveyorAny1", "Build_ConveyorAttachmentMerger_C_30.Input2"),
        (f"{_PLATES}.Output0", "Build_ConveyorAttachmentMerger_C_30.Input1"),
        ("Build_ConveyorAttachmentMerger_C_30.Output1", f"{_RODS}.Input0"),
    )

    assert set(links) == {(_SMELTER, _PLATES), (_SMELTER, _RODS), (_PLATES, _RODS)}
    assert links[(_SMELTER, _RODS)].via == (
        "Build_ConveyorBeltMk1_C_10",
        "Build_ConveyorBeltMk1_C_12",
    )
    assert links[(_PLATES, _RODS)].via == ()


def test_a_container_is_passed_through_and_one_nothing_feeds_is_a_source() -> None:
    fed = "Build_StorageContainerMk1_C_40"
    by_hand = "Build_StorageContainerMk1_C_41"
    links = _trace(
        (f"{_SMELTER}.Output2", f"{fed}.Input0"),
        (f"{fed}.Output1", f"{_PLATES}.Input0"),
        (f"{by_hand}.Output1", f"{_RODS}.Input0"),
        loose=(f"{by_hand}.Input0",),
    )

    assert set(links) == {(_SMELTER, fed), (_SMELTER, _PLATES), (by_hand, _RODS)}


def test_a_belt_ending_in_nothing_and_a_loop_link_nowhere() -> None:
    links = _trace(
        (f"{_SMELTER}.Output2", "Build_ConveyorBeltMk1_C_10.ConveyorAny0"),
        ("Build_ConveyorBeltMk1_C_10.ConveyorAny1", "Build_ConveyorAttachmentMerger_C_30.Input1"),
        ("Build_ConveyorAttachmentMerger_C_30.Output1", "Build_ConveyorBeltMk1_C_11.ConveyorAny0"),
        ("Build_ConveyorBeltMk1_C_11.ConveyorAny1", "Build_ConveyorAttachmentMerger_C_30.Input2"),
        (f"{_PLATES}.Output0", "Build_ConveyorBeltMk1_C_12.ConveyorAny0"),
        loose=("Build_ConveyorBeltMk1_C_12.ConveyorAny1",),
    )

    assert links == {}


def test_a_splitter_entered_at_an_output_leads_nowhere() -> None:
    links = _trace(
        (f"{_SMELTER}.Output2", "Build_ConveyorAttachmentSplitter_C_20.Output1"),
        ("Build_ConveyorAttachmentSplitter_C_20.Output2", f"{_PLATES}.Input0"),
    )

    assert links == {}


def test_a_pipe_network_links_whatever_puts_fluid_in_to_whatever_takes_it_out() -> None:
    pump = "Build_WaterPump_C_1"
    refineries = ("Build_OilRefinery_C_2", "Build_OilRefinery_C_3")
    links = _trace(
        (f"{pump}.FGPipeConnectionFactory", "Build_Pipeline_C_10.PipelineConnection0"),
        (
            "Build_Pipeline_C_10.PipelineConnection1",
            "Build_PipelineJunction_Cross_C_11.Connection0",
        ),
        ("Build_PipelineJunction_Cross_C_11.Connection1", f"{refineries[0]}.PipeInputFactory"),
        ("Build_PipelineJunction_Cross_C_11.Connection2", "Build_PipelinePump_C_12.Connection1"),
        ("Build_PipelinePump_C_12.Connection0", f"{refineries[1]}.PipeInputFactory"),
        (f"{refineries[0]}.PipeOutputFactory", "Build_Pipeline_C_13.PipelineConnection0"),
        (
            "Build_Pipeline_C_13.PipelineConnection1",
            "Build_GeneratorFuel_C_4.FGPipeConnectionFactory",
        ),
    )

    assert set(links) == {
        (pump, refineries[0]),
        (pump, refineries[1]),
        (refineries[0], "Build_GeneratorFuel_C_4"),
    }
    assert all(link.carrier == "pipe" and link.via == () for link in links.values())
