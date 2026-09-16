"""Renders the `chat` field of a `ResponseArtifact` (implementation.md Stage 12) as HTML.

Pure string templating — no LLM, no orchestrator, no other module. `render_message` produces one
chat-bubble; `render_page` wraps a sequence of them in a minimal standalone page so fixture chat
payloads can be checked against a real interface (Stage 12's "done when").
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Literal

Role = Literal["user", "assistant"]


def render_message(text: str | None, *, role: Role = "assistant") -> str:
    """Escapes `text` and wraps it as paragraphs (split on blank lines) inside a role-tagged
    bubble. `None`/empty text (a `ResponseArtifact` with no chat content) still renders a bubble,
    so a missing response is visible rather than silently dropped."""
    paragraphs = "".join(
        f"<p>{html.escape(line.strip())}</p>" for line in (text or "").split("\n\n") if line.strip()
    )
    if not paragraphs:
        paragraphs = "<p><em>(no response)</em></p>"
    return f'<div class="message message-{role}">{paragraphs}</div>'


def render_page(messages: Sequence[tuple[Role, str | None]], *, title: str = "Pioneer") -> str:
    body = "\n".join(render_message(text, role=role) for role, text in messages)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
</head>
<body>
<main class="chat">
{body}
</main>
</body>
</html>
"""


STYLE = """
:root { color-scheme: dark; }
body {
  margin:0; font-family: system-ui, -apple-system, sans-serif;
  background:#0b0f14; color:#e8eaed;
}
.chat {
  max-width: 760px; margin: 0 auto; padding: 32px 20px;
  display:flex; flex-direction:column; gap:14px;
}
.message {
  padding: 12px 16px; border-radius: 16px; max-width: 82%;
  line-height:1.5; font-size: 15px;
}
.message p { margin: 0 0 8px; }
.message p:last-child { margin-bottom: 0; }
.message-assistant { background:#1c2733; align-self:flex-start; border-bottom-left-radius:4px; }
.message-user { background:#2f6fed; align-self:flex-end; border-bottom-right-radius:4px; }
"""
