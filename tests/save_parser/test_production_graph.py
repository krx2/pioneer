"""Tests for folding placements into a `ProductionGraph`, against hand-built `PlacementRecord`s —
no byte parsing involved here."""

from pioneer.contracts import Coordinates, PlacementRecord
from pioneer.save_parser.production_graph import to_production_graph


def _placement(recipe_id: str | None, building_id: str = "Build_SmelterMk1_C") -> PlacementRecord:
    return PlacementRecord(
        building_id=building_id, position=Coordinates(x=0.0, y=0.0, z=0.0), recipe_id=recipe_id
    )


def test_one_node_per_recipe_counting_machines() -> None:
    placements = (
        _placement("Recipe_IngotIron_C"),
        _placement("Recipe_IngotIron_C"),
        _placement("Recipe_IngotCopper_C"),
    )

    graph = to_production_graph(placements)

    counts = {node.recipe_id: node.machine_count for node in graph.nodes}
    assert counts == {"Recipe_IngotIron_C": 2, "Recipe_IngotCopper_C": 1}


def test_placements_without_a_recipe_are_skipped() -> None:
    graph = to_production_graph((_placement(None, "Build_ConveyorBeltMk1_C"), _placement(None)))

    assert graph.nodes == ()


def test_nodes_are_marked_existing_and_carry_their_building() -> None:
    graph = to_production_graph((_placement("Recipe_IngotIron_C"),))

    (node,) = graph.nodes
    assert node.is_existing is True
    assert node.building_id == "Build_SmelterMk1_C"
    assert node.node_id == "save_Recipe_IngotIron_C"


def test_flows_are_empty_since_routing_is_not_recoverable() -> None:
    graph = to_production_graph((_placement("Recipe_IngotIron_C"),))

    assert graph.flows == ()


def test_empty_input_produces_an_empty_graph() -> None:
    graph = to_production_graph(())

    assert graph.nodes == ()
    assert graph.flows == ()
