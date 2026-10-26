"""Client for the Dedicated Server HTTPS API's `QueryServerState` function.

Per `CommunityResources/DedicatedServerAPIDocs.md`, shipped with the game: every function is a
POST to `<base_url>/api/v1` with `{"function": "...", "data": {...}}`, and a successful response
is `{"data": {...}}`, here nested under `ServerGameState`. The transport is injected (`PostJson`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pioneer.contracts import GameState, TransportError

_FUNCTION_NAME = "QueryServerState"


@dataclass(frozen=True)
class ServerUnavailable:
    """Typed "the dedicated server couldn't be reached or gave a response we can't use" result,
    returned instead of raising."""

    reason: str


class PostJson(Protocol):
    """POSTs `body` (already JSON-serializable) to `url` with `headers`, returning
    `(status_code, parsed_json_body)` — `parsed_json_body` is `None` if the response wasn't valid
    JSON. Raises `contracts.TransportError` if the request couldn't complete at all — a non-2xx
    status is an answer, and `query_server_state` handles that itself."""

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

    if status_code in (401, 403):
        return ServerUnavailable(reason=f"the server refused the API token (HTTP {status_code})")
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
