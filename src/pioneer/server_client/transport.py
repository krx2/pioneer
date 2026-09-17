"""Real HTTPS transport for the Dedicated Server API — the concrete `PostJson` that
`query_server_state` takes, picked at Stage 16 the way `llm_client` picks the LLM's.

Standard library only (`urllib`), like `llm_client`, so talking to a server never needs a new
dependency.

**No certificate verification.** A dedicated server serves a self-signed certificate unless its
owner installs a real one, and the API documentation shipped with the game
(`CommunityResources/DedicatedServerAPIDocs.md`) tells clients to accept that. So this does — for
the one host the player configured, with the API token they gave it. Anyone able to intercept that
connection could read the token; that's the game's own trade-off, not one this module can undo.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any

from pioneer.server_client.client import TransportError

_TIMEOUT_SECONDS = 10.0


def post_json(
    url: str, body: dict[str, Any], headers: dict[str, str]
) -> tuple[int, dict[str, Any] | None]:
    """Conforms to `server_client.client.PostJson`: an error status comes back as a status, with
    whatever JSON body the server sent; only a request that couldn't complete at all raises
    `TransportError`."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=_TIMEOUT_SECONDS, context=_unverified_tls()
        ) as response:
            return response.status, _json_object(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _json_object(error.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TransportError(f"could not reach {url}: {error}") from error


def _json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _unverified_tls() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context
