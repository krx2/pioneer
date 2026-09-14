"""Tests for the Stage 15 scoring functions, against hand-built `ResponseArtifact`-shaped fixtures
per channel, with known-correct expected scores (implementation.md Stage 15) — no live
orchestrator, no rendered UI."""

from pioneer.contracts import (
    Building,
    Coordinates,
    Feedback,
    ItemAmount,
    ProductionGraph,
    ProductionNode,
    Purity,
    RankedLocation,
    Recipe,
    ResourceNode,
    ResponseArtifact,
)
from pioneer.verification_feedback.scoring import (
    JudgeVerdict,
    check_rag_consistency,
    score_graph,
    score_map,
    score_response,
)

# --- Chat channel ----------------------------------------------------------------------------

_PASSAGES = (
    "The Smelter converts Iron Ore into Iron Ingots at a 1:1 ratio.",
    "Reinforced Iron Plate requires Iron Plate and Screw in an Assembler.",
)


def test_chat_grounded_in_the_cited_passages_is_consistent() -> None:
    score = check_rag_consistency("The Smelter converts Iron Ore into Iron Ingots.", _PASSAGES)

    assert score.consistent
    assert score.grounded_fraction == 1.0


def test_chat_with_an_unsupported_claim_is_inconsistent() -> None:
    score = check_rag_consistency(
        "The Smelter converts Iron Ore into Iron Ingots, and also prints Turbofuel for free.",
        _PASSAGES,
    )

    assert not score.consistent
    assert score.grounded_fraction < 1.0


def test_empty_chat_is_trivially_consistent() -> None:
    score = check_rag_consistency("", _PASSAGES)

    assert score.consistent
    assert score.grounded_fraction == 1.0


def test_qualitative_score_defaults_to_none() -> None:
    score = check_rag_consistency("Smelter Iron Ore Iron Ingot", _PASSAGES)

    assert score.qualitative_score is None


# --- Graph channel ---------------------------------------------------------------------------

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
_RECIPES = (_IRON_INGOT, _IRON_PLATE)

_SMELTER = Building(
    building_id="Build_SmelterMk1_C",
    name="Smelter",
    power_consumption_mw=4,
    input_slots=1,
    output_slots=1,
)
_CONSTRUCTOR = Building(
    building_id="Build_ConstructorMk1_C",
    name="Constructor",
    power_consumption_mw=4,
    input_slots=1,
    output_slots=1,
)
_BUILDINGS = (_SMELTER, _CONSTRUCTOR)


def _node(node_id: str, recipe_id: str, machine_count: float, building_id: str) -> ProductionNode:
    return ProductionNode(
        node_id=node_id, recipe_id=recipe_id, building_id=building_id, machine_count=machine_count
    )


def _single_smelter_graph(machine_count: float) -> ProductionGraph:
    return ProductionGraph(
        nodes=(_node("smelter", "Recipe_IngotIron_C", machine_count, "Build_SmelterMk1_C"),),
        flows=(),
    )


def test_balanced_graph_with_enough_smelters_passes() -> None:
    graph = ProductionGraph(
        nodes=(
            _node("smelter", "Recipe_IngotIron_C", 1, "Build_SmelterMk1_C"),
            _node("plate", "Recipe_IronPlate_C", 1, "Build_ConstructorMk1_C"),
        ),
        flows=(),
    )

    score = score_graph(graph, _RECIPES, _BUILDINGS, raw_item_ids=("Desc_OreIron_C",))

    assert score.balanced
    assert score.passed


def test_under_provisioned_stage_is_unbalanced() -> None:
    graph = ProductionGraph(
        nodes=(
            _node("smelter", "Recipe_IngotIron_C", 1, "Build_SmelterMk1_C"),
            _node("plate", "Recipe_IronPlate_C", 2, "Build_ConstructorMk1_C"),  # needs 2x ingots
        ),
        flows=(),
    )

    score = score_graph(graph, _RECIPES, _BUILDINGS, raw_item_ids=("Desc_OreIron_C",))

    assert not score.balanced
    assert not score.passed


def test_raw_item_deficit_is_excluded_from_the_balance_check() -> None:
    graph = _single_smelter_graph(1)

    # Without declaring Desc_OreIron_C as raw, its consumption would look like an unbacked deficit.
    without_raw_declared = score_graph(graph, _RECIPES, _BUILDINGS)
    with_raw_declared = score_graph(graph, _RECIPES, _BUILDINGS, raw_item_ids=("Desc_OreIron_C",))

    assert not without_raw_declared.balanced
    assert with_raw_declared.balanced


def test_power_draw_within_budget_passes() -> None:
    graph = _single_smelter_graph(2)

    score = score_graph(
        graph, _RECIPES, _BUILDINGS, raw_item_ids=("Desc_OreIron_C",), available_power_mw=10
    )

    assert score.power_ok


def test_power_draw_exceeding_budget_fails() -> None:
    graph = _single_smelter_graph(3)

    score = score_graph(
        graph, _RECIPES, _BUILDINGS, raw_item_ids=("Desc_OreIron_C",), available_power_mw=10
    )

    assert not score.power_ok
    assert not score.passed


def test_no_power_budget_given_never_fails_on_power() -> None:
    graph = _single_smelter_graph(99)

    score = score_graph(graph, _RECIPES, _BUILDINGS, raw_item_ids=("Desc_OreIron_C",))

    assert score.power_ok


def test_deviation_from_optimum_is_none_without_a_reference_graph() -> None:
    graph = _single_smelter_graph(1)

    score = score_graph(graph, _RECIPES, _BUILDINGS, raw_item_ids=("Desc_OreIron_C",))

    assert score.deviation_from_optimum_pct is None


def test_deviation_from_optimum_matches_hand_calculated_percentage() -> None:
    optimal = _single_smelter_graph(4)
    actual = _single_smelter_graph(3)

    score = score_graph(
        actual, _RECIPES, _BUILDINGS, raw_item_ids=("Desc_OreIron_C",), optimal_graph=optimal
    )

    # |3 - 4| / 4 * 100 = 25%
    assert score.deviation_from_optimum_pct == 25.0


# --- Map channel -----------------------------------------------------------------------------

_NODE = ResourceNode(
    node_id="iron_pure_1",
    item_id="Desc_OreIron_C",
    purity=Purity.PURE,
    position=Coordinates(x=1000, y=2000),
)
_NODES = (_NODE,)


def test_matching_distance_and_purity_passes() -> None:
    location = RankedLocation(
        resource_node_id="iron_pure_1",
        position=Coordinates(x=1000, y=2000),
        purity=Purity.PURE,
        distance_to_reference=500.0,
        score=0.9,
    )

    (result,) = score_map((location,), _NODES)

    assert result.distance_ok
    assert result.purity_ok
    assert result.passed


def test_mismatched_purity_fails() -> None:
    location = RankedLocation(
        resource_node_id="iron_pure_1",
        position=Coordinates(x=1000, y=2000),
        purity=Purity.NORMAL,  # real node is PURE
        distance_to_reference=500.0,
        score=0.9,
    )

    (result,) = score_map((location,), _NODES)

    assert not result.purity_ok
    assert not result.passed


def test_position_far_from_the_real_node_fails_distance_check() -> None:
    location = RankedLocation(
        resource_node_id="iron_pure_1",
        position=Coordinates(x=5000, y=5000),  # nowhere near the real node
        purity=Purity.PURE,
        distance_to_reference=500.0,
        score=0.9,
    )

    (result,) = score_map((location,), _NODES)

    assert not result.distance_ok
    assert not result.passed


def test_unknown_resource_node_id_fails_closed() -> None:
    location = RankedLocation(
        resource_node_id="does_not_exist",
        position=Coordinates(x=1000, y=2000),
        purity=Purity.PURE,
        distance_to_reference=500.0,
        score=0.9,
    )

    (result,) = score_map((location,), _NODES)

    assert not result.distance_ok
    assert not result.purity_ok
    assert not result.passed
    assert result.judge_verdict is None


def test_judge_hook_is_invoked_and_its_verdict_is_attached() -> None:
    location = RankedLocation(
        resource_node_id="iron_pure_1",
        position=Coordinates(x=1000, y=2000),
        purity=Purity.PURE,
        distance_to_reference=500.0,
        score=0.9,
    )
    calls = []

    def fake_judge(loc, context):
        calls.append((loc, context))
        return JudgeVerdict(fits_context=False, rationale="too close to a lake")

    (result,) = score_map((location,), _NODES, judge=fake_judge, judge_context="near water")

    assert calls == [(location, "near water")]
    assert result.judge_verdict is not None
    assert result.judge_verdict.fits_context is False


def test_no_judge_given_leaves_verdict_none() -> None:
    location = RankedLocation(
        resource_node_id="iron_pure_1",
        position=Coordinates(x=1000, y=2000),
        purity=Purity.PURE,
        distance_to_reference=500.0,
        score=0.9,
    )

    (result,) = score_map((location,), _NODES)

    assert result.judge_verdict is None


# --- score_response (combined) ----------------------------------------------------------------


def test_score_response_only_scores_populated_channels() -> None:
    artifact = ResponseArtifact(
        response_id="r1", chat="The Smelter converts Iron Ore into Iron Ingots."
    )

    result = score_response(artifact, cited_passages=_PASSAGES)

    assert result.chat is not None
    assert result.chat.consistent
    assert result.graph is None
    assert result.map is None


def test_score_response_carries_player_feedback_into_chat_score() -> None:
    artifact = ResponseArtifact(
        response_id="r2",
        chat="The Smelter converts Iron Ore into Iron Ingots.",
        feedback=Feedback(qualitative_score=4),
    )

    result = score_response(artifact, cited_passages=_PASSAGES)

    assert result.chat.qualitative_score == 4


def test_score_response_scores_the_graph_channel() -> None:
    artifact = ResponseArtifact(response_id="r3", graph=_single_smelter_graph(1))

    result = score_response(
        artifact, recipes=_RECIPES, buildings=_BUILDINGS, raw_item_ids=("Desc_OreIron_C",)
    )

    assert result.graph is not None
    assert result.graph.balanced


def test_score_response_scores_the_map_channel() -> None:
    location = RankedLocation(
        resource_node_id="iron_pure_1",
        position=Coordinates(x=1000, y=2000),
        purity=Purity.PURE,
        distance_to_reference=500.0,
        score=0.9,
    )
    artifact = ResponseArtifact(response_id="r4", map_locations=(location,))

    result = score_response(artifact, resource_nodes=_NODES)

    assert result.map is not None
    assert result.map[0].passed
