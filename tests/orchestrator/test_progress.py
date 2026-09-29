"""Tests for the words the page shows while a tool runs."""

from pioneer.orchestrator.progress import describe_tool_call

_NAMES = {"Desc_IronPlate_C": "Iron Plate"}


def test_a_tool_call_is_described_with_what_it_works_on() -> None:
    assert describe_tool_call("find_item", {"query": "plates"}, _NAMES) == "Looking up plates"
    assert (
        describe_tool_call(
            "expand_existing_factory",
            {"target_item_id": "Desc_IronPlate_C", "target_rate_per_minute": 7.5},
            _NAMES,
        )
        == "Planning how to extend your factory to 7.5/min of Iron Plate"
    )
    assert describe_tool_call("plan_power", {"target_mw": 300.0}, _NAMES) == (
        "Planning 300 MW of power"
    )


def test_missing_or_odd_arguments_still_give_a_line() -> None:
    assert describe_tool_call("plan_production", {}, _NAMES) == "Planning"
    assert describe_tool_call("plan_power", {"target_mw": "lots"}, _NAMES) == "Planning power"
    assert describe_tool_call(
        "plan_production", {"target_item_id": "Iron Plate", "target_rate_per_minute": "x"}, _NAMES
    ) == ("Planning Iron Plate")
    assert describe_tool_call("made_up_tool", {}, _NAMES) == "Running made_up_tool"
