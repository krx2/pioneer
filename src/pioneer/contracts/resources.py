"""Static resource node shapes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pioneer.contracts.geometry import Coordinates


class Purity(StrEnum):
    IMPURE = "impure"
    NORMAL = "normal"
    PURE = "pure"


@dataclass(frozen=True)
class ResourceNode:
    node_id: str
    item_id: str
    purity: Purity
    position: Coordinates
    on_well: bool = False
    """A satellite node of a resource well, worked by a Resource Well Extractor once a pressurizer
    stands at the well's core -- not by a miner or an Oil Extractor."""
