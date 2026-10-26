"""Dedicated server API client: live game state (`client`), checking a server and getting a token
for it (`access`), and the real HTTPS transport (`transport.post_json`). An unreachable server is
a typed `ServerUnavailable` result, never an exception.
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
