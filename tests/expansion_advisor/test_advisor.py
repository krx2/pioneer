"""Tests for turning a plan of additional machines into a `ChangeSet`, against hand-built
"existing" and "additions" graphs — no call to the Production Planner (the additions below are a
Stage 7 output copied in as a fixture, per implementation.md Stage 8)."""

from pioneer.contracts import ChangeAction, MaterialFlow, ProductionGraph, ProductionNode
from pioneer.expansion_advisor.advisor import advise_expansion


def _node(node_id: str, recipe_id: str, machine_count: float, *, is_existing: bool = False):
    return ProductionNode(
        node_id=node_id,
        recipe_id=recipe_id,
        building_id="Build_Whatever_C",
        machine_count=machine_count,
        is_existing=is_existing,
    )


# What the Production Planner returns for 5/min Reinforced Iron Plate against a factory with
# 30/min of spare Iron Plate: no plate constructors (the surplus covers them), so no smelters for
# plates either -- just the screw branch and the assembler.
_ADDITIONS = ProductionGraph(
    nodes=(
        _node("node_Desc_IronPlateReinforced_C", "Recipe_IronPlateReinforced_C", 1),
        _node("node_Desc_Screw_C", "Recipe_Screw_C", 2),
        _node("node_Desc_IronRod_C", "Recipe_IronRod_C", 2),
        _node("node_Desc_IronIngot_C", "Recipe_IngotIron_C", 1),
    ),
    flows=(
        MaterialFlow(
            item_id="Desc_OreIron_C", amount_per_minute=30, target_node_id="node_Desc_IronIngot_C"
        ),
        MaterialFlow(
            item_id="Desc_IronIngot_C",
            amount_per_minute=30,
            source_node_id="node_Desc_IronIngot_C",
            target_node_id="node_Desc_IronRod_C",
        ),
        MaterialFlow(
            item_id="Desc_IronRod_C",
            amount_per_minute=20,
            source_node_id="node_Desc_IronRod_C",
            target_node_id="node_Desc_Screw_C",
        ),
        MaterialFlow(
            item_id="Desc_Screw_C",
            amount_per_minute=60,
            source_node_id="node_Desc_Screw_C",
            target_node_id="node_Desc_IronPlateReinforced_C",
        ),
        MaterialFlow(  # from the existing factory's surplus
            item_id="Desc_IronPlate_C",
            amount_per_minute=30,
            target_node_id="node_Desc_IronPlateReinforced_C",
        ),
        MaterialFlow(
            item_id="Desc_IronPlateReinforced_C",
            amount_per_minute=5,
            source_node_id="node_Desc_IronPlateReinforced_C",
        ),
    ),
)

_EXISTING = ProductionGraph(
    nodes=(
        _node("save_Recipe_IngotIron_C", "Recipe_IngotIron_C", 10, is_existing=True),
        _node("save_Recipe_IronPlate_C", "Recipe_IronPlate_C", 6, is_existing=True),
        _node("save_Recipe_IngotCopper_C", "Recipe_IngotCopper_C", 4, is_existing=True),
    ),
    flows=(),
)


def _by_recipe(change_set, recipe_id):
    return next(c for c in change_set.changes if c.recipe_id == recipe_id)


def _result_node(graph: ProductionGraph, node_id: str) -> ProductionNode:
    return next(n for n in graph.nodes if n.node_id == node_id)


def test_an_addition_running_an_existing_recipe_extends_it() -> None:
    """Ten existing smelters don't make the new one unnecessary: whatever spare capacity they have
    was already taken out of the plan (as supply). Every addition is real."""
    result = advise_expansion(_EXISTING, _ADDITIONS)
    extend = _by_recipe(result, "Recipe_IngotIron_C")

    assert extend.action is ChangeAction.EXTEND
    assert extend.additional_machine_count == 1
    assert extend.target_node_id == "save_Recipe_IngotIron_C"


def test_additions_with_no_existing_recipe_are_added() -> None:
    result = advise_expansion(_EXISTING, _ADDITIONS)

    assert {c.recipe_id: c.action for c in result.changes} == {
        "Recipe_IngotIron_C": ChangeAction.EXTEND,
        "Recipe_IronRod_C": ChangeAction.ADD,
        "Recipe_Screw_C": ChangeAction.ADD,
        "Recipe_IronPlateReinforced_C": ChangeAction.ADD,
    }
    add = _by_recipe(result, "Recipe_Screw_C")
    assert add.additional_machine_count == 2
    assert add.target_node_id is None


def test_resulting_graph_is_the_changed_chain() -> None:
    graph = advise_expansion(_EXISTING, _ADDITIONS).resulting_graph

    assert {n.node_id for n in graph.nodes} == {
        "save_Recipe_IngotIron_C",
        "node_Desc_IronRod_C",
        "node_Desc_Screw_C",
        "node_Desc_IronPlateReinforced_C",
    }  # the untouched plate and copper nodes are the rest of the factory, not this chain
    smelters = _result_node(graph, "save_Recipe_IngotIron_C")
    assert smelters.machine_count == 11  # 10 existing + 1 from the extend
    assert smelters.is_existing is True
    assert _result_node(graph, "node_Desc_Screw_C").is_existing is False


def test_flows_are_rewired_onto_the_resulting_node_ids() -> None:
    graph = advise_expansion(_EXISTING, _ADDITIONS).resulting_graph

    node_ids = {n.node_id for n in graph.nodes}
    for flow in graph.flows:
        assert flow.source_node_id is None or flow.source_node_id in node_ids
        assert flow.target_node_id is None or flow.target_node_id in node_ids
    ingot_to_rods = next(f for f in graph.flows if f.item_id == "Desc_IronIngot_C")
    assert ingot_to_rods.source_node_id == "save_Recipe_IngotIron_C"


def test_no_additions_means_no_changes() -> None:
    result = advise_expansion(_EXISTING, ProductionGraph(nodes=(), flows=()))

    assert result.changes == ()
    assert result.resulting_graph.nodes == ()


def test_the_first_existing_node_running_the_recipe_is_the_anchor() -> None:
    existing = ProductionGraph(
        nodes=(
            _node("smelters_a", "Recipe_IngotIron_C", 1, is_existing=True),
            _node("smelters_b", "Recipe_IngotIron_C", 2, is_existing=True),
        ),
        flows=(),
    )

    extend = _by_recipe(advise_expansion(existing, _ADDITIONS), "Recipe_IngotIron_C")

    assert extend.target_node_id == "smelters_a"


def test_added_node_ids_never_collide_with_existing_ones() -> None:
    existing = ProductionGraph(
        nodes=(_node("node_Desc_Screw_C", "Recipe_SomethingElse_C", 1, is_existing=True),),
        flows=(),
    )

    graph = advise_expansion(existing, _ADDITIONS).resulting_graph

    screws = next(n for n in graph.nodes if n.recipe_id == "Recipe_Screw_C")
    assert screws.node_id == "node_Desc_Screw_C_2"
    rods_to_screws = next(f for f in graph.flows if f.item_id == "Desc_IronRod_C")
    assert rods_to_screws.target_node_id == "node_Desc_Screw_C_2"
