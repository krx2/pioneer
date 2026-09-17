"""Tests for the Orchestrator's verification step, against hand-built artifacts and contexts."""

from dataclasses import replace

import pytest

from pioneer.contracts import (
    Building,
    Coordinates,
    Feedback,
    ItemAmount,
    MaterialFlow,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
    Purity,
    RankedLocation,
    Recipe,
    ResourceNode,
    ResponseArtifact,
)
from pioneer.orchestrator import OrchestratorContext, verify_response
from pioneer.verification_feedback import JudgeVerdict

_PLATE = Recipe(
    recipe_id="Recipe_IronPlate_C",
    name="Iron Plate",
    building_ids=("Build_ConstructorMk1_C",),
    inputs=(ItemAmount(item_id="Desc_IronIngot_C", amount_per_minute=30),),
    outputs=(ItemAmount(item_id="Desc_IronPlate_C", amount_per_minute=20),),
)
_CONSTRUCTOR = Building(
    building_id="Build_ConstructorMk1_C",
    name="Constructor",
    power_consumption_mw=4,
    input_slots=1,
    output_slots=1,
)
_CONTEXT = OrchestratorContext(recipes=(_PLATE,), buildings=(_CONSTRUCTOR,))
_PLAN = ProductionGraph(
    nodes=(
        ProductionNode(
            node_id="node_Desc_IronPlate_C",
            recipe_id="Recipe_IronPlate_C",
            building_id="Build_ConstructorMk1_C",
            machine_count=1,
        ),
    ),
    flows=(
        MaterialFlow(  # ingots come from outside the plan
            item_id="Desc_IronIngot_C",
            amount_per_minute=30,
            target_node_id="node_Desc_IronPlate_C",
        ),
        MaterialFlow(
            item_id="Desc_IronPlate_C",
            amount_per_minute=20,
            source_node_id="node_Desc_IronPlate_C",
        ),
    ),
)
_NODE = ResourceNode(
    node_id="n", item_id="Desc_OreIron_C", purity=Purity.PURE, position=Coordinates(x=1, y=2)
)
_SITE = RankedLocation(
    resource_node_id="n",
    position=_NODE.position,
    purity=Purity.NORMAL,  # the node data says pure
    distance_to_reference=0.0,
    score=1.0,
)


def test_the_chat_is_checked_against_its_grounding() -> None:
    artifact = ResponseArtifact(
        response_id="r",
        chat="Iron Plate needs a Constructor.",
        grounding=('{"machine_counts": {"Recipe_IronPlate_C": 1}}\nIron Plate, Constructor',),
    )

    score = verify_response(artifact, _CONTEXT)

    assert score.chat is not None
    assert score.chat.grounded_fraction == 0.75  # "needs" is the only word not grounded
    assert score.chat.consistent
    assert (score.graph, score.map) == (None, None)


def test_a_plans_inputs_from_outside_are_not_held_against_it() -> None:
    score = verify_response(ResponseArtifact(response_id="r", graph=_PLAN), _CONTEXT)

    assert score.graph is not None
    assert score.graph.balanced
    assert score.graph.passed


def test_a_graph_the_data_cannot_score_is_left_unscored_without_failing_the_rest() -> None:
    no_constructor = OrchestratorContext(
        recipes=(_PLATE,),
        buildings=(
            Building(
                building_id="Build_SmelterMk1_C",
                name="Smelter",
                power_consumption_mw=4,
                input_slots=1,
                output_slots=1,
            ),
        ),
    )
    artifact = ResponseArtifact(
        response_id="r", chat="Iron Plate.", graph=_PLAN, grounding=("Iron Plate",)
    )

    score = verify_response(artifact, no_constructor)

    assert score.graph is None
    assert score.chat is not None and score.chat.consistent


def test_map_sites_are_checked_against_the_node_data() -> None:
    artifact = ResponseArtifact(response_id="r", map_locations=(_SITE,))

    score = verify_response(artifact, OrchestratorContext(resource_nodes=(_NODE,)))

    assert score.map is not None
    (site,) = score.map
    assert site.position_ok
    assert not site.purity_ok
    assert not site.passed


def test_map_distances_are_checked_from_the_reference_the_answer_used() -> None:
    """The site claims 0 from its reference, but the node is 5 from (4, 6) — and a miner in the
    save already works it."""
    miner = PlacementRecord(
        building_id="Build_MinerMk1_C", position=_NODE.position, resource_node_id="n"
    )
    site = RankedLocation(
        resource_node_id="n",
        position=_NODE.position,
        purity=Purity.PURE,
        distance_to_reference=0.0,
        score=1.0,
    )
    artifact = ResponseArtifact(
        response_id="r", map_locations=(site,), map_reference=Coordinates(x=4, y=6)
    )
    context = OrchestratorContext(resource_nodes=(_NODE,), existing_placements=(miner,))

    (checked,) = verify_response(artifact, context).map or ()

    assert checked.position_ok and checked.purity_ok
    assert checked.distance_ok is False
    assert not checked.still_free
    assert not checked.passed


def test_a_made_up_number_makes_the_chat_inconsistent() -> None:
    artifact = ResponseArtifact(
        response_id="r",
        chat="You need 3 Constructors.",
        grounding=('{"machine_counts": {"Recipe_IronPlate_C": 1}}',),
    )

    score = verify_response(artifact, _CONTEXT)

    assert score.chat is not None
    assert not score.chat.consistent
    assert score.chat.ungrounded_numbers == ("3",)


def test_a_whole_machine_plan_is_measured_against_its_fractional_optimum() -> None:
    """30 plates/min takes 1.5 constructors; the plan builds 2 — a third more than needed."""
    plan = ProductionGraph(
        nodes=(replace(_PLAN.nodes[0], machine_count=2),),
        flows=(
            MaterialFlow(
                item_id="Desc_IronIngot_C",
                amount_per_minute=60,
                target_node_id="node_Desc_IronPlate_C",
            ),
            MaterialFlow(
                item_id="Desc_IronPlate_C",
                amount_per_minute=30,
                source_node_id="node_Desc_IronPlate_C",
            ),
        ),
    )

    score = verify_response(ResponseArtifact(response_id="r", graph=plan), _CONTEXT)

    assert score.graph is not None
    assert score.graph.deviation_from_optimum_pct == pytest.approx(100 / 3)
    assert score.graph.available_power_mw is None  # no save: nothing to judge power against
    assert score.graph.power_ok


_BURNER = Building(
    building_id="Build_GeneratorBiomass_C",
    name="Biomass Burner",
    power_consumption_mw=-30,
    input_slots=1,
    output_slots=0,
)


def test_an_expansion_is_scored_on_the_machines_it_adds_against_spare_power() -> None:
    """The save runs 3 constructors (12 MW) on one 30 MW burner: 18 MW to spare. Extending them
    by 5 (100 plates/min) adds 20 MW — more than the grid has — however many already run."""
    existing_node = ProductionNode(
        node_id="save_Recipe_IronPlate_C",
        recipe_id="Recipe_IronPlate_C",
        building_id="Build_ConstructorMk1_C",
        machine_count=3,
        is_existing=True,
    )
    context = OrchestratorContext(
        recipes=(_PLATE,),
        buildings=(_CONSTRUCTOR, _BURNER),
        existing_graph=ProductionGraph(nodes=(existing_node,), flows=()),
        existing_placements=(
            *(
                PlacementRecord(
                    building_id="Build_ConstructorMk1_C",
                    position=Coordinates(x=0, y=0),
                    recipe_id="Recipe_IronPlate_C",
                )
                for _ in range(3)
            ),
            PlacementRecord(building_id="Build_GeneratorBiomass_C", position=Coordinates(x=0, y=0)),
        ),
    )
    expansion = ProductionGraph(
        nodes=(replace(existing_node, machine_count=8, existing_machine_count=3),),
        flows=(
            MaterialFlow(
                item_id="Desc_IronIngot_C",
                amount_per_minute=150,
                target_node_id="save_Recipe_IronPlate_C",
            ),
            MaterialFlow(
                item_id="Desc_IronPlate_C",
                amount_per_minute=100,
                source_node_id="save_Recipe_IronPlate_C",
            ),
        ),
    )

    score = verify_response(ResponseArtifact(response_id="r", graph=expansion), context)

    assert score.graph is not None
    assert score.graph.balanced
    assert score.graph.deviation_from_optimum_pct == pytest.approx(0)
    assert (score.graph.power_draw_mw, score.graph.available_power_mw) == (20, 18)
    assert not score.graph.power_ok
    assert not score.graph.passed


def test_judges_are_consulted_with_the_question_and_what_is_known() -> None:
    calls = []

    def chat_judge(question, answer, context):
        calls.append(("chat", question, answer, context))
        return JudgeVerdict(fits_context=True, rationale="Fine.")

    def terrain_judge(location, context):
        calls.append(("map", location.resource_node_id, context))
        return None

    context = OrchestratorContext(
        resource_nodes=(_NODE,),
        existing_placements=(
            PlacementRecord(building_id="Build_ConstructorMk1_C", position=Coordinates(x=100, y=0)),
        ),
    )
    artifact = ResponseArtifact(
        response_id="r", chat="ok", question="where?", map_locations=(_SITE,)
    )

    score = verify_response(artifact, context, chat_judge=chat_judge, terrain_judge=terrain_judge)

    assert score.chat is not None and score.chat.judge_verdict == JudgeVerdict(True, "Fine.")
    assert score.map is not None and score.map[0].judge_verdict is None
    chat_call, map_call = calls
    assert chat_call[1:3] == ("where?", "ok")
    assert "Data available" in chat_call[3]
    assert map_call[1] == "n"
    assert "1 buildings" in map_call[2]


def test_the_players_rating_is_carried_into_the_chat_score() -> None:
    artifact = ResponseArtifact(
        response_id="r", chat="ok", grounding=("ok",), feedback=Feedback(qualitative_score=4)
    )

    score = verify_response(artifact, _CONTEXT)

    assert score.chat is not None and score.chat.qualitative_score == 4
