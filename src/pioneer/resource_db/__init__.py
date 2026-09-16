"""Resource / map database module (implementation.md Stage 3).

Static data set of resource node locations and purity. Depends only on `pioneer.contracts`;
tested against its own `fixtures/`. The real data ships at `docs/resource_nodes.json` — see
`loader.py` for where it came from.
"""

from pioneer.resource_db.loader import load_from_dict, load_from_file
from pioneer.resource_db.queries import ResourceDatabase, nearest_nodes, unclaimed_nodes

__all__ = [
    "ResourceDatabase",
    "load_from_dict",
    "load_from_file",
    "nearest_nodes",
    "unclaimed_nodes",
]
