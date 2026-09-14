"""Fixture-driven preview of the target chatbot shell — NOT Stage 16 integration.

implementation.md's methodology (§"Methodology") says nothing renders through a shared app shell
before Stage 16: no live orchestrator, no LLM, no module calling another module. This script
doesn't break that — it lives outside every module's package and only *composes* each finished
presentation module's own public `render_page`/`render_message` function against hand-written
fixture data (the same kind of literal `ResponseArtifact`-shaped data each module's own tests use),
purely so the three already-"done" renderers can be eyeballed together as one conversation. Delete
or ignore this once Stage 16 produces the real thing.

Run: python web/render_preview.py   (writes web/preview.html next to this script)
"""

from __future__ import annotations

import html
from pathlib import Path

from pioneer.chat_presentation.renderer import STYLE as CHAT_STYLE
from pioneer.chat_presentation.renderer import render_message
from pioneer.contracts import (
    Coordinates,
    MaterialFlow,
    PlacementRecord,
    ProductionGraph,
    ProductionNode,
    Purity,
    RankedLocation,
    ResourceNode,
)
from pioneer.graph_presentation.renderer import render_page as render_graph_page
from pioneer.map_presentation.renderer import render_page as render_map_page

# --- Fixture data (copied in as literal ResponseArtifact-shaped data, not imported from another
# module's tests) -------------------------------------------------------------------------------

_GRAPH = ProductionGraph(
    nodes=(
        ProductionNode(
            node_id="node_smelter",
            recipe_id="Recipe_IngotIron_C",
            building_id="Build_SmelterMk1_C",
            machine_count=4,
            is_existing=True,
        ),
        ProductionNode(
            node_id="node_plate",
            recipe_id="Recipe_IronPlate_C",
            building_id="Build_ConstructorMk1_C",
            machine_count=3,
            is_existing=True,
        ),
        ProductionNode(
            node_id="node_rod",
            recipe_id="Recipe_IronRod_C",
            building_id="Build_ConstructorMk1_C",
            machine_count=1,
            is_existing=False,
        ),
        ProductionNode(
            node_id="node_screw",
            recipe_id="Recipe_Screw_C",
            building_id="Build_ConstructorMk1_C",
            machine_count=2,
            is_existing=False,
        ),
        ProductionNode(
            node_id="node_reinforced",
            recipe_id="Recipe_IronPlateReinforced_C",
            building_id="Build_AssemblerMk1_C",
            machine_count=2,
            is_existing=False,
        ),
    ),
    flows=(
        MaterialFlow(
            item_id="Desc_OreIron_C", amount_per_minute=120, target_node_id="node_smelter"
        ),
        MaterialFlow(
            item_id="Desc_IronIngot_C",
            amount_per_minute=45,
            source_node_id="node_smelter",
            target_node_id="node_plate",
        ),
        MaterialFlow(
            item_id="Desc_IronIngot_C",
            amount_per_minute=15,
            source_node_id="node_smelter",
            target_node_id="node_rod",
        ),
        MaterialFlow(
            item_id="Desc_IronPlate_C",
            amount_per_minute=30,
            source_node_id="node_plate",
            target_node_id="node_reinforced",
        ),
        MaterialFlow(
            item_id="Desc_IronRod_C",
            amount_per_minute=15,
            source_node_id="node_rod",
            target_node_id="node_screw",
        ),
        MaterialFlow(
            item_id="Desc_Screw_C",
            amount_per_minute=60,
            source_node_id="node_screw",
            target_node_id="node_reinforced",
        ),
        MaterialFlow(
            item_id="Desc_IronPlateReinforced_C",
            amount_per_minute=5,
            source_node_id="node_reinforced",
        ),
    ),
)

_RESOURCE_NODES = (
    ResourceNode(
        node_id="iron_pure_north",
        item_id="Desc_OreIron_C",
        purity=Purity.PURE,
        position=Coordinates(x=1200, y=-800),
    ),
    ResourceNode(
        node_id="iron_normal_east",
        item_id="Desc_OreIron_C",
        purity=Purity.NORMAL,
        position=Coordinates(x=2600, y=400),
    ),
)
_PLACEMENTS = (
    PlacementRecord(
        building_id="Build_SmelterMk1_C",
        position=Coordinates(x=900, y=-200),
        recipe_id="Recipe_IngotIron_C",
    ),
    PlacementRecord(
        building_id="Build_ConstructorMk1_C",
        position=Coordinates(x=1000, y=-150),
        recipe_id="Recipe_IronPlate_C",
    ),
)
_RANKED_LOCATIONS = (
    RankedLocation(
        resource_node_id="iron_pure_north",
        position=Coordinates(x=1200, y=-800),
        purity=Purity.PURE,
        distance_to_reference=650.0,
        score=0.93,
    ),
    RankedLocation(
        resource_node_id="iron_normal_east",
        position=Coordinates(x=2600, y=400),
        purity=Purity.NORMAL,
        distance_to_reference=1700.0,
        score=0.58,
    ),
)

_CONVERSATION_TEXT = [
    ("user", "Chcę produkować 5/min Reinforced Iron Plate. Mam już częściowo zbudowaną hutę."),
    (
        "assistant",
        "Masz już piece i konstruktory na płytki — brakuje tylko gałęzi pod śruby i "
        "assemblera. Rozszerzam istniejącą hutę o linię Iron Rod -> Screw i dopinam "
        "nowy Reinforced Iron Plate Assembler. Szare węzły poniżej to to, co już stoi; "
        "niebieskie to nowe elementy do postawienia.",
    ),
    (
        "assistant",
        "Nowa linia potrzebuje dodatkowej rudy żelaza. Najbliższe wolne złoże to czyste "
        "złoże na północy (score 0.93) — bliżej niż normalne złoże na wschodzie. "
        "Rekomendowana lokalizacja oznaczona jest na mapie jako #1.",
    ),
    (
        "assistant",
        "Podsumowanie: rozbuduj hutę o 1x Constructor (Iron Rod) i 2x Constructor "
        "(Screw), dodaj 2x Assembler (Reinforced Iron Plate), i postaw nowy Miner przy "
        "czystym złożu na północy.",
    ),
]


def _iframe(srcdoc_html: str, *, height: str = "460px") -> str:
    return (
        f'<iframe class="panel" style="height:{height}" '
        f'srcdoc="{html.escape(srcdoc_html, quote=True)}"></iframe>'
    )


def render_preview() -> str:
    graph_panel = _iframe(render_graph_page(_GRAPH, title="Proposed production graph"))
    map_panel = _iframe(
        render_map_page(
            _RESOURCE_NODES, _PLACEMENTS, _RANKED_LOCATIONS, title="Recommended location"
        )
    )

    turns = [
        render_message(_CONVERSATION_TEXT[0][1], role="user"),
        render_message(_CONVERSATION_TEXT[1][1], role="assistant"),
        graph_panel,
        render_message(_CONVERSATION_TEXT[2][1], role="assistant"),
        map_panel,
        render_message(_CONVERSATION_TEXT[3][1], role="assistant"),
    ]
    body = "\n".join(turns)

    return f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pioneer — preview</title>
<style>
{CHAT_STYLE}
.panel {{
  width: 100%; align-self: stretch; max-width: none;
  border: 1px solid #2a3542; border-radius: 12px; background:#11161c;
}}
</style>
</head>
<body>
<main class="chat">
{body}
</main>
</body>
</html>
"""


if __name__ == "__main__":
    output_path = Path(__file__).parent / "preview.html"
    output_path.write_text(render_preview(), encoding="utf-8")
    print(f"wrote {output_path}")
