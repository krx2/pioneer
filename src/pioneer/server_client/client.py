"""Client for the Dedicated Server HTTPS API's `QueryServerState` function.

API shape confirmed against `CommunityResources/DedicatedServerAPIDocs.md`, shipped with the game
itself: every function is a POST to `<base_url>/api/v1` with a JSON body `{"function": "...",
"data": {...}}`, wrapped in TLS (often a self-signed certificate — the docs explicitly call out
that clients must tolerate this), and a successful response is `{"data": {...}}`.
`QueryServerState`'s response nests further under a `ServerGameState` key, with fields
`GamePhase`, `TechTier`, `ActiveSessionName` among others — see `contracts/game_state.py` for which
of those this project keeps.

This module deliberately never imports an HTTP library itself. The actual wire transport (TLS,
self-signed certificate handling, retries, ...) is supplied by the caller as a `PostJson` callable
— the same pattern architecture.md uses for the LLM backend (`pioneer.config.Settings` names the
endpoint; the concrete client library is chosen where it's first needed). Here, that "first needed"
point is Stage 16's integration, not this module — so `query_server_state` stays fully testable
against fake transports with zero real networking, per implementation.md Stage 6's own fixture
philosophy (mocked HTTP responses, including the unreachable-server case).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pioneer.contracts import GameState

_FUNCTION_NAME = "QueryServerState"


@dataclass(frozen=True)
class ServerUnavailable:
    """Typed "the dedicated server couldn't be reached or gave a response we can't use" result —
    per architecture.md's graceful-degradation invariant (§7.5), this is what `query_server_state`
    returns instead of raising, so callers can degrade gracefully rather than crash."""

    reason: str


class TransportError(Exception):
    """Raised by a `PostJson` implementation when the request couldn't be completed at all
    (connection refused, timeout, DNS failure, TLS handshake failure, ...) — distinct from the
    server successfully responding with a non-2xx status, which `query_server_state` handles
    itself without needing this exception."""


class PostJson(Protocol):
    """POSTs `body` (already JSON-serializable) to `url` with `headers`, returning
    `(status_code, parsed_json_body)` — `parsed_json_body` is `None` if the response wasn't valid
    JSON. Raises `TransportError` if the request couldn't complete at all."""

    def __call__(
        self, url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any] | None]: ...


def query_server_state(
    post_json: PostJson, base_url: str, api_token: str
) -> GameState | ServerUnavailable:
    """`base_url` is e.g. `https://my-server:7777`; `api_token` is the Bearer token from
    `pioneer.config.Settings.dedicated_server_api_token`."""
    headers = {"Authorization": f"Bearer {api_token}"}
    body = {"function": _FUNCTION_NAME}

    try:
        status_code, response = post_json(f"{base_url}/api/v1", body, headers)
    except TransportError as error:
        return ServerUnavailable(reason=f"could not reach dedicated server: {error}")

    if status_code != 200:
        return ServerUnavailable(reason=f"dedicated server returned HTTP {status_code}")
    if response is None:
        return ServerUnavailable(reason="dedicated server response was not valid JSON")

    data = response.get("data")
    if not isinstance(data, dict):
        return ServerUnavailable(reason="malformed response: missing 'data'")
    raw_game_state = data.get("ServerGameState")
    if not isinstance(raw_game_state, dict):
        return ServerUnavailable(reason="malformed response: missing 'ServerGameState'")

    try:
        tech_tier = int(raw_game_state.get("TechTier", 0))
    except (TypeError, ValueError):
        return ServerUnavailable(
            reason=f"malformed response: TechTier {raw_game_state.get('TechTier')!r}"
        )
    return GameState(
        phase=str(raw_game_state.get("GamePhase") or "None"),
        tech_tier=tech_tier,
        session_name=str(raw_game_state.get("ActiveSessionName") or "") or None,
    )
