"""The chat page's own styles and scripts, served under `/static/`. Their URLs carry a hash of
what's in them, so a browser keeps each one until it changes and fetches it again as soon as it
does."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pioneer.chat_presentation import STYLESHEET as CHAT_STYLESHEET
from pioneer.common import static_path
from pioneer.map_presentation import MAP_VIEW_SCRIPT

STATIC_FILES: dict[str, Path] = {
    "chat.css": CHAT_STYLESHEET,
    "page.css": static_path(__file__, "page.css"),
    "panels.css": static_path(__file__, "panels.css"),
    "map-view.js": MAP_VIEW_SCRIPT,
    "page.js": static_path(__file__, "page.js"),
    "panels.js": static_path(__file__, "panels.js"),
}
"""Every file `/static/` serves, by the name it's served under: nothing else is."""

MEDIA_TYPES = {".css": "text/css", ".js": "text/javascript"}


def static_url(name: str) -> str:
    """Where the page asks for `STATIC_FILES[name]`, as it is now."""
    digest = hashlib.sha256(STATIC_FILES[name].read_bytes()).hexdigest()[:12]
    return f"/static/{name}?v={digest}"
