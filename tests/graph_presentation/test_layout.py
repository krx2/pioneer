"""Tests for the layered layout, against small hand-made graphs."""

from pioneer.graph_presentation.layout import COLUMN_GAP, layered_layout


def test_a_chain_runs_left_to_right_in_a_straight_line() -> None:
    layout = layered_layout(["ore", "ingot", "plate"], [("ore", "ingot"), ("ingot", "plate")])

    assert [layout.positions[n] for n in ("ore", "ingot", "plate")] == [
        (0.0, 0.0),
        (COLUMN_GAP, 0.0),
        (2 * COLUMN_GAP, 0.0),
    ]


def test_lines_wired_across_each_other_are_untangled() -> None:
    layout = layered_layout(
        ["a1", "a2", "b1", "b2", "c"],
        [("a1", "b2"), ("a2", "b1"), ("b1", "c"), ("b2", "c")],
    )
    y = {node: position[1] for node, position in layout.positions.items()}

    assert (y["a1"] < y["a2"]) == (y["b2"] < y["b1"])


def test_an_input_stands_just_left_of_what_it_feeds() -> None:
    layout = layered_layout(
        ["ore", "ingot", "rod", "screw", "bought_in"],
        [("ore", "ingot"), ("ingot", "rod"), ("rod", "screw"), ("bought_in", "screw")],
    )

    assert layout.positions["bought_in"][0] == layout.positions["rod"][0]


def test_a_long_line_gets_a_waypoint_in_every_column_it_crosses() -> None:
    layout = layered_layout(
        ["ore", "ingot", "rod", "screw"],
        [("ore", "ingot"), ("ingot", "rod"), ("rod", "screw"), ("ore", "screw")],
    )

    assert [x for x, _ in layout.waypoints[("ore", "screw")]] == [COLUMN_GAP, 2 * COLUMN_GAP]
    assert ("ore", "ingot") not in layout.waypoints


def test_a_loop_is_laid_out_one_way_and_its_closing_edge_runs_back() -> None:
    layout = layered_layout(
        ["fuel", "packager", "canister"],
        [("fuel", "packager"), ("packager", "canister"), ("canister", "packager")],
    )

    assert layout.back_edges == {("canister", "packager")}
    assert layout.positions["fuel"][0] < layout.positions["packager"][0]
    assert layout.positions["packager"][0] < layout.positions["canister"][0]


def test_nodes_without_edges_and_unknown_ends_are_harmless() -> None:
    layout = layered_layout(["alone", "a"], [("a", "nowhere"), ("a", "a")])

    assert set(layout.positions) == {"alone", "a"}
    assert layout.waypoints == {} and layout.back_edges == frozenset()
