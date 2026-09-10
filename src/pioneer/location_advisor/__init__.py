"""Location Advisor module (implementation.md Stage 9).

Recommends where to place new buildings based on unclaimed resource deposits. Depends only on
`pioneer.contracts`; tested against fixture resource-node and placement lists.
"""

from pioneer.location_advisor.advisor import DistanceFn, rank_locations

__all__ = ["DistanceFn", "rank_locations"]
