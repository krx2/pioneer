"""Shared data contracts: plain data definitions, a few derived values on them
(`Coordinates.distance_to`, `Recipe.output_rate`), and the one exception an injected transport
raises — no I/O. Modules depend on these shapes; this package imports no sibling.
"""

from pioneer.contracts.anomalies import AnomalyKind, AnomalyRecord, AnomalySeverity
from pioneer.contracts.game_state import GameState
from pioneer.contracts.geometry import Coordinates
from pioneer.contracts.intent import Intent, IntentKind
from pioneer.contracts.locations import (
    BuildSite,
    Extractor,
    FactorySite,
    NodeAssignment,
    RankedLocation,
    SitePlan,
    SiteStage,
    SiteTransfer,
    SupplyReservation,
)
from pioneer.contracts.placement import Carrier, PlacementRecord, TransportLink
from pioneer.contracts.plan import Plan, PlanTarget, StageProgress
from pioneer.contracts.planning import ChangeAction, ChangeItem, ChangeSet
from pioneer.contracts.production_graph import MaterialFlow, ProductionGraph, ProductionNode
from pioneer.contracts.recipes import (
    Building,
    ClassDescription,
    GeneratorFuel,
    Item,
    ItemAmount,
    ItemCount,
    Recipe,
    Technology,
    TransportTier,
)
from pioneer.contracts.resources import Purity, ResourceNode
from pioneer.contracts.responses import Feedback, ResponseArtifact
from pioneer.contracts.transport import TransportError

__all__ = [
    "AnomalyKind",
    "AnomalyRecord",
    "AnomalySeverity",
    "Building",
    "BuildSite",
    "Carrier",
    "ChangeAction",
    "ChangeItem",
    "ChangeSet",
    "ClassDescription",
    "Coordinates",
    "Extractor",
    "FactorySite",
    "Feedback",
    "GameState",
    "GeneratorFuel",
    "Intent",
    "IntentKind",
    "Item",
    "ItemAmount",
    "ItemCount",
    "MaterialFlow",
    "NodeAssignment",
    "PlacementRecord",
    "Plan",
    "PlanTarget",
    "ProductionGraph",
    "ProductionNode",
    "Purity",
    "RankedLocation",
    "Recipe",
    "ResourceNode",
    "ResponseArtifact",
    "SitePlan",
    "SiteStage",
    "SiteTransfer",
    "StageProgress",
    "SupplyReservation",
    "Technology",
    "TransportError",
    "TransportLink",
    "TransportTier",
]
