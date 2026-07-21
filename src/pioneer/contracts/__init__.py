"""Shared data contracts (implementation.md Stage 1).

Plain data definitions only — no logic, no I/O. Every other module in this package depends on
these shapes and nothing else; this package must never import from any sibling module.

Everything is re-exported here so other modules can do `from pioneer.contracts import Recipe`
instead of reaching into individual files.
"""

from pioneer.contracts.anomalies import AnomalyKind, AnomalyRecord, AnomalySeverity
from pioneer.contracts.game_state import GameState
from pioneer.contracts.geometry import Coordinates
from pioneer.contracts.intent import Intent, IntentKind
from pioneer.contracts.locations import RankedLocation
from pioneer.contracts.placement import PlacementRecord
from pioneer.contracts.planning import ChangeAction, ChangeItem, ChangeSet
from pioneer.contracts.production_graph import MaterialFlow, ProductionGraph, ProductionNode
from pioneer.contracts.recipes import Building, ItemAmount, Recipe, Technology
from pioneer.contracts.resources import Purity, ResourceNode
from pioneer.contracts.responses import Feedback, ResponseArtifact

__all__ = [
    "AnomalyKind",
    "AnomalyRecord",
    "AnomalySeverity",
    "Building",
    "ChangeAction",
    "ChangeItem",
    "ChangeSet",
    "Coordinates",
    "Feedback",
    "GameState",
    "Intent",
    "IntentKind",
    "ItemAmount",
    "MaterialFlow",
    "PlacementRecord",
    "ProductionGraph",
    "ProductionNode",
    "Purity",
    "RankedLocation",
    "Recipe",
    "ResourceNode",
    "ResponseArtifact",
    "Technology",
]
