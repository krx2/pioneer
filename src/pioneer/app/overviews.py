"""What the header's Map and Tech panels show: the player's factories and the resource nodes, and
the technologies there are to unlock, with what the save has unlocked of them."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping

from pioneer.app.sources import MapOverview, SiteOverview, TechOverview, TechSection
from pioneer.common import raw_resource_ids
from pioneer.contracts import FactorySite, Technology
from pioneer.knowledge_base import KnowledgeBase, milestone_progress, research_trees
from pioneer.location_advisor import claimed_node_ids, site_feeds
from pioneer.orchestrator import OrchestratorContext, site_balance

_SPARE_EPSILON = 0.01
"""Per minute: less than this to spare is rounding, not a product."""


def map_overview(context: OrchestratorContext) -> MapOverview:
    """The player's factories -- what each takes in, puts out and is fed by -- and every resource
    node, taken or free."""
    feeds = site_feeds(context.factory_sites, context.existing_placements)
    raw = raw_resource_ids(context.items)
    sites = []
    for site in context.factory_sites:
        balance = site_balance(site, context)
        sites.append(
            SiteOverview(
                site=site,
                main_product=_main_product(site, balance, context, raw),
                balance=balance,
                feeds=feeds.get(site.site_id, ()),
            )
        )
    return MapOverview(
        sites=tuple(sites),
        nodes=context.resource_nodes,
        occupied=claimed_node_ids(context.resource_nodes, context.existing_placements),
    )


def tech_overview(kb: KnowledgeBase | None, unlocked: frozenset[str] | None) -> TechOverview:
    """The HUB's milestones tier by tier, the MAM's trees and the hard drives' alternates, with
    which of them are `unlocked` (`None` when no save says)."""
    technologies = kb.technologies if kb else ()
    hub = [t for t in technologies if t.kind in ("tutorial", "milestone")]
    sections = [
        TechSection("HUB", f"Tier {tier}", tuple(sorted(at_tier, key=_by_steps)))
        for tier in sorted({t.tier for t in hub})
        if (at_tier := [t for t in hub if t.tier == tier])
    ]
    sections += [TechSection("MAM", name, tree) for name, tree in research_trees(technologies)]
    drives = sorted(
        (t for t in technologies if t.kind in ("alternate", "harddrive")),
        key=lambda technology: (technology.tier, technology.name),
    )
    if drives:
        sections.append(TechSection("Hard drives", "Alternate recipes", tuple(drives)))
    unlocks: dict[str, list[str]] = {}
    for recipe in kb.recipes if kb else ():
        for technology_id in recipe.unlockable_by:
            unlocks.setdefault(technology_id, []).append(recipe.recipe_id)
    return TechOverview(
        unlocked=unlocked,
        tiers=milestone_progress(technologies, unlocked or ()),
        sections=tuple(sections),
        unlocks={technology: tuple(recipes) for technology, recipes in unlocks.items()},
    )


def _main_product(
    site: FactorySite,
    balance: Mapping[str, float],
    context: OrchestratorContext,
    raw: Collection[str],
) -> str | None:
    """What `site` is there for: of what it makes and has to spare, the end products -- ones none
    of its own recipes takes in -- before the rest, and of those the item the most of its
    machines make. With nothing to spare, what the most of its machines make."""
    recipes = {recipe.recipe_id: recipe for recipe in context.recipes}
    makers: dict[str, int] = {}
    consumed: set[str] = set()
    for placement in site.placements:
        recipe = recipes.get(placement.recipe_id or "")
        if recipe is None:
            continue
        for output in recipe.outputs:
            makers[output.item_id] = makers.get(output.item_id, 0) + 1
        consumed.update(ingredient.item_id for ingredient in recipe.inputs)
    spare = [
        item
        for item, rate in balance.items()
        if rate > _SPARE_EPSILON and item in makers and item not in raw
    ]
    candidates = [item for item in spare if item not in consumed] or spare or list(makers)
    return max(candidates, key=lambda item: makers[item], default=None)


def _by_steps(technology: Technology) -> tuple[tuple[int, ...], str]:
    """Milestones in the HUB's order: `Schematic_3-2_C` after `_3-1_`."""
    return tuple(int(n) for n in re.findall(r"\d+", technology.technology_id)), technology.name
