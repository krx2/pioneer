"""Tests for build_markers/render_page, against resource-node and placement fixtures copied in
the same style as Stage 3/9 example outputs, per implementation.md Stage 14."""

import re

from pioneer.contracts import (
    Coordinates,
    FactorySite,
    PlacementRecord,
    Purity,
    RankedLocation,
    ResourceNode,
)
from pioneer.map_presentation.renderer import build_markers, compute_view_box, render_page

_NODES = (
    ResourceNode(
        node_id="iron_pure",
        item_id="Desc_OreIron_C",
        purity=Purity.PURE,
        position=Coordinates(x=1000, y=2000),
    ),
    ResourceNode(
        node_id="iron_normal",
        item_id="Desc_OreIron_C",
        purity=Purity.NORMAL,
        position=Coordinates(x=3000, y=2500),
    ),
)
_PLACEMENTS = (
    PlacementRecord(
        building_id="Build_SmelterMk1_C",
        position=Coordinates(x=1500, y=2200),
        recipe_id="Recipe_IngotIron_C",
    ),
)
_RANKED = (
    RankedLocation(
        resource_node_id="iron_normal",
        position=Coordinates(x=3000, y=2500),
        purity=Purity.NORMAL,
        distance_to_reference=800.0,
        score=0.6,
    ),
    RankedLocation(
        resource_node_id="iron_pure",
        position=Coordinates(x=1000, y=2000),
        purity=Purity.PURE,
        distance_to_reference=500.0,
        score=0.9,
    ),
)


def test_marker_counts_match_input() -> None:
    markers = build_markers(_NODES, _PLACEMENTS, _RANKED)

    assert sum(1 for m in markers if m.kind == "resource") == 2
    assert sum(1 for m in markers if m.kind == "existing_building") == 1
    assert sum(1 for m in markers if m.kind == "recommended") == 2


def test_recommended_markers_are_ranked_by_score_descending() -> None:
    markers = build_markers((), (), _RANKED)

    ranks_by_position = {m.rank: (m.x, m.y) for m in markers}
    assert ranks_by_position[1] == (1000, 2000)  # higher score (0.9) ranked first
    assert ranks_by_position[2] == (3000, 2500)


def test_purity_maps_to_a_distinct_color_per_level() -> None:
    markers = build_markers(_NODES, (), ())

    colors = {m.color for m in markers}
    assert len(colors) == 2  # PURE and NORMAL nodes get different colors


def test_view_box_covers_every_marker_with_padding() -> None:
    markers = build_markers(_NODES, _PLACEMENTS, _RANKED)

    min_x, min_y, w, h = compute_view_box(markers, padding=100.0)

    for m in markers:
        assert min_x <= m.x <= min_x + w
        assert min_y <= m.y <= min_y + h


def test_view_box_falls_back_when_nothing_to_show() -> None:
    assert compute_view_box(()) == (0.0, 0.0, 1000.0, 1000.0)


def test_render_page_embeds_every_marker_label() -> None:
    page = render_page(_NODES, _PLACEMENTS, _RANKED)

    assert "Recipe_IngotIron_C" in page
    assert "pure, score 0.90" in page
    assert "<!doctype html>" in page.lower()


def test_render_page_handles_no_data() -> None:
    page = render_page()

    assert "<svg" in page


def test_labels_use_the_given_names() -> None:
    names = {"Desc_OreIron_C": "Iron Ore", "Recipe_IngotIron_C": "Iron Ingot"}

    page = render_page(_NODES, _PLACEMENTS, (), names=names)

    assert "Iron Ore (pure)" in page
    assert ">Iron Ingot<" in page
    assert "Desc_OreIron_C" not in page


def test_a_crowded_map_labels_only_the_recommendations() -> None:
    page = render_page(_NODES, _PLACEMENTS, _RANKED, label_limit=2)

    labels = re.findall(r'class="label"[^>]*>([^<]*)<', page)
    assert len(labels) == 2
    assert all(label.startswith("#") for label in labels)
    assert "<title>Recipe_IngotIron_C</title>" in page  # still there on hover


def test_factory_sites_become_labelled_pins() -> None:
    site = FactorySite(
        site_id="site_2",
        position=Coordinates(x=1500, y=2200),
        placements=_PLACEMENTS * 2,
    )

    (marker,) = build_markers(factory_sites=(site,), names={"Recipe_IngotIron_C": "Iron Ingot"})
    page = render_page(factory_sites=(site,), label_limit=0)

    assert (marker.kind, marker.x, marker.y) == ("factory", 1500, 2200)
    assert marker.label == "site_2: Iron Ingot"
    assert "site_2: Recipe_IngotIron_C" in page  # labelled even past the label limit
    assert "factory to extend" in page
