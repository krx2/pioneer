"""The one exception an injected transport raises.

Modules that talk to the LLM or the dedicated server take their transport as a plain callable and
never import an HTTP library themselves; each turns this into a typed "unavailable" result.
"""

from __future__ import annotations


class TransportError(Exception):
    """The request couldn't complete at all (connection refused, timeout, DNS failure, an unusable
    reply), as opposed to the other end answering something the caller can make sense of."""
