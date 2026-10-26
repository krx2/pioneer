"""The chat page: one static document whose scripts talk to `/api/ask/stream`, the conversation
endpoints and the feedback endpoint. Its styles and scripts are files of their own (see `assets`).

Answer bubbles arrive already rendered and escaped by `chat_presentation`; the player's text, the
model's text while it streams and conversation titles are only ever inserted with `textContent`.
Graph and map open in frames pointing at their own pages. The open conversation's id is the URL
fragment. An accepted plan shows in a bar over the conversation, refreshed every minute while the
page is in view, which opens into the plan's panel.
"""

from __future__ import annotations

from typing import Any

from pioneer.common import html_document, static_text
from pioneer.web.assets import static_url
from pioneer.web.panels import POPOVER, render_sources

_STYLESHEETS = ("chat.css", "page.css", "panels.css")
_SCRIPTS = ("map-view.js", "page.js", "panels.js")  # one global scope, in this order


def render_chat_page(*, status: str, sources: dict[str, Any] | None = None) -> str:
    """`status` is the one-line summary of what the assistant works from; `sources` says the same
    source by source (see `app.Sources`), for the header's buttons."""
    head = "\n".join(f'<link rel="stylesheet" href="{static_url(n)}">' for n in _STYLESHEETS)
    scripts = "\n".join(f'<script src="{static_url(n)}"></script>' for n in _SCRIPTS)
    body = f"""<header class="bar" id="bar">
  <div class="bar-inner">
    <button type="button" class="menu" id="menu" aria-label="Conversations">&#9776;</button>
    <h1 class="brand">{WORDMARK}</h1>
    <p class="tagline">{TAGLINE}</p>
    {render_sources(status, sources)}
  </div>
</header>
{POPOVER}
<aside class="sidebar" id="sidebar" aria-label="Conversations">
  <button type="button" class="new-chat" id="new-chat">+ New conversation</button>
  <h2 class="sidebar-title">History</h2>
  <nav class="conversations" id="conversations"></nav>
</aside>
<div class="scrim" id="scrim"></div>
<div class="main">
  <div class="plan-bar" id="plan-bar" hidden></div>
  <main class="chat" id="chat"></main>
  <form id="ask" class="composer">
    <div class="composer-box">
      <textarea id="question" rows="2" required
        placeholder="e.g. I want to produce 10/min of Reinforced Iron Plate"></textarea>
      <button type="submit">Ask</button>
    </div>
  </form>
</div>
{scripts}"""
    return html_document("Pioneer", body, head=head)


# What the name stands for, its letters picked out.
TAGLINE = (
    "<b>P</b>roduction <b>I</b>ntelligence &amp; <b>O</b>ptimization for <b>N</b>etworked "
    "<b>E</b>ngineering, <b>E</b>xpansion and <b>R</b>esource-planning for Satisfactory"
)

WORDMARK = static_text(__file__, "wordmark.svg")
