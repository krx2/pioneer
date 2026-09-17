"""Dedicated server API client module (implementation.md Stage 6).

Pulls live game state: progress, phase, session info. Depends only on `pioneer.contracts`;
tested against mocked HTTP responses, including the "server unreachable" case, which must
surface as a typed result, never a raw exception. `transport.post_json` is the real HTTPS
transport the app wires in.
"""

from pioneer.server_client.client import (
    PostJson,
    ServerUnavailable,
    TransportError,
    query_server_state,
)
from pioneer.server_client.transport import post_json

__all__ = [
    "PostJson",
    "ServerUnavailable",
    "TransportError",
    "post_json",
    "query_server_state",
]
