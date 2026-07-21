"""Static resource node shapes.

Produced by the Resource/Map Database module (Stage 3); consumed by the Location Advisor
(Stage 9) and Map presentation (Stage 14).
"""

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
