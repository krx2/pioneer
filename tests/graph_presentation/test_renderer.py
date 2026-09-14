"""Tests for graph_to_d3_data, against a Reinforced Iron Plate production graph copied in as a
static fixture (a Stage 7/8-style example output), per implementation.md Stage 13."""

from pioneer.contracts import MaterialFlow, ProductionGraph, ProductionNode
from pioneer.graph_presentation.renderer import graph_to_d3_data, render_page

_GRAPH = ProductionGraph(
    nodes=(
        ProductionNode(
            node_id="node_smelter",
            recipe_id="Recipe_IngotIron_C",
            building_id="Build_SmelterMk1_C",
            machine_count=4,
            is_existing=True,
        ),
        ProductionNode(
            node_id="node_plate",
            recipe_id="Recipe_IronPlate_C",
            building_id="Build_ConstructorMk1_C",
            machine_count=3,
            is_existing=True,
        ),
        ProductionNode(
            node_id="node_screw",
            recipe_id="Recipe_Screw_C",
            building_id="Build_ConstructorMk1_C",
            machine_count=2,
            is_existing=False,
        ),
        ProductionNode(
            node_id="node_reinforced",
            recipe_id="Recipe_IronPlateReinforced_C",
            building_id="Build_AssemblerMk1_C",
            machine_count=2,
            is_existing=False,
        ),
    ),
    flows=(
        MaterialFlow(
            item_id="Desc_OreIron_C", amount_per_minute=120, target_node_id="node_smelter"
        ),
        MaterialFlow(
            item_id="Desc_IronIngot_C",
            amount_per_minute=60,
            source_node_id="node_smelter",
            target_node_id="node_plate",
        ),
        MaterialFlow(
            item_id="Desc_IronPlate_C",
            amount_per_minute=30,
            source_node_id="node_plate",
            target_node_id="node_reinforced",
        ),
        MaterialFlow(
            item_id="Desc_Screw_C",
            amount_per_minute=60,
            source_node_id="node_screw",
            target_node_id="node_reinforced",
        ),
        MaterialFlow(
            item_id="Desc_IronPlateReinforced_C",
            amount_per_minute=5,
            source_node_id="node_reinforced",
        ),
    ),
)


def test_every_machine_node_is_present() -> None:
    data = graph_to_d3_data(_GRAPH)

    machine_ids = {n["id"] for n in data["nodes"] if n["kind"] == "machine"}
    assert machine_ids == {"node_smelter", "node_plate", "node_screw", "node_reinforced"}


def test_existing_vs_new_flag_is_preserved() -> None:
    data = graph_to_d3_data(_GRAPH)

    existing = {n["id"] for n in data["nodes"] if n["kind"] == "machine" and n["existing"]}
    new = {n["id"] for n in data["nodes"] if n["kind"] == "machine" and not n["existing"]}
    assert existing == {"node_smelter", "node_plate"}
    assert new == {"node_screw", "node_reinforced"}


def test_boundary_nodes_are_synthesized_for_dangling_flows() -> None:
    data = graph_to_d3_data(_GRAPH)

    boundary_kinds = {n["kind"] for n in data["nodes"] if n["kind"].startswith("boundary")}
    assert boundary_kinds == {"boundary-in", "boundary-out"}


def test_every_link_endpoint_matches_a_real_node_id() -> None:
    data = graph_to_d3_data(_GRAPH)

    node_ids = {n["id"] for n in data["nodes"]}
    for link in data["links"]:
        assert link["source"] in node_ids
        assert link["target"] in node_ids


def test_link_count_matches_flow_count() -> None:
    data = graph_to_d3_data(_GRAPH)

    assert len(data["links"]) == len(_GRAPH.flows)


def test_layer_strictly_increases_from_source_to_target_along_every_link() -> None:
    data = graph_to_d3_data(_GRAPH)

    layer_by_id = {n["id"]: n["layer"] for n in data["nodes"]}
    for link in data["links"]:
        assert layer_by_id[link["source"]] < layer_by_id[link["target"]]


def test_raw_input_boundary_nodes_are_in_the_leftmost_layer() -> None:
    data = graph_to_d3_data(_GRAPH)

    for node in data["nodes"]:
        if node["kind"] == "boundary-in":
            assert node["layer"] == 0


def test_final_output_boundary_node_has_the_deepest_layer() -> None:
    data = graph_to_d3_data(_GRAPH)

    layers = {n["id"]: n["layer"] for n in data["nodes"]}
    output_nodes = [n for n in data["nodes"] if n["kind"] == "boundary-out"]
    assert output_nodes
    for output_node in output_nodes:
        assert output_node["layer"] == max(layers.values())


def test_a_node_with_no_flows_at_all_defaults_to_layer_zero() -> None:
    graph = ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="node_isolated",
                recipe_id="Recipe_Whatever_C",
                building_id="Build_Whatever_C",
                machine_count=1,
            ),
        ),
        flows=(),
    )

    data = graph_to_d3_data(graph)

    assert data["nodes"][0]["layer"] == 0


def test_render_page_embeds_the_data_and_loads_d3() -> None:
    page = render_page(_GRAPH)

    assert "node_reinforced" in page
    assert "d3" in page.lower()
    assert "<!doctype html>" in page.lower()


def test_render_page_handles_an_empty_graph() -> None:
    page = render_page(ProductionGraph(nodes=(), flows=()))

    assert '"nodes": []' in page or '"nodes":[]' in page
