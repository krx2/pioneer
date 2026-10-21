"""Tests for what a revision of the player's plan changes, against hand-built versions of a plan --
no call to the Production Planner."""

from pioneer.contracts import (
    ChangeAction,
    ChangeItem,
    MaterialFlow,
    ProductionGraph,
    ProductionNode,
)
from pioneer.expansion_advisor import plan_changes

_SMELTER = "Build_SmelterMk1_C"
_FOUNDRY = "Build_FoundryMk1_C"
_CONSTRUCTOR = "Build_ConstructorMk1_C"


def _node(item_id: str, recipe_id: str, building_id: str, machines: float, standing: float = 0):
    return ProductionNode(
        node_id=f"node_{item_id}",
        recipe_id=recipe_id,
        building_id=building_id,
        machine_count=machines,
        is_existing=standing > 0,
        existing_machine_count=standing,
    )


def _plan(*stages: ProductionNode) -> ProductionGraph:
    """`stages` in order, each one's item flowing out of it -- what tells a stage's item."""
    flows = tuple(
        MaterialFlow(
            item_id=node.node_id.removeprefix("node_"),
            amount_per_minute=10,
            source_node_id=node.node_id,
        )
        for node in stages
    )
    return ProductionGraph(nodes=stages, flows=flows)


_PLATES = _node("Desc_IronPlate_C", "Recipe_IronPlate_C", _CONSTRUCTOR, 3)
_INGOTS = _node("Desc_IronIngot_C", "Recipe_IngotIron_C", _SMELTER, 4)


def test_the_same_plan_changes_nothing() -> None:
    assert plan_changes(_plan(_PLATES, _INGOTS), _plan(_PLATES, _INGOTS)) == ()


def test_more_or_fewer_machines_of_a_stage_extend_or_remove_it() -> None:
    after = _plan(
        _node("Desc_IronPlate_C", "Recipe_IronPlate_C", _CONSTRUCTOR, 5),
        _node("Desc_IronIngot_C", "Recipe_IngotIron_C", _SMELTER, 3),
    )

    assert plan_changes(_plan(_PLATES, _INGOTS), after) == (
        ChangeItem(
            ChangeAction.EXTEND,
            "Recipe_IronPlate_C",
            2,
            "node_Desc_IronPlate_C",
            building_id=_CONSTRUCTOR,
        ),
        ChangeItem(
            ChangeAction.REMOVE,
            "Recipe_IngotIron_C",
            -1,
            "node_Desc_IronIngot_C",
            building_id=_SMELTER,
        ),
    )


def test_a_new_stage_is_added_and_a_dropped_one_removed() -> None:
    rods = _node("Desc_IronRod_C", "Recipe_IronRod_C", _CONSTRUCTOR, 2)

    assert plan_changes(_plan(_PLATES, _INGOTS), _plan(rods, _INGOTS)) == (
        ChangeItem(ChangeAction.ADD, "Recipe_IronRod_C", 2, building_id=_CONSTRUCTOR),
        ChangeItem(
            ChangeAction.REMOVE,
            "Recipe_IronPlate_C",
            -3,
            "node_Desc_IronPlate_C",
            building_id=_CONSTRUCTOR,
        ),
    )


def test_another_recipe_in_the_same_building_reconfigures_the_machines() -> None:
    """4 smelters on Iron Ingot become 6 on Iron Alloy Ingot (a smelter recipe here): the 4 are
    switched over, 2 more are built."""
    alloy = _node("Desc_IronIngot_C", "Recipe_Alternate_IronAlloy_C", _SMELTER, 6)

    assert plan_changes(_plan(_PLATES, _INGOTS), _plan(_PLATES, alloy)) == (
        ChangeItem(
            ChangeAction.RECONFIGURE,
            "Recipe_Alternate_IronAlloy_C",
            4,
            "node_Desc_IronIngot_C",
            from_recipe_id="Recipe_IngotIron_C",
            building_id=_SMELTER,
        ),
        ChangeItem(
            ChangeAction.EXTEND,
            "Recipe_Alternate_IronAlloy_C",
            2,
            "node_Desc_IronIngot_C",
            building_id=_SMELTER,
        ),
    )


def test_fewer_machines_on_the_new_recipe_switch_some_and_remove_the_rest() -> None:
    alloy = _node("Desc_IronIngot_C", "Recipe_Alternate_IronAlloy_C", _SMELTER, 3)

    changes = plan_changes(_plan(_INGOTS), _plan(alloy))

    assert [(c.action, c.recipe_id, c.additional_machine_count) for c in changes] == [
        (ChangeAction.RECONFIGURE, "Recipe_Alternate_IronAlloy_C", 3),
        (ChangeAction.REMOVE, "Recipe_IngotIron_C", -1),
    ]


def test_another_recipe_in_another_building_takes_the_old_ones_down() -> None:
    foundry = _node("Desc_IronIngot_C", "Recipe_Alternate_IngotIron_C", _FOUNDRY, 2)

    assert plan_changes(_plan(_INGOTS), _plan(foundry)) == (
        ChangeItem(
            ChangeAction.REMOVE,
            "Recipe_IngotIron_C",
            -4,
            "node_Desc_IronIngot_C",
            building_id=_SMELTER,
        ),
        ChangeItem(ChangeAction.ADD, "Recipe_Alternate_IngotIron_C", 2, building_id=_FOUNDRY),
    )


def test_a_plan_extending_the_factory_is_compared_on_what_it_adds() -> None:
    """10 smelters stand; the plan first adds 2, then 5 more than stand."""
    before = _plan(_node("Desc_IronIngot_C", "Recipe_IngotIron_C", _SMELTER, 12, standing=10))
    after = _plan(_node("Desc_IronIngot_C", "Recipe_IngotIron_C", _SMELTER, 15, standing=10))

    assert plan_changes(before, after) == (
        ChangeItem(
            ChangeAction.EXTEND,
            "Recipe_IngotIron_C",
            3,
            "node_Desc_IronIngot_C",
            building_id=_SMELTER,
        ),
    )


def test_what_is_built_of_a_switched_recipe_is_said_to_need_switching_over() -> None:
    """3 of the 4 smelters on Iron Ingot stand; 6 on Iron Alloy Ingot replace them: the 3 are
    switched over in the game, the 4th switch only happens in the plan."""
    alloy = _node("Desc_IronIngot_C", "Recipe_Alternate_IronAlloy_C", _SMELTER, 6)

    changes = plan_changes(_plan(_INGOTS), _plan(alloy), {"Recipe_IngotIron_C": 3})

    assert [(c.action, c.already_built) for c in changes] == [
        (ChangeAction.RECONFIGURE, 3),
        (ChangeAction.EXTEND, None),
    ]


def test_what_is_built_goes_first_to_what_the_new_version_still_builds() -> None:
    """3 of 4 smelters stand, and the revision keeps 2: one of the standing comes down."""
    fewer = _node("Desc_IronIngot_C", "Recipe_IngotIron_C", _SMELTER, 2)

    (change,) = plan_changes(_plan(_INGOTS), _plan(fewer), {"Recipe_IngotIron_C": 3})

    assert (change.action, change.additional_machine_count, change.already_built) == (
        ChangeAction.REMOVE,
        -2,
        1,
    )


def test_switching_some_and_removing_the_rest_takes_the_switch_first() -> None:
    alloy = _node("Desc_IronIngot_C", "Recipe_Alternate_IronAlloy_C", _SMELTER, 3)

    changes = plan_changes(_plan(_INGOTS), _plan(alloy), {"Recipe_IngotIron_C": 4})

    assert [(c.action, c.already_built) for c in changes] == [
        (ChangeAction.RECONFIGURE, 3),
        (ChangeAction.REMOVE, 1),
    ]


def test_what_cannot_be_told_stays_untold() -> None:
    (change,) = plan_changes(_plan(_PLATES, _INGOTS), _plan(_INGOTS), {"Recipe_IronPlate_C": None})

    assert (change.action, change.already_built) == (ChangeAction.REMOVE, None)
