"""The one exception an injected transport raises.

Every module that talks to something outside the process — the LLM (`qa_engine`, `orchestrator`),
the dedicated server (`server_client`) — takes its transport as a plain callable and never imports
an HTTP library itself. This is the part of those callables' contract that isn't data: the request
couldn't complete at all (connection refused, timeout, DNS failure, a reply in a shape that isn't
usable), as opposed to the other end answering something the caller can make sense of. Each of
those modules handles it its own way, and each turns it into a typed "unavailable" result rather
than letting it escape (architecture.md invariant #5).
"""

from __future__ import annotations


class TransportError(Exception):
    """The request couldn't complete at all — see this module's docstring."""
