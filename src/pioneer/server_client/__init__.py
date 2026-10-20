"""Dedicated server API client module (implementation.md Stage 6).

Pulls live game state: progress, phase, session info. Depends only on `pioneer.contracts`;
tested against mocked HTTP responses, including the "server unreachable" case, which must
surface as a typed result, never a raw exception. `transport.post_json` is the real HTTPS
transport the app wires in. `access` checks a server and gets a token for it (`HealthCheck`,
`VerifyAuthenticationToken`, admin password to application token).
"""

from pioneer.server_client.access import (
    LoginToken,
    ServerHealth,
    application_token_in,
    health_check,
    log_in,
    token_privilege,
    verify_token,
)
from pioneer.server_client.client import (
    PostJson,
    ServerUnavailable,
    TransportError,
    query_server_state,
)
from pioneer.server_client.transport import post_json

__all__ = [
    "LoginToken",
    "PostJson",
    "ServerHealth",
    "ServerUnavailable",
    "TransportError",
    "application_token_in",
    "health_check",
    "log_in",
    "post_json",
    "query_server_state",
    "token_privilege",
    "verify_token",
]
