"""The header's data sources: five buttons — Recipes, Tech, Map, Save, Server — and the panel each
opens under it.

The buttons are drawn from `/api/status`'s `sources`, handed inline first and asked for again after
every answer and every change. A panel's content is fetched when it opens; while an answer is on
its way, controls that would change the data are disabled, so an answer is worked out on one
context throughout. Everything the server sends is inserted with `textContent`.
"""

from __future__ import annotations

import html
import json
from typing import Any

SOURCES = (
    ("recipes", "Recipes"),
    ("tech", "Tech"),
    ("map", "Map"),
    ("save", "Save"),
    ("server", "Server"),
)


def render_sources(status: str, sources: dict[str, Any] | None) -> str:
    """The header's buttons and the panel they share; the status they're drawn from goes along as
    JSON, for the script to draw them from without asking again."""
    buttons = "\n".join(
        f'    <button type="button" class="source" data-panel="{name}" aria-haspopup="dialog" '
        f'aria-expanded="false"><span class="source-name">{label}</span>'
        f'<span class="source-state"></span></button>'
        for name, label in SOURCES
    )
    # Inside <script>, "</" would end it: no "<" goes in unescaped.
    initial = json.dumps({"status": status, "sources": sources}, ensure_ascii=False)
    initial = initial.replace("<", "\\u003c")
    return f"""<nav class="sources" id="sources" aria-label="Data sources"
    title="{html.escape(status)}">
{buttons}
    <span class="context-warning" id="context-warning" hidden>Model context too small</span>
  </nav>
  <script type="application/json" id="initial-status">{initial}</script>"""


POPOVER = """<div class="popover" id="popover" role="dialog" aria-labelledby="popover-title" hidden>
  <div class="popover-head">
    <h2 id="popover-title"></h2>
    <button type="button" class="popover-close" id="popover-close"
      aria-label="Close">&times;</button>
  </div>
  <div class="popover-body" id="popover-body"></div>
</div>"""
