"""Location Advisor module (implementation.md Stage 9).

Recommends where to place new buildings based on unclaimed resource deposits — and where to build a
whole plan, on one site or several (`plan_sites`). Depends only on `pioneer.contracts`; tested
against fixture resource-node and placement lists.
"""

from pioneer.location_advisor.advisor import (
    DistanceFn,
    SiteFeed,
    claimed_node_ids,
    find_factory_sites,
    rank_locations,
    site_feeds,
)
from pioneer.location_advisor.sites import SITE_RADIUS, ExtractorFor, plan_sites, shards_for

__all__ = [
    "SITE_RADIUS",
    "DistanceFn",
    "ExtractorFor",
    "SiteFeed",
    "claimed_node_ids",
    "find_factory_sites",
    "plan_sites",
    "rank_locations",
    "shards_for",
    "site_feeds",
]
