"""Web UI (implementation.md Stage 16's presentation wiring).

A FastAPI app serving the chat, and each answer's production graph and factory map, through the
Stage 12-14 renderers — plus the feedback buttons Stage 15 records. Run it with
`python -m pioneer.web`.
"""

from pioneer.web.server import AnsweredQuestion, create_app

__all__ = ["AnsweredQuestion", "create_app"]
