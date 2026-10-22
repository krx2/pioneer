"""Where a plan is built: the least nodes that are enough, worked overclocked, on one site when they
lie close together, else on several each making its own parts."""

import pytest

from pioneer.contracts import (
    Coordinates,
    Extractor,
    MaterialFlow,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
    Purity,
    ResourceNode,
)
from pioneer.location_advisor import plan_sites, shards_for

_MINER = Extractor("Build_MinerMk1_C", 60.0, 2.5, 1e9)
_BASE = Coordinates(x=0, y=0)


def _node(node_id: str, item: str, purity: Purity, x: float, y: float = 0.0) -> ResourceNode:
    return ResourceNode(node_id, item, purity, Coordinates(x=x, y=y))


def _miner(node: ResourceNode) -> Extractor:
    return _MINER


def _plan(needs, nodes, graph=None, *, extractor_for=_miner, placements=()):
    return plan_sites(needs, nodes, placements, extractor_for, _BASE, graph)


def test_the_least_node_that_is_enough_is_chosen_and_overclocked() -> None:
    """100 a minute: an impure node gives at most 75, a normal one 150 at 250% — so the normal
    one, at 167%, not the pure one underclocked."""
    nodes = (
        _node("impure", "Desc_OreIron_C", Purity.IMPURE, 1000),
        _node("normal", "Desc_OreIron_C", Purity.NORMAL, 5000),
        _node("pure", "Desc_OreIron_C", Purity.PURE, 2000),
    )

    plan = _plan({"Desc_OreIron_C": 100}, nodes)

    (deposit,) = plan.deposits
    assert deposit.resource_node_id == "normal"
    assert deposit.clock_speed == pytest.approx(100 / 60)
    assert (deposit.power_shards, deposit.rate_per_minute) == (2, pytest.approx(100))
    assert len(plan.sites) == 1 and plan.shortfall == ()


def test_nodes_chosen_together_run_at_the_same_share_of_what_each_can_give() -> None:
    """200 a minute: impure and normal give 225 between them, sparing less than the pure one's
    300 — both at 200/225 of their most."""
    nodes = (
        _node("impure", "Desc_OreIron_C", Purity.IMPURE, 1000),
        _node("normal", "Desc_OreIron_C", Purity.NORMAL, 2000),
        _node("pure", "Desc_OreIron_C", Purity.PURE, 3000),
    )

    plan = _plan({"Desc_OreIron_C": 200}, nodes)

    assert sorted(d.resource_node_id for d in plan.deposits) == ["impure", "normal"]
    assert sum(d.rate_per_minute for d in plan.deposits) == pytest.approx(200)
    assert {round(d.clock_speed, 4) for d in plan.deposits} == {round(2.5 * 200 / 225, 4)}


def test_one_node_wins_over_two_that_give_as_much() -> None:
    nodes = (
        _node("impure_a", "Desc_OreIron_C", Purity.IMPURE, 1000),
        _node("impure_b", "Desc_OreIron_C", Purity.IMPURE, 1500),
        _node("normal", "Desc_OreIron_C", Purity.NORMAL, 9000),
    )

    plan = _plan({"Desc_OreIron_C": 140}, nodes)

    assert [d.resource_node_id for d in plan.deposits] == ["normal"]


def test_a_node_is_pushed_no_further_than_its_belt_carries() -> None:
    belt_60 = Extractor("Build_MinerMk1_C", 60.0, 2.5, 60.0)
    nodes = (_node("pure", "Desc_OreIron_C", Purity.PURE, 1000),)

    plan = _plan({"Desc_OreIron_C": 100}, nodes, extractor_for=lambda node: belt_60)

    (deposit,) = plan.deposits
    assert (deposit.rate_per_minute, deposit.clock_speed) == (60, 0.5)
    assert plan.shortfall == (("Desc_OreIron_C", pytest.approx(40)),)


def test_power_shards_are_one_per_fifty_points_past_full_speed() -> None:
    assert [shards_for(c) for c in (0.5, 1.0, 1.01, 1.5, 1.51, 2.0, 2.5)] == [0, 0, 1, 1, 2, 2, 3]


def test_claimed_nodes_are_left_alone() -> None:
    nodes = (
        _node("taken", "Desc_OreIron_C", Purity.NORMAL, 1000),
        _node("free", "Desc_OreIron_C", Purity.NORMAL, 3000),
    )
    miner = PlacementRecord("Build_MinerMk1_C", nodes[0].position, resource_node_id="taken")

    plan = _plan({"Desc_OreIron_C": 60}, nodes, placements=(miner,))

    assert [d.resource_node_id for d in plan.deposits] == ["free"]


def test_a_node_only_a_locked_extractor_works_is_used_only_when_the_rest_fall_short() -> None:
    nodes = (
        _node("oil", "Desc_LiquidOil_C", Purity.NORMAL, 1000),
        ResourceNode(
            "well", "Desc_LiquidOil_C", Purity.PURE, Coordinates(x=1500, y=0), on_well=True
        ),
    )
    pump = Extractor("Build_OilPump_C", 120.0, 2.5, 600.0)
    well = Extractor("Build_FrackingExtractor_C", 60.0, 1.0, 600.0, unlocked=False)

    def extractor_for(node):
        return well if node.on_well else pump

    enough = _plan({"Desc_LiquidOil_C": 250}, nodes, extractor_for=extractor_for)
    short = _plan({"Desc_LiquidOil_C": 350}, nodes, extractor_for=extractor_for)

    assert [d.resource_node_id for d in enough.deposits] == ["oil"]
    assert sorted(d.resource_node_id for d in short.deposits) == ["oil", "well"]


def test_what_no_free_node_is_left_for_is_a_shortfall() -> None:
    nodes = (_node("iron", "Desc_OreIron_C", Purity.NORMAL, 1000),)

    plan = _plan({"Desc_OreIron_C": 60, "Desc_Coal_C": 30}, nodes)

    assert plan.shortfall == (("Desc_Coal_C", 30),)
    assert [d.item_id for d in plan.deposits] == ["Desc_OreIron_C"]


# --- sites -----------------------------------------------------------------------------------


def _graph(*stages: tuple[str, str, float, dict[str, float]]) -> ProductionGraph:
    """Stages as (node id, recipe id, machines, {input: per minute}): an input that's another
    stage's id comes from it, any other from outside (mined)."""
    ids = {stage[0] for stage in stages}
    nodes = tuple(ProductionNode(nid, recipe, f"Build_{recipe}", m) for nid, recipe, m, _ in stages)
    flows = tuple(
        MaterialFlow(
            f"Desc_{source}_C" if source in ids else source,
            rate,
            source_node_id=source if source in ids else None,
            target_node_id=nid,
        )
        for nid, _, _, inputs in stages
        for source, rate in inputs.items()
    )
    return ProductionGraph(nodes=nodes, flows=flows)


_STEEL = _graph(
    ("IronIngot", "Recipe_IngotIron_C", 2, {"Desc_OreIron_C": 60}),
    ("CopperIngot", "Recipe_IngotCopper_C", 2, {"Desc_OreCopper_C": 60}),
    ("Alloy", "Recipe_Alloy_C", 3, {"IronIngot": 60, "CopperIngot": 60}),
)


def test_deposits_close_together_make_one_site_building_everything() -> None:
    nodes = (
        _node("iron", "Desc_OreIron_C", Purity.NORMAL, 10_000),
        _node("copper", "Desc_OreCopper_C", Purity.NORMAL, 30_000),
    )

    plan = _plan({"Desc_OreIron_C": 60, "Desc_OreCopper_C": 60}, nodes, _STEEL)

    (site,) = plan.sites
    assert site.position.x == pytest.approx(20_000)
    assert {d.site_id for d in plan.deposits} == {site.site_id}
    assert [(s.recipe_id, s.machine_count, s.share) for s in site.stages] == [
        ("Recipe_IngotIron_C", 2, 1.0),
        ("Recipe_IngotCopper_C", 2, 1.0),
        ("Recipe_Alloy_C", 3, 1.0),
    ]
    assert plan.transfers == ()


def test_deposits_far_apart_make_sites_each_building_its_own_parts() -> None:
    """Iron and copper 2 km apart: each is smelted where it's mined, and the alloy is made where
    more of what it takes comes from — tied here, so at the site mining more."""
    nodes = (
        _node("iron_a", "Desc_OreIron_C", Purity.NORMAL, 0),
        _node("iron_b", "Desc_OreIron_C", Purity.NORMAL, 5000),
        _node("copper", "Desc_OreCopper_C", Purity.NORMAL, 200_000),
    )
    needs = {"Desc_OreIron_C": 200, "Desc_OreCopper_C": 60}
    graph = _graph(
        ("IronIngot", "Recipe_IngotIron_C", 7, {"Desc_OreIron_C": 200}),
        ("CopperIngot", "Recipe_IngotCopper_C", 2, {"Desc_OreCopper_C": 60}),
        ("Alloy", "Recipe_Alloy_C", 3, {"IronIngot": 200, "CopperIngot": 60}),
    )

    plan = _plan(needs, nodes, graph)

    iron_site, copper_site = plan.sites
    assert {d.item_id for d in plan.deposits if d.site_id == iron_site.site_id} == {
        "Desc_OreIron_C"
    }
    assert [s.recipe_id for s in iron_site.stages] == ["Recipe_IngotIron_C", "Recipe_Alloy_C"]
    assert [s.recipe_id for s in copper_site.stages] == ["Recipe_IngotCopper_C"]
    (sent,) = plan.transfers
    assert (sent.item_id, sent.from_site_id, sent.to_site_id) == (
        "Desc_CopperIngot_C",
        copper_site.site_id,
        iron_site.site_id,
    )
    assert sent.rate_per_minute == pytest.approx(60)


def test_a_need_no_one_place_covers_is_mined_at_several_and_smelted_where_it_is_mined() -> None:
    """300 iron: one normal node gives 150 at most, the next is 2 km off. Each site smelts its own
    ore; the plates are made at one of them, from both sites' ingots — the ore stays put."""
    nodes = (
        _node("west", "Desc_OreIron_C", Purity.NORMAL, 0),
        _node("east", "Desc_OreIron_C", Purity.NORMAL, 200_000),
    )
    graph = _graph(
        ("IronIngot", "Recipe_IngotIron_C", 10, {"Desc_OreIron_C": 300}),
        ("IronPlate", "Recipe_IronPlate_C", 10, {"IronIngot": 300}),
    )

    plan = _plan({"Desc_OreIron_C": 300}, nodes, graph)

    assert len(plan.sites) == 2
    smelting = {
        site.site_id: next(s for s in site.stages if s.recipe_id == "Recipe_IngotIron_C")
        for site in plan.sites
    }
    assert [s.share for s in smelting.values()] == [pytest.approx(0.5)] * 2
    assert [s.machine_count for s in smelting.values()] == [5, 5]
    plating = [
        site for site in plan.sites if any(s.recipe_id == "Recipe_IronPlate_C" for s in site.stages)
    ]
    assert len(plating) == 1
    (sent,) = plan.transfers
    assert (sent.item_id, sent.to_site_id) == ("Desc_IronIngot_C", plating[0].site_id)
    assert sent.rate_per_minute == pytest.approx(150)


# --- revisions -------------------------------------------------------------------------------


def test_nodes_other_plans_work_are_never_offered() -> None:
    nodes = (
        _node("theirs", "Desc_OreIron_C", Purity.NORMAL, 1000),
        _node("free", "Desc_OreIron_C", Purity.NORMAL, 9000),
    )

    plan = plan_sites({"Desc_OreIron_C": 60}, nodes, (), _miner, _BASE, reserved={"theirs"})

    assert [d.resource_node_id for d in plan.deposits] == ["free"]


def test_a_revision_keeps_its_deposits_and_sites_where_they_were() -> None:
    """The plan was fed from the impure node; a normal one has come free next to the base since.
    Asked for a little more, the plan stays on its node, its site where it was — overclocked more,
    with an extractor standing on it already that'd claim it from anyone else."""
    nodes = (
        _node("kept", "Desc_OreIron_C", Purity.IMPURE, 50_000),
        _node("near", "Desc_OreIron_C", Purity.NORMAL, 1000),
    )
    before = plan_sites({"Desc_OreIron_C": 50}, nodes[:1], (), _miner, _BASE)
    miner = PlacementRecord("Build_MinerMk1_C", nodes[0].position, resource_node_id="kept")

    after = plan_sites({"Desc_OreIron_C": 70}, nodes, (miner,), _miner, _BASE, kept=before)

    assert [(d.resource_node_id, d.site_id) for d in after.deposits] == [("kept", "new_site_1")]
    assert after.deposits[0].rate_per_minute == pytest.approx(70)
    assert after.sites == before.sites


def test_a_revision_needing_less_keeps_the_nodes_built_on_first() -> None:
    nodes = (
        _node("built", "Desc_OreIron_C", Purity.NORMAL, 1000),
        _node("bare", "Desc_OreIron_C", Purity.IMPURE, 2000),
    )
    before = plan_sites({"Desc_OreIron_C": 200}, nodes, (), _miner, _BASE)
    miner = PlacementRecord("Build_MinerMk1_C", nodes[0].position, resource_node_id="built")

    after = plan_sites({"Desc_OreIron_C": 60}, nodes, (miner,), _miner, _BASE, kept=before)

    assert sorted(d.resource_node_id for d in before.deposits) == ["bare", "built"]
    assert [d.resource_node_id for d in after.deposits] == ["built"]


def test_a_revision_needing_more_adds_deposits_near_what_it_keeps() -> None:
    """One normal node gives 150 at most: for 200, the next one 100 m off joins its site, and for
    a need past both, one 2 km off makes a site of its own."""
    nodes = (
        _node("kept", "Desc_OreIron_C", Purity.NORMAL, 0),
        _node("close", "Desc_OreIron_C", Purity.NORMAL, 10_000),
        _node("far", "Desc_OreIron_C", Purity.NORMAL, 200_000),
    )
    before = plan_sites({"Desc_OreIron_C": 100}, nodes[:1], (), _miner, _BASE)

    more = plan_sites({"Desc_OreIron_C": 200}, nodes, (), _miner, _BASE, kept=before)
    most = plan_sites({"Desc_OreIron_C": 400}, nodes, (), _miner, _BASE, kept=before)

    assert {(d.resource_node_id, d.site_id) for d in more.deposits} == {
        ("kept", "new_site_1"),
        ("close", "new_site_1"),
    }
    assert more.sites[0].position == before.sites[0].position
    assert {(d.resource_node_id, d.site_id) for d in most.deposits} == {
        ("kept", "new_site_1"),
        ("close", "new_site_1"),
        ("far", "new_site_2"),
    }
    assert most.shortfall == ()
    assert sum(d.rate_per_minute for d in most.deposits) == pytest.approx(400)


def test_a_revision_keeps_each_stage_at_the_site_it_was_at() -> None:
    """The alloy was made at the copper site. Revised with more iron, the iron site gives more of
    what it takes in — but the alloy stays where it's built, as the copper site still gives some."""
    nodes = (
        _node("iron_a", "Desc_OreIron_C", Purity.NORMAL, 0),
        _node("iron_b", "Desc_OreIron_C", Purity.NORMAL, 5000),
        _node("copper", "Desc_OreCopper_C", Purity.NORMAL, 200_000),
    )

    def graph(iron: float, copper: float) -> ProductionGraph:
        return _graph(
            ("IronIngot", "Recipe_IngotIron_C", 4, {"Desc_OreIron_C": iron}),
            ("CopperIngot", "Recipe_IngotCopper_C", 4, {"Desc_OreCopper_C": copper}),
            ("Alloy", "Recipe_Alloy_C", 3, {"IronIngot": iron, "CopperIngot": copper}),
        )

    needs = {"Desc_OreIron_C": 60, "Desc_OreCopper_C": 120}
    before = plan_sites(needs, nodes, (), _miner, _BASE, graph(60, 120))
    copper_site = next(d.site_id for d in before.deposits if d.item_id == "Desc_OreCopper_C")
    more = {"Desc_OreIron_C": 280, "Desc_OreCopper_C": 120}

    after = plan_sites(more, nodes, (), _miner, _BASE, graph(280, 120), kept=before)
    fresh = plan_sites(more, nodes, (), _miner, _BASE, graph(280, 120))

    def alloy_site(plan):
        return next(
            s.site_id for s in plan.sites for st in s.stages if st.recipe_id == "Recipe_Alloy_C"
        )

    assert alloy_site(before) == copper_site
    assert alloy_site(after) == copper_site
    assert alloy_site(fresh) != next(
        d.site_id for d in fresh.deposits if d.item_id == "Desc_OreCopper_C"
    )
