"""Chat presentation module (implementation.md Stage 12).

Renders the `chat` field of a `ResponseArtifact`. Depends only on `pioneer.contracts`; tested
against hand-written fixture artifacts, with no dependency on the orchestrator.
"""

from pioneer.chat_presentation.renderer import Role, render_message, render_page

__all__ = ["Role", "render_message", "render_page"]
