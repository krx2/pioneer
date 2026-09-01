"""Tests for the production planner, against a hand-written iron-chain recipe set (own fixture,
not knowledge_base's `docs/en-US.json` or its loader) covering the Reinforced Iron Plate chain
from the source deck — chosen because Iron Ingot demand converges from two branches (Iron Plate
and, via Screw, Iron Rod), which is exactly what demand aggregation needs to get right."""

import pytest

from pioneer.contracts import ItemAmount, MaterialFlow, ProductionNode, Recipe
from pioneer.production_planner.planner import plan_production, recipes_for_output

_IRON_INGOT = Recipe(
    recipe_id="Recipe_IngotIron_C",
    name="Iron Ingot",
    building_ids=("Build_SmelterMk1_C",),
    inputs=(ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
)
_IRON_PLATE = Recipe(
    recipe_id="Recipe_IronPlate_C",
    name="Iron Plate",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronPlate_C", amount_per_minute=20),),
)
_IRON_ROD = Recipe(
    recipe_id="Recipe_IronRod_C",
    name="Iron Rod",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=15),),
    outputs=(ItemAmount(item_id="Desc_IronRod_C", amount_per_minute=15),),
)
_SCREW = Recipe(
    recipe_id="Recipe_Screw_C",
    name="Screw",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronRod_C", amount_per_minute=10),),
    outputs=(ItemAmount(item_id="Desc_Screw_C", amount_per_minute=40),),
)
_REINFORCED_IRON_PLATE = Recipe(
    recipe_id="Recipe_IronPlateReinforced_C",
    name="Reinforced Iron Plate",
    building_ids=("Build_AssemblerMk1_C",),
    inputs=(
        ItemAmount(item_id="Desc_IronPlate_C", amount_per_minute=30),
        ItemAmount(item_id="Desc_Screw_C", amount_per_minute=60),
    ),
    outputs=(ItemAmount(item_id="Desc_IronPlateReinforced_C", amount_per_minute=5),),
)
_IRON_INGOT_PURE = Recipe(
    recipe_id="Recipe_Alternate_PureIronIngot_C",
    name="Alternate: Pure Iron Ingot",
    building_ids=("Build_OilRefinery_C",),
    inputs=(
        ItemAmount(item_id="Desc_OreIron_C", amount_per_minute=35),
        ItemAmount(item_id="Desc_Water_C", amount_per_minute=20),
    ),
    outputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=65),),
)

RECIPES = (_IRON_INGOT, _IRON_PLATE, _IRON_ROD, _SCREW, _REINFORCED_IRON_PLATE)


def _node(graph, item_id: str) -> ProductionNode:
    return next(n for n in graph.nodes if n.node_id == f"node_{item_id}")


def _flow(graph, item_id: str, target_item_id: str | None) -> MaterialFlow:
    target_node = f"node_{target_item_id}" if target_item_id else None
    return next(
        f
        for f in graph.flows
        if f.item_id == item_id and f.target_node_id == target_node
    )


def test_reinforced_iron_plate_chain_machine_counts() -> None:
    graph = plan_production("Desc_IronPlateReinforced_C", 10, RECIPES)

    assert len(graph.nodes) == 5
    assert _node(graph, "Desc_IronPlateReinforced_C").machine_count == 2
    assert _node(graph, "Desc_IronPlate_C").machine_count == 3
    assert _node(graph, "Desc_Screw_C").machine_count == 3
    assert _node(graph, "Desc_IronRod_C").machine_count == 2
    # Converges from both the Iron Plate branch (30*3=90) and the Iron Rod branch (15*2=30).
    assert _node(graph, "Desc_IronIngot_C").machine_count == 4


def test_reinforced_iron_plate_chain_node_fields() -> None:
    graph = plan_production("Desc_IronPlateReinforced_C", 10, RECIPES)

    ingot_node = _node(graph, "Desc_IronIngot_C")
    assert ingot_node.recipe_id == "Recipe_IngotIron_C"
    assert ingot_node.building_id == "Build_SmelterMk1_C"
    assert ingot_node.is_existing is False


def test_reinforced_iron_plate_chain_flows() -> None:
    graph = plan_production("Desc_IronPlateReinforced_C", 10, RECIPES)

    assert len(graph.flows) == 7
    assert _flow(graph, "Desc_OreIron_C", "Desc_IronIngot_C") == MaterialFlow(
        item_id="Desc_OreIron_C",
        amount_per_minute=120,
        source_node_id=None,
        target_node_id="node_Desc_IronIngot_C",
    )
    ingot_to_plate = _flow(graph, "Desc_IronIngot_C", "Desc_IronPlate_C")
    ingot_to_rod = _flow(graph, "Desc_IronIngot_C", "Desc_IronRod_C")
    plate_to_rip = _flow(graph, "Desc_IronPlate_C", "Desc_IronPlateReinforced_C")
    screw_to_rip = _flow(graph, "Desc_Screw_C", "Desc_IronPlateReinforced_C")
    assert ingot_to_plate.amount_per_minute == pytest.approx(90)
    assert ingot_to_rod.amount_per_minute == pytest.approx(30)
    assert plate_to_rip.amount_per_minute == pytest.approx(60)
    assert screw_to_rip.amount_per_minute == pytest.approx(120)
    final_output = _flow(graph, "Desc_IronPlateReinforced_C", None)
    assert final_output.amount_per_minute == 10
    assert final_output.source_node_id == "node_Desc_IronPlateReinforced_C"
    assert final_output.target_node_id is None


def test_machine_counts_round_up() -> None:
    # 1/min of Reinforced Iron Plate: ceil(1/5)=1 machine, actually overproducing 5/min.
    graph = plan_production("Desc_IronPlateReinforced_C", 1, RECIPES)
    assert _node(graph, "Desc_IronPlateReinforced_C").machine_count == 1


def test_target_with_no_recipe_raises() -> None:
    with pytest.raises(ValueError, match="Desc_OreIron_C"):
        plan_production("Desc_OreIron_C", 100, RECIPES)


def test_recipes_for_output_returns_all_alternates() -> None:
    recipes = (_IRON_INGOT, _IRON_INGOT_PURE)
    result = recipes_for_output("Desc_IronIngot_C", recipes)
    assert set(result) == {_IRON_INGOT, _IRON_INGOT_PURE}


def test_recipes_for_output_unknown_item_is_empty() -> None:
    assert recipes_for_output("nonexistent", RECIPES) == ()


def test_default_recipe_choice_is_the_first_alternate() -> None:
    recipes = (_IRON_INGOT, _IRON_INGOT_PURE, _IRON_PLATE)
    graph = plan_production("Desc_IronPlate_C", 20, recipes)
    assert _node(graph, "Desc_IronIngot_C").recipe_id == "Recipe_IngotIron_C"


def test_recipe_choices_overrides_the_default() -> None:
    recipes = (_IRON_INGOT, _IRON_INGOT_PURE, _IRON_PLATE)
    graph = plan_production(
        "Desc_IronPlate_C",
        20,
        recipes,
        recipe_choices={"Desc_IronIngot_C": "Recipe_Alternate_PureIronIngot_C"},
    )
    ingot_node = _node(graph, "Desc_IronIngot_C")
    assert ingot_node.recipe_id == "Recipe_Alternate_PureIronIngot_C"
    # Pure Iron Ingot needs Water too, on top of Iron Ore.
    assert any(f.item_id == "Desc_Water_C" for f in graph.flows)


def test_invalid_recipe_choice_raises() -> None:
    with pytest.raises(ValueError, match="does_not_exist"):
        plan_production(
            "Desc_IronPlate_C",
            20,
            (_IRON_INGOT, _IRON_PLATE),
            recipe_choices={"Desc_IronIngot_C": "does_not_exist"},
        )


def test_cyclic_recipe_dependency_raises() -> None:
    a = Recipe(
        recipe_id="A",
        name="A",
        building_ids=("b",),
        inputs=(ItemAmount(item_id="item_b", amount_per_minute=1),),
        outputs=(ItemAmount(item_id="item_a", amount_per_minute=1),),
    )
    b = Recipe(
        recipe_id="B",
        name="B",
        building_ids=("b",),
        inputs=(ItemAmount(item_id="item_a", amount_per_minute=1),),
        outputs=(ItemAmount(item_id="item_b", amount_per_minute=1),),
    )
    with pytest.raises(ValueError, match="cyclic"):
        plan_production("item_a", 10, (a, b))
