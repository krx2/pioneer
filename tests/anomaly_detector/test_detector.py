"""Tests for the anomaly classifier, against hand-built graphs + hand-supplied balance/power
numbers (as if from the Verifier). The core fixture is a deliberately under-provisioned smelter
stage, per implementation.md Stage 10."""

from pioneer.anomaly_detector.detector import detect_anomalies
from pioneer.contracts import (
    AnomalyKind,
    AnomalySeverity,
    MaterialFlow,
    ProductionGraph,
    ProductionNode,
)


def _node(node_id: str, recipe_id: str, machine_count: float) -> ProductionNode:
    return ProductionNode(
        node_id=node_id, recipe_id=recipe_id, building_id="b", machine_count=machine_count
    )


# 1 smelter (30 ingot/min) feeding 3 constructors that need 90 ingot/min -> 60/min short.
_SHORT_GRAPH = ProductionGraph(
    nodes=(
        _node("smelters", "Recipe_IngotIron_C", 1),
        _node("plates", "Recipe_IronPlate_C", 3),
    ),
    flows=(
        MaterialFlow(item_id="Desc_OreIron_C", amount_per_minute=30, target_node_id="smelters"),
        MaterialFlow(
            item_id="Desc_IronIngot_C",
            amount_per_minute=90,
            source_node_id="smelters",
            target_node_id="plates",
        ),
        MaterialFlow(item_id="Desc_IronPlate_C", amount_per_minute=60, source_node_id="plates"),
    ),
)
_SHORT_BALANCE = {
    "Desc_OreIron_C": -30.0,  # raw input, has a boundary inflow -> not an anomaly
    "Desc_IronIngot_C": -60.0,  # 30 produced - 90 consumed
    "Desc_IronPlate_C": 60.0,  # boundary output -> not an anomaly
}


def test_under_provisioned_smelters_flags_one_deficit() -> None:
    anomalies = detect_anomalies(_SHORT_GRAPH, _SHORT_BALANCE, net_power_draw_mw=0)

    assert len(anomalies) == 1
    (deficit,) = anomalies
    assert deficit.kind is AnomalyKind.RESOURCE_DEFICIT
    assert deficit.item_id == "Desc_IronIngot_C"
    assert deficit.node_id == "plates"  # the single consumer of the short item
    assert deficit.severity is AnomalySeverity.MEDIUM  # 60 short / 90 demand = 0.67


def test_raw_input_with_boundary_inflow_is_not_a_deficit() -> None:
    anomalies = detect_anomalies(_SHORT_GRAPH, _SHORT_BALANCE, net_power_draw_mw=0)
    assert not any(a.item_id == "Desc_OreIron_C" for a in anomalies)


def test_intended_output_with_boundary_outflow_is_not_a_surplus() -> None:
    anomalies = detect_anomalies(_SHORT_GRAPH, _SHORT_BALANCE, net_power_draw_mw=0)
    assert not any(a.kind is AnomalyKind.RESOURCE_SURPLUS for a in anomalies)


def test_balanced_graph_has_no_anomalies() -> None:
    balance = {"Desc_OreIron_C": -120.0, "Desc_IronIngot_C": 0.0, "Desc_IronPlate_C": 20.0}
    graph = ProductionGraph(
        nodes=(_node("s", "R", 4),),
        flows=(
            MaterialFlow(item_id="Desc_OreIron_C", amount_per_minute=120, target_node_id="s"),
            MaterialFlow(item_id="Desc_IronPlate_C", amount_per_minute=20, source_node_id="s"),
        ),
    )
    assert detect_anomalies(graph, balance, net_power_draw_mw=50) == ()


def test_deficit_without_flow_info_needs_the_raw_hint() -> None:
    graph = ProductionGraph(nodes=(), flows=())
    balance = {"Desc_OreIron_C": -120.0, "Desc_IronIngot_C": -60.0}

    # No hint, no flows -> both bare negatives look like shortages.
    both = detect_anomalies(graph, balance, net_power_draw_mw=0)
    assert {a.item_id for a in both} == {"Desc_OreIron_C", "Desc_IronIngot_C"}
    assert all(a.severity is AnomalySeverity.MEDIUM for a in both)  # no derivable demand

    # Tell it ore is raw -> only the ingot shortage remains.
    with_hint = detect_anomalies(
        graph, balance, net_power_draw_mw=0, raw_item_ids=["Desc_OreIron_C"]
    )
    assert [a.item_id for a in with_hint] == ["Desc_IronIngot_C"]


def test_surplus_of_unconsumed_intermediate() -> None:
    graph = ProductionGraph(nodes=(), flows=())
    balance = {"Desc_IronIngot_C": 40.0}

    (surplus,) = detect_anomalies(graph, balance, net_power_draw_mw=0)

    assert surplus.kind is AnomalyKind.RESOURCE_SURPLUS
    assert surplus.item_id == "Desc_IronIngot_C"
    assert surplus.severity is AnomalySeverity.LOW


def test_power_blackout_only_when_a_budget_is_given() -> None:
    graph = ProductionGraph(nodes=(), flows=())

    no_budget = detect_anomalies(graph, {}, net_power_draw_mw=9999)
    assert no_budget == ()

    over = detect_anomalies(graph, {}, net_power_draw_mw=250, available_power_mw=100)
    (blackout,) = over
    assert blackout.kind is AnomalyKind.POWER_BLACKOUT
    assert blackout.severity is AnomalySeverity.HIGH  # 150 over / 100 budget = 1.5


def test_power_within_budget_is_fine() -> None:
    graph = ProductionGraph(nodes=(), flows=())
    assert detect_anomalies(graph, {}, net_power_draw_mw=90, available_power_mw=100) == ()


def test_congestion_on_an_over_capacity_flow() -> None:
    graph = ProductionGraph(
        nodes=(),
        flows=(
            MaterialFlow(
                item_id="Desc_OreIron_C",
                amount_per_minute=1600,
                source_node_id="miners",
                target_node_id="smelters",
            ),
            MaterialFlow(
                item_id="Desc_IronIngot_C",
                amount_per_minute=200,
                source_node_id="smelters",
                target_node_id="plates",
            ),
        ),
    )

    anomalies = detect_anomalies(graph, {}, net_power_draw_mw=0, belt_capacity_per_minute=780)

    congestion = [a for a in anomalies if a.kind is AnomalyKind.CONGESTION]
    assert len(congestion) == 1
    assert congestion[0].item_id == "Desc_OreIron_C"
    assert congestion[0].node_id == "smelters"
    assert congestion[0].severity is AnomalySeverity.HIGH  # (1600-780)/780 = 1.05


def test_record_ordering_is_deficits_then_power_then_congestion_then_surplus() -> None:
    graph = ProductionGraph(
        nodes=(),
        flows=(MaterialFlow(item_id="belt_item", amount_per_minute=1000, target_node_id="n"),),
    )
    balance = {"short_item": -10.0, "extra_item": 10.0}

    anomalies = detect_anomalies(
        graph,
        balance,
        net_power_draw_mw=200,
        available_power_mw=100,
        belt_capacity_per_minute=780,
    )
    kinds = [a.kind for a in anomalies]
    assert kinds == [
        AnomalyKind.RESOURCE_DEFICIT,
        AnomalyKind.POWER_BLACKOUT,
        AnomalyKind.CONGESTION,
        AnomalyKind.RESOURCE_SURPLUS,
    ]
