"""Whole HTML pages, and the static files beside a module that they're built from."""

from __future__ import annotations

import html
from pathlib import Path


def static_path(module_file: str, name: str) -> Path:
    """`static/<name>` beside the module whose `__file__` is `module_file`."""
    return Path(module_file).with_name("static") / name


def static_text(module_file: str, name: str) -> str:
    return static_path(module_file, name).read_text(encoding="utf-8")


def html_document(title: str, body: str, *, head: str = "") -> str:
    """A whole page: the charset, viewport and escaped `title`, then `head` (styles, scripts)."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
{head}
</head>
<body>
{body}
</body>
</html>
"""
