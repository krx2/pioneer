"""Tests for graph_to_d3_data, against a Reinforced Iron Plate production graph copied in as a
static fixture (a Stage 7/8-style example output), per implementation.md Stage 13."""

import pytest

from pioneer.contracts import MaterialFlow, ProductionGraph, ProductionNode
from pioneer.graph_presentation.renderer import graph_to_d3_data, readable_id, render_page

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


def test_a_flow_to_an_unknown_node_becomes_a_boundary_not_a_dangling_link() -> None:
    graph = ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="node_known",
                recipe_id="Recipe_Whatever_C",
                building_id="Build_Whatever_C",
                machine_count=1,
            ),
        ),
        flows=(
            MaterialFlow(
                item_id="Desc_In_C",
                amount_per_minute=1,
                source_node_id="node_gone",
                target_node_id="node_known",
            ),
            MaterialFlow(
                item_id="Desc_Out_C",
                amount_per_minute=1,
                source_node_id="node_known",
                target_node_id="node_also_gone",
            ),
        ),
    )

    data = graph_to_d3_data(graph)

    node_ids = {n["id"] for n in data["nodes"]}
    for link in data["links"]:
        assert link["source"] in node_ids
        assert link["target"] in node_ids
    assert {n["kind"] for n in data["nodes"]} == {"machine", "boundary-in", "boundary-out"}


def test_an_extended_node_says_how_many_of_its_machines_are_new() -> None:
    graph = ProductionGraph(
        nodes=(
            ProductionNode(
                node_id="save_smelters",
                recipe_id="Recipe_IngotIron_C",
                building_id="Build_SmelterMk1_C",
                machine_count=11,
                is_existing=True,
                existing_machine_count=10,
            ),
        ),
        flows=(),
    )

    (node,) = graph_to_d3_data(graph, {"Recipe_IngotIron_C": "Iron Ingot"})["nodes"]

    assert node["label"] == "Iron Ingot ×11 (+1 new)"
    assert node["extended"] is True
    assert "node-extended" in render_page(graph)


def test_only_existing_nodes_with_new_machines_count_as_extended() -> None:
    data = graph_to_d3_data(_GRAPH)

    assert not any(node.get("extended") for node in data["nodes"])


def test_render_page_embeds_the_data_and_loads_d3() -> None:
    page = render_page(_GRAPH)

    assert "node_reinforced" in page
    assert "d3" in page.lower()
    assert "<!doctype html>" in page.lower()


def test_render_page_handles_an_empty_graph() -> None:
    page = render_page(ProductionGraph(nodes=(), flows=()))

    assert '"nodes": []' in page or '"nodes":[]' in page


def test_labels_use_the_given_names_and_fall_back_to_readable_ids() -> None:
    names = {"Recipe_IngotIron_C": "Iron Ingot", "Desc_OreIron_C": "Iron Ore"}

    data = graph_to_d3_data(_GRAPH, names)

    labels = {node["id"]: node["label"] for node in data["nodes"]}
    assert labels["node_smelter"] == "Iron Ingot ×4"
    assert labels["__in__Desc_OreIron_C"] == "Iron Ore (input)"
    assert labels["node_plate"] == "Iron Plate ×3"  # no name for Recipe_IronPlate_C
    assert data["links"][0]["itemName"] == "Iron Ore"


def test_no_label_shows_a_raw_class_id_even_with_no_names_at_all() -> None:
    data = graph_to_d3_data(_GRAPH)

    labelled = [node["label"] for node in data["nodes"]]
    labelled += [link["itemName"] for link in data["links"]]
    assert not [text for text in labelled if "_C" in text]
    assert "Ore Iron (input)" in labelled


@pytest.mark.parametrize(
    ("class_id", "expected"),
    [
        ("Desc_IronPlate_C", "Iron Plate"),
        ("Recipe_Alternate_CoatedIronPlate_C", "Alternate Coated Iron Plate"),
        ("Build_SmelterMk1_C", "Smelter Mk1"),
        ("Desc_SpaceElevatorPart_1_C", "Space Elevator Part 1"),
        ("Desc_GunpowderMK2_C", "Gunpowder MK2"),
        ("just a name", "just a name"),
    ],
)
def test_readable_id_turns_a_class_id_into_words(class_id: str, expected: str) -> None:
    assert readable_id(class_id) == expected


def test_nodes_carry_the_icon_of_their_recipe_or_building_or_item() -> None:
    icons = {
        "Recipe_IngotIron_C": "/icons/Desc_IronIngot_C.png",
        "Build_ConstructorMk1_C": "/icons/Build_ConstructorMk1_C.png",
        "Desc_OreIron_C": "/icons/Desc_OreIron_C.png",
    }

    nodes = {node["id"]: node for node in graph_to_d3_data(_GRAPH, icons=icons)["nodes"]}

    assert nodes["node_smelter"]["icon"] == "/icons/Desc_IronIngot_C.png"
    assert nodes["node_plate"]["icon"] == "/icons/Build_ConstructorMk1_C.png"
    assert nodes["node_reinforced"]["icon"] is None
    assert nodes["__in__Desc_OreIron_C"]["icon"] == "/icons/Desc_OreIron_C.png"


def test_without_icons_no_node_has_one() -> None:
    assert all(node["icon"] is None for node in graph_to_d3_data(_GRAPH)["nodes"])


def test_only_mined_inputs_are_raw_and_parts_are_what_the_player_already_makes() -> None:
    graph = ProductionGraph(
        nodes=(ProductionNode("engine", "Recipe_ModularEngine_C", "Build_ManufacturerMk1_C", 2),),
        flows=(
            MaterialFlow("Desc_Motor_C", 4, target_node_id="engine"),
            MaterialFlow("Desc_OreIron_C", 30, target_node_id="engine"),
            MaterialFlow("Desc_ModularEngine_C", 2, source_node_id="engine"),
        ),
    )

    inputs = {
        node["id"]: node["raw"]
        for node in graph_to_d3_data(graph, raw_resources={"Desc_OreIron_C"})["nodes"]
        if node["kind"] == "boundary-in"
    }

    assert inputs == {"__in__Desc_Motor_C": False, "__in__Desc_OreIron_C": True}


def test_without_raw_resources_every_input_counts_as_raw() -> None:
    inputs = [node for node in graph_to_d3_data(_GRAPH)["nodes"] if node["kind"] == "boundary-in"]

    assert inputs and all(node["raw"] for node in inputs)


def test_every_node_says_what_it_makes_with_what_and_how_much_in_three_lines() -> None:
    names = {
        "Desc_IronIngot_C": "Iron Ingot",
        "Desc_IronPlate_C": "Iron Plate",
        "Desc_OreIron_C": "Iron Ore",
        "Build_SmelterMk1_C": "Smelter",
        "Build_ConstructorMk1_C": "Constructor",
    }
    products = {
        "Recipe_IngotIron_C": ("Desc_IronIngot_C", 30.0),
        "Recipe_IronPlate_C": ("Desc_IronPlate_C", 20.0),
    }

    data = graph_to_d3_data(_GRAPH, names, raw_resources={"Desc_OreIron_C"}, products=products)
    lines = {node["id"]: node["lines"] for node in data["nodes"]}

    assert lines["node_smelter"] == ["Iron Ingot", "Smelter ×4", "120/min"]
    assert lines["node_plate"] == ["Iron Plate", "Constructor ×3", "60/min"]
    assert lines["__in__Desc_OreIron_C"] == ["Iron Ore", "raw resource", "120/min"]
    assert lines["node_screw"][2] == "60/min"  # no product known: what its flows carry away


def test_every_node_and_link_is_placed_left_to_right() -> None:
    data = graph_to_d3_data(_GRAPH)
    x = {node["id"]: node["x"] for node in data["nodes"]}

    for link in data["links"]:
        assert x[link["source"]] < x[link["target"]]
        assert link["back"] is False
        assert all(x[link["source"]] < px < x[link["target"]] for px, _ in link["points"])
