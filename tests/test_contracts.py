"""Stage 1 smoke tests: every contract is importable and constructs with sane sample data.

Not domain logic — contracts have none. This just proves the shapes are well-formed and that
`pioneer.contracts` re-exports everything a module would need.
"""

from pioneer import contracts as c


def test_recipe_and_friends() -> None:
    iron_ingot = c.ItemAmount(item_id="iron_ore", amount_per_minute=30)
    iron_rod = c.ItemAmount(item_id="iron_rod", amount_per_minute=15)
    smelter = c.Building(
        building_id="smelter", name="Smelter", power_consumption_mw=4, input_slots=1, output_slots=1
    )
    tech = c.Technology(technology_id="tier_1", name="Tier 1", tier=1)
    recipe = c.Recipe(
        recipe_id="iron_rod",
        name="Iron Rod",
        building_id=smelter.building_id,
        inputs=(iron_ingot,),
        outputs=(iron_rod,),
        unlocked_by=tech.technology_id,
    )
    assert recipe.inputs[0].item_id == "iron_ore"


def test_production_graph() -> None:
    node = c.ProductionNode(
        node_id="n1", recipe_id="iron_rod", building_id="constructor", machine_count=2
    )
    flow = c.MaterialFlow(item_id="iron_rod", amount_per_minute=15, source_node_id="n1")
    graph = c.ProductionGraph(nodes=(node,), flows=(flow,))
    assert graph.nodes[0].is_existing is False


def test_resource_node_and_placement() -> None:
    position = c.Coordinates(x=1.0, y=2.0)
    node = c.ResourceNode(node_id="r1", item_id="iron_ore", purity=c.Purity.PURE, position=position)
    placement = c.PlacementRecord(building_id="miner", position=position, recipe_id="iron_ore")
    assert node.purity == c.Purity.PURE
    assert placement.position == position


def test_game_state() -> None:
    state = c.GameState(phase="tier_3", progress_percent=42.0)
    assert state.session_name is None


def test_change_set() -> None:
    graph = c.ProductionGraph(nodes=(), flows=())
    change = c.ChangeItem(
        action=c.ChangeAction.EXTEND,
        recipe_id="iron_rod",
        additional_machine_count=1,
        target_node_id="n1",
    )
    change_set = c.ChangeSet(changes=(change,), resulting_graph=graph)
    assert change_set.changes[0].action == c.ChangeAction.EXTEND


def test_anomaly_record() -> None:
    anomaly = c.AnomalyRecord(
        kind=c.AnomalyKind.POWER_BLACKOUT,
        severity=c.AnomalySeverity.HIGH,
        description="Grid is short 40 MW",
    )
    assert anomaly.item_id is None


def test_ranked_location() -> None:
    location = c.RankedLocation(
        resource_node_id="r1",
        position=c.Coordinates(x=0, y=0),
        purity=c.Purity.NORMAL,
        distance_to_reference=123.4,
        score=0.8,
    )
    assert location.purity == c.Purity.NORMAL


def test_response_artifact_and_feedback() -> None:
    feedback = c.Feedback(thumbs_up=True)
    artifact = c.ResponseArtifact(
        response_id="resp-1", chat="Build two more constructors.", feedback=feedback
    )
    assert artifact.feedback.thumbs_up is True


def test_intent() -> None:
    intent = c.Intent(kind=c.IntentKind.NEW_PLAN, raw_query="I want 10/min of screws")
    assert intent.target_item_id is None
