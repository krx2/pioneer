"""Tests for counting over items, recipes and graphs, against hand-built contract data."""

import pytest
from tests.game_data import IRON_INGOT, IRON_PLATE, PURE_IRON_INGOT

from pioneer.common import (
    machines_by_recipe,
    machines_needed,
    raw_resource_ids,
    recipes_for_output,
)
from pioneer.contracts import Item, ProductionGraph, ProductionNode


def _node(node_id: str, recipe_id: str, machines: float) -> ProductionNode:
    return ProductionNode(
        node_id=node_id, recipe_id=recipe_id, building_id="Build", machine_count=machines
    )


def test_raw_resource_ids_are_the_items_flagged_raw() -> None:
    items = (
        Item(item_id="Desc_OreIron_C", name="Iron Ore", is_raw_resource=True),
        Item(item_id="Desc_IronIngot_C", name="Iron Ingot"),
        Item(item_id="Desc_Water_C", name="Water", is_fluid=True, is_raw_resource=True),
    )
    assert raw_resource_ids(items) == {"Desc_OreIron_C", "Desc_Water_C"}


def test_recipes_for_output_returns_every_recipe_making_the_item_in_order() -> None:
    recipes = (PURE_IRON_INGOT, IRON_PLATE, IRON_INGOT)
    assert recipes_for_output("Desc_IronIngot_C", recipes) == (PURE_IRON_INGOT, IRON_INGOT)


def test_recipes_for_output_of_an_item_nothing_makes_is_empty() -> None:
    assert recipes_for_output("Desc_OreIron_C", (IRON_INGOT, IRON_PLATE)) == ()


@pytest.mark.parametrize(
    ("rate", "target", "expected"),
    [(15, 100, 7), (15, 45, 3), (15, 0, 0), (15, -10, 0), (0, 0, 0)],
)
def test_machines_needed_rounds_up_to_whole_machines(
    rate: float, target: float, expected: int
) -> None:
    assert machines_needed(rate, target) == expected


def test_machines_needed_refuses_a_rate_that_isnt_positive() -> None:
    with pytest.raises(ValueError, match="no positive output rate"):
        machines_needed(0, 10)


def test_machines_by_recipe_adds_up_nodes_running_the_same_recipe() -> None:
    graph = ProductionGraph(
        nodes=(
            _node("a", "Recipe_IngotIron_C", 2.5),
            _node("b", "Recipe_IronPlate_C", 1.0),
            _node("c", "Recipe_IngotIron_C", 1.5),
        ),
        flows=(),
    )
    assert machines_by_recipe(graph) == {"Recipe_IngotIron_C": 4.0, "Recipe_IronPlate_C": 1.0}


def test_machines_by_recipe_of_no_graph_is_empty() -> None:
    assert machines_by_recipe(None) == {}
