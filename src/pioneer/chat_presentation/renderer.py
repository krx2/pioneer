"""Renders the `chat` field of a `ResponseArtifact` (implementation.md Stage 12) as HTML.

Pure string templating — no LLM, no orchestrator, no other module, and no markdown dependency:
just enough of the syntax (bold, italic, inline/fenced code, lists, headings) that model answers
tend to use. Everything that isn't markup is escaped, so this stays safe to drop into the page
with `innerHTML`. `render_message` produces one chat-bubble; `render_page` wraps a sequence of
them in a minimal standalone page so fixture chat payloads can be checked against a real
interface (Stage 12's "done when").
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from typing import Any, Literal

Role = Literal["user", "assistant"]

_UNORDERED_ITEM = re.compile(r"^[-*+]\s+(.*)$")
_ORDERED_ITEM = re.compile(r"^\d+\.\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_CODE_SPAN = re.compile(r"`([^`]+?)`")
_BOLD = re.compile(r"\*\*([^\n*]+?)\*\*|__([^\n_]+?)__")
_ITALIC = re.compile(r"(?<!\*)\*([^\n*]+?)\*(?!\*)|(?<!\w)_([^\n_]+?)_(?!\w)")
_STASH = re.compile("\x00(\\d+)\x00")


def _inline(text: str) -> str:
    """Escapes `text` and applies inline markdown (code, bold, italic). Code spans are stashed
    before escaping so their contents can't be reinterpreted as bold/italic markers."""
    codes: list[str] = []

    def stash(match: re.Match[str]) -> str:
        codes.append(html.escape(match.group(1)))
        return f"\x00{len(codes) - 1}\x00"

    text = _CODE_SPAN.sub(stash, text)
    text = html.escape(text)
    text = _BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = _ITALIC.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    return _STASH.sub(lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)


def _render_blocks(text: str) -> str:
    """Walks `text` line by line rather than splitting on blank lines first: models routinely emit
    a heading or a numbered item immediately followed by a run of `- ` sub-bullets with no blank
    line in between (unindented, so not proper CommonMark nesting) -- a blank-line-delimited block
    splitter never recognizes those as a heading/list at all and falls back to one flat paragraph.
    Line-by-line, each line is classified on its own; consecutive lines of the same kind are
    grouped (so `- `/`* ` runs still become one `<ul>`), and `- `/`* ` lines that directly follow
    a numbered item are nested inside that item's `<li>` rather than closing the `<ol>`, since
    that's what a numbered item with sub-bullets almost always means in practice."""
    output: list[str] = []
    group_kind: str | None = None
    group_items: list[Any] = []
    fence: list[str] | None = None

    def flush() -> None:
        nonlocal group_kind, group_items
        if group_kind == "p" and group_items:
            output.append(f"<p>{'<br>'.join(group_items)}</p>")
        elif group_kind == "ul" and group_items:
            output.append(f"<ul>{''.join(f'<li>{item}</li>' for item in group_items)}</ul>")
        elif group_kind == "ol" and group_items:
            rendered = []
            for item in group_items:
                items = "".join(f"<li>{sub}</li>" for sub in item["sub"])
                rendered.append(f"<li>{item['text']}{f'<ul>{items}</ul>' if items else ''}</li>")
            output.append(f"<ol>{''.join(rendered)}</ol>")
        group_kind, group_items = None, []

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        if fence is not None:
            if stripped.startswith("```"):
                output.append(f"<pre><code>{html.escape(chr(10).join(fence))}</code></pre>")
                fence = None
            else:
                fence.append(raw_line)
            continue
        if stripped.startswith("```"):
            flush()
            fence = []
            continue
        if not stripped:
            flush()
            continue
        heading = _HEADING.match(stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            output.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue
        unordered = _UNORDERED_ITEM.match(stripped)
        if unordered:
            item_html = _inline(unordered.group(1))
            if group_kind == "ol" and group_items:
                group_items[-1]["sub"].append(item_html)
            else:
                if group_kind != "ul":
                    flush()
                    group_kind = "ul"
                group_items.append(item_html)
            continue
        ordered = _ORDERED_ITEM.match(stripped)
        if ordered:
            if group_kind != "ol":
                flush()
                group_kind = "ol"
            group_items.append({"text": _inline(ordered.group(1)), "sub": []})
            continue
        if group_kind != "p":
            flush()
            group_kind = "p"
        group_items.append(_inline(stripped))

    if fence is not None:  # unterminated fence: render whatever was collected rather than drop it
        output.append(f"<pre><code>{html.escape(chr(10).join(fence))}</code></pre>")
    flush()
    return "".join(output)


def render_message(text: str | None, *, role: Role = "assistant") -> str:
    """Renders `text` as lightweight markdown (paragraphs, bold/italic/code, lists, headings)
    inside a role-tagged bubble. `None`/empty text (a `ResponseArtifact` with no chat content)
    still renders a bubble, so a missing response is visible rather than silently dropped."""
    body = _render_blocks(text or "")
    if not body:
        body = "<p><em>(no response)</em></p>"
    return f'<div class="message message-{role}">{body}</div>'


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
.message > *:first-child { margin-top: 0; }
.message > *:last-child { margin-bottom: 0; }
.message p, .message ul, .message ol, .message pre, .message h1, .message h2,
.message h3, .message h4, .message h5, .message h6 { margin: 0 0 8px; }
.message ul, .message ol { padding-left: 22px; }
.message li { margin: 2px 0; }
.message li > ul { margin: 2px 0 0; }
.message h1, .message h2, .message h3, .message h4, .message h5, .message h6 {
  line-height: 1.3;
}
.message code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em;
  background: rgba(255,255,255,0.12); border-radius: 4px; padding: 1px 5px;
}
.message pre {
  background: rgba(255,255,255,0.08); border-radius: 8px; padding: 10px 12px;
  overflow-x: auto;
}
.message pre code { background: none; padding: 0; }
.message-assistant { background:#1c2733; align-self:flex-start; border-bottom-left-radius:4px; }
.message-user { background:#2f6fed; align-self:flex-end; border-bottom-right-radius:4px; }
"""
