"""Renders the `chat` field of a `ResponseArtifact` as HTML.

No markdown dependency: just the syntax model answers use (bold, italic, inline and fenced code,
lists, headings, tables). Everything that isn't markup is escaped, so the result is safe to insert
with `innerHTML`. Given icons by in-game name, each named item or building gets its icon in front.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from pioneer.common import html_document, static_path

Role = Literal["user", "assistant"]

_UNORDERED_ITEM = re.compile(r"^[-*+]\s+(.*)$")
_ORDERED_ITEM = re.compile(r"^\d+\.\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_CODE_SPAN = re.compile(r"`([^`]+?)`")
_BOLD = re.compile(r"\*\*([^\n*]+?)\*\*|__([^\n_]+?)__")
_ITALIC = re.compile(r"(?<!\*)\*([^\n*]+?)\*(?!\*)|(?<!\w)_([^\n_]+?)_(?!\w)")
_STASH = re.compile("\x00(\\d+)\x00")
_TABLE_DELIMITER = re.compile(r"^\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?$")
_CELL_SEPARATOR = re.compile(r"(?<!\\)\|")


def _table_cells(line: str) -> list[str]:
    """`| a | b |` -> `["a", "b"]`: the outer pipes are optional, and `\\|` is a pipe inside a
    cell rather than between two."""
    row = line.strip()
    row = row[1:] if row.startswith("|") else row
    row = row[:-1] if row.endswith("|") and not row.endswith("\\|") else row
    return [cell.strip().replace("\\|", "|") for cell in _CELL_SEPARATOR.split(row)]


def _alignment(delimiter_cell: str) -> str:
    """`:--` left, `--:` right, `:-:` centre -- as an attribute for the column's cells."""
    left, right = delimiter_cell.startswith(":"), delimiter_cell.endswith(":")
    align = "center" if left and right else "right" if right else "left" if left else None
    return f' style="text-align:{align}"' if align else ""


Marker = Callable[[str], str]
"""Adds icons to a run of escaped text whose bold and italic are already marked up."""


def _no_icons(text: str) -> str:
    return text


def _icon_marker(icons: Mapping[str, str] | None) -> Marker:
    """Puts the icon of each name in `icons` (name -> icon URL) in front of it wherever the name
    stands as words of its own -- "Iron Plates" too, but not the "Coal" of "Coal-Powered
    Generator": the longest name wins. Matched in escaped text, so the names are escaped too."""
    if not icons:
        return _no_icons
    by_name = {html.escape(name): url for name, url in icons.items()}
    names = "|".join(re.escape(name) for name in sorted(by_name, key=len, reverse=True))
    pattern = re.compile(rf"(?<![\w-])({names})s?(?![\w-])")

    def mark(text: str) -> str:
        return pattern.sub(
            lambda m: (
                f'<span class="ref"><img class="icon" alt="" '
                f'src="{html.escape(by_name[m.group(1)])}">{m.group(0)}</span>'
            ),
            text,
        )

    return mark


def _inline(text: str, mark: Marker = _no_icons) -> str:
    """Escapes `text` and applies inline markdown (code, bold, italic), then icons. Code spans are
    stashed before escaping so their contents can't be reinterpreted as bold/italic markers, and
    icons go in after bold/italic, whose `_` markers would otherwise find the ones in icon URLs."""
    codes: list[str] = []

    def stash(match: re.Match[str]) -> str:
        codes.append(html.escape(match.group(1)))
        return f"\x00{len(codes) - 1}\x00"

    text = _CODE_SPAN.sub(stash, text)
    text = html.escape(text)
    text = _BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = _ITALIC.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    text = mark(text)
    return _STASH.sub(lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)


def _render_blocks(text: str, mark: Marker = _no_icons) -> str:
    """Classifies `text` line by line rather than splitting on blank lines: models often follow a
    heading or numbered item directly with unindented `- ` sub-bullets, which then nest under that
    item instead of closing the list. Consecutive lines of one kind are grouped.

    Tables are GitHub's: a header row, a delimiter row with as many cells, then rows until a blank
    line or a line with no `|`; rows are padded or cut to the header's columns."""
    lines = text.split("\n")
    output: list[str] = []
    group_kind: str | None = None
    group_items: list[Any] = []
    fence: list[str] | None = None
    alignments: list[str] = []
    delimiter_at: int | None = None

    def row(cells: list[str], tag: str) -> str:
        return (
            "<tr>"
            + "".join(
                f"<{tag}{align}>{_inline(cell, mark)}</{tag}>"
                for cell, align in zip(cells, alignments, strict=True)
            )
            + "</tr>"
        )

    def flush() -> None:
        nonlocal group_kind, group_items
        if group_kind == "table" and group_items:
            header, *body = group_items
            output.append(
                f'<div class="table"><table><thead>{row(header, "th")}</thead>'
                f"<tbody>{''.join(row(cells, 'td') for cells in body)}</tbody></table></div>"
            )
        elif group_kind == "p" and group_items:
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

    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if fence is not None:
            if stripped.startswith("```"):
                output.append(f"<pre><code>{html.escape(chr(10).join(fence))}</code></pre>")
                fence = None
            else:
                fence.append(raw_line)
            continue
        if index == delimiter_at:  # the table's header already took it
            continue
        if stripped.startswith("```"):
            flush()
            fence = []
            continue
        if not stripped:
            flush()
            continue
        if group_kind == "table":
            if "|" in stripped:
                columns = len(alignments)
                group_items.append((_table_cells(stripped) + [""] * columns)[:columns])
                continue
            flush()
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if "|" in stripped and _TABLE_DELIMITER.match(following):
            header, delimiter = _table_cells(stripped), _table_cells(following)
            if len(header) == len(delimiter):
                flush()
                group_kind, group_items = "table", [header]
                alignments = [_alignment(cell) for cell in delimiter]
                delimiter_at = index + 1
                continue
        heading = _HEADING.match(stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            output.append(f"<h{level}>{_inline(heading.group(2), mark)}</h{level}>")
            continue
        unordered = _UNORDERED_ITEM.match(stripped)
        if unordered:
            item_html = _inline(unordered.group(1), mark)
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
            group_items.append({"text": _inline(ordered.group(1), mark), "sub": []})
            continue
        if group_kind != "p":
            flush()
            group_kind = "p"
        group_items.append(_inline(stripped, mark))

    if fence is not None:  # unterminated fence: render whatever was collected rather than drop it
        output.append(f"<pre><code>{html.escape(chr(10).join(fence))}</code></pre>")
    flush()
    return "".join(output)


def render_message(
    text: str | None, *, role: Role = "assistant", icons: Mapping[str, str] | None = None
) -> str:
    """Renders `text` as lightweight markdown (paragraphs, bold/italic/code, lists, headings)
    inside a role-tagged bubble. `None`/empty text (a `ResponseArtifact` with no chat content)
    still renders a bubble, so a missing response is visible rather than silently dropped.
    `icons` maps in-game names to icon URLs; see `_icon_marker`."""
    body = _render_blocks(text or "", _icon_marker(icons))
    if not body:
        body = "<p><em>(no response)</em></p>"
    return f'<div class="message message-{role}">{body}</div>'


def render_page(messages: Sequence[tuple[Role, str | None]], *, title: str = "Pioneer") -> str:
    body = "\n".join(render_message(text, role=role) for role, text in messages)
    head = f"<style>{STYLESHEET.read_text(encoding='utf-8')}</style>"
    return html_document(title, f'<main class="chat">\n{body}\n</main>', head=head)


STYLESHEET = static_path(__file__, "chat.css")
"""The chat's styles, Satisfactory's palette among them: the web UI's page uses them too."""
