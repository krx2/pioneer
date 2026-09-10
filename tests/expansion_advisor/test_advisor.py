"""Tests for the expansion diff, against hand-built "existing" and "from scratch" graphs — no
call to the Production Planner (the target graph below is a Stage 7 example output copied in as a
fixture, per implementation.md Stage 8)."""

from pioneer.contracts import ChangeAction, ProductionGraph, ProductionNode
from pioneer.expansion_advisor.advisor import advise_expansion


def _node(node_id: str, recipe_id: str, machine_count: float, *, is_existing: bool = False):
    return ProductionNode(
        node_id=node_id,
        recipe_id=recipe_id,
        building_id="Build_Whatever_C",
        machine_count=machine_count,
        is_existing=is_existing,
    )


# From-scratch plan for 10/min Reinforced Iron Plate (a Stage 7 output, copied in as a fixture).
_TARGET = ProductionGraph(
    nodes=(
        _node("node_Desc_IronIngot_C", "Recipe_IngotIron_C", 4),
        _node("node_Desc_IronPlate_C", "Recipe_IronPlate_C", 3),
        _node("node_Desc_IronRod_C", "Recipe_IronRod_C", 2),
        _node("node_Desc_Screw_C", "Recipe_Screw_C", 3),
        _node("node_Desc_IronPlateReinforced_C", "Recipe_IronPlateReinforced_C", 2),
    ),
    flows=(),
)


def _by_recipe(change_set, recipe_id):
    return next(c for c in change_set.changes if c.recipe_id == recipe_id)


def _result_node(graph: ProductionGraph, recipe_id: str) -> ProductionNode:
    return next(n for n in graph.nodes if n.recipe_id == recipe_id)


def test_extends_short_factories_and_adds_missing_stages() -> None:
    existing = ProductionGraph(
        nodes=(
            _node("smelters", "Recipe_IngotIron_C", 2, is_existing=True),  # need 4 -> extend +2
            _node("plates", "Recipe_IronPlate_C", 3, is_existing=True),  # need 3 -> sufficient
        ),
        flows=(),
    )

    result = advise_expansion(existing, _TARGET)

    actions = {c.recipe_id: c.action for c in result.changes}
    assert actions == {
        "Recipe_IngotIron_C": ChangeAction.EXTEND,
        "Recipe_IronRod_C": ChangeAction.ADD,
        "Recipe_Screw_C": ChangeAction.ADD,
        "Recipe_IronPlateReinforced_C": ChangeAction.ADD,
    }
    assert "Recipe_IronPlate_C" not in actions  # already sufficient -> no change


def test_extend_amount_and_anchor() -> None:
    existing = ProductionGraph(
        nodes=(_node("smelters", "Recipe_IngotIron_C", 2, is_existing=True),), flows=()
    )

    result = advise_expansion(existing, _TARGET)
    extend = _by_recipe(result, "Recipe_IngotIron_C")

    assert extend.action is ChangeAction.EXTEND
    assert extend.additional_machine_count == 2  # 4 needed - 2 existing
    assert extend.target_node_id == "smelters"


def test_add_amount_and_no_anchor() -> None:
    result = advise_expansion(ProductionGraph(nodes=(), flows=()), _TARGET)
    add = _by_recipe(result, "Recipe_Screw_C")

    assert add.action is ChangeAction.ADD
    assert add.additional_machine_count == 3
    assert add.target_node_id is None


def test_resulting_graph_merges_and_flags_nodes() -> None:
    existing = ProductionGraph(
        nodes=(_node("smelters", "Recipe_IngotIron_C", 2, is_existing=True),), flows=()
    )

    result = advise_expansion(existing, _TARGET)
    graph = result.resulting_graph

    assert len(graph.nodes) == 5
    smelter = _result_node(graph, "Recipe_IngotIron_C")
    assert smelter.machine_count == 4  # 2 existing + 2 from the extend
    assert smelter.is_existing is True
    screw = _result_node(graph, "Recipe_Screw_C")
    assert screw.machine_count == 3
    assert screw.is_existing is False


def test_fully_sufficient_existing_produces_no_changes() -> None:
    existing = ProductionGraph(
        nodes=(
            _node("s", "Recipe_IngotIron_C", 10, is_existing=True),
            _node("p", "Recipe_IronPlate_C", 10, is_existing=True),
            _node("r", "Recipe_IronRod_C", 10, is_existing=True),
            _node("sc", "Recipe_Screw_C", 10, is_existing=True),
            _node("rip", "Recipe_IronPlateReinforced_C", 10, is_existing=True),
        ),
        flows=(),
    )

    result = advise_expansion(existing, _TARGET)

    assert result.changes == ()
    assert len(result.resulting_graph.nodes) == 5
    assert all(n.is_existing for n in result.resulting_graph.nodes)


def test_capacity_sums_across_multiple_existing_nodes_for_a_recipe() -> None:
    existing = ProductionGraph(
        nodes=(
            _node("smelters_a", "Recipe_IngotIron_C", 1, is_existing=True),
            _node("smelters_b", "Recipe_IngotIron_C", 2, is_existing=True),
        ),
        flows=(),
    )

    result = advise_expansion(existing, _TARGET)
    extend = _by_recipe(result, "Recipe_IngotIron_C")

    assert extend.additional_machine_count == 1  # 4 needed - (1 + 2) existing
    assert extend.target_node_id == "smelters_a"  # first node running the recipe


def test_prefers_extend_over_a_second_parallel_factory() -> None:
    existing = ProductionGraph(
        nodes=(_node("smelters", "Recipe_IngotIron_C", 1, is_existing=True),), flows=()
    )

    result = advise_expansion(existing, _TARGET)
    ingot_changes = [c for c in result.changes if c.recipe_id == "Recipe_IngotIron_C"]

    ingot_nodes = [n for n in result.resulting_graph.nodes if n.recipe_id == "Recipe_IngotIron_C"]
    assert len(ingot_changes) == 1
    assert ingot_changes[0].action is ChangeAction.EXTEND
    assert len(ingot_nodes) == 1


def test_resulting_graph_flows_come_from_target() -> None:
    target = ProductionGraph(
        nodes=(_node("n", "R", 1),),
        flows=_TARGET.flows,
    )
    result = advise_expansion(ProductionGraph(nodes=(), flows=()), target)
    assert result.resulting_graph.flows is target.flows
