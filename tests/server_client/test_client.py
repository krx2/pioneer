"""Tests for query_server_state, against fake `PostJson` transports — no real networking, per
implementation.md Stage 6: mocked HTTP responses, including the unreachable-server case, which
must come back as a typed `ServerUnavailable`, never a raised exception."""

from typing import Any

import pytest

from pioneer.contracts import GameState
from pioneer.server_client.client import ServerUnavailable, TransportError, query_server_state

_BASE_URL = "https://my-server:7777"
_TOKEN = "test-token"


def _fake_transport(status_code: int, json_body: dict[str, Any] | None):
    calls = []

    def post_json(url: str, body: dict[str, Any], headers: dict[str, str]):
        calls.append((url, body, headers))
        return status_code, json_body

    post_json.calls = calls  # type: ignore[attr-defined]
    return post_json


def test_successful_response_is_parsed_into_game_state() -> None:
    transport = _fake_transport(
        200,
        {
            "data": {
                "ServerGameState": {
                    "ActiveSessionName": "stal mielec",
                    "TechTier": 6,
                    "GamePhase": "Phase_1",
                }
            }
        },
    )

    result = query_server_state(transport, _BASE_URL, _TOKEN)

    assert result == GameState(phase="Phase_1", tech_tier=6, session_name="stal mielec")


def test_request_shape_matches_the_documented_api() -> None:
    transport = _fake_transport(200, {"data": {"ServerGameState": {}}})

    query_server_state(transport, _BASE_URL, _TOKEN)

    (url, body, headers) = transport.calls[0]  # type: ignore[attr-defined]
    assert url == f"{_BASE_URL}/api/v1"
    assert body == {"function": "QueryServerState"}
    assert headers == {"Authorization": f"Bearer {_TOKEN}"}


def test_missing_game_phase_defaults_to_none_string() -> None:
    transport = _fake_transport(200, {"data": {"ServerGameState": {"TechTier": 0}}})

    result = query_server_state(transport, _BASE_URL, _TOKEN)

    assert isinstance(result, GameState)
    assert result.phase == "None"
    assert result.session_name is None


def test_transport_error_becomes_server_unavailable() -> None:
    def failing_transport(url: str, body: dict[str, Any], headers: dict[str, str]):
        raise TransportError("connection refused")

    result = query_server_state(failing_transport, _BASE_URL, _TOKEN)

    assert isinstance(result, ServerUnavailable)
    assert "connection refused" in result.reason


def test_non_200_status_becomes_server_unavailable() -> None:
    transport = _fake_transport(503, None)

    result = query_server_state(transport, _BASE_URL, _TOKEN)

    assert isinstance(result, ServerUnavailable)
    assert "503" in result.reason


def test_invalid_json_becomes_server_unavailable() -> None:
    transport = _fake_transport(200, None)

    result = query_server_state(transport, _BASE_URL, _TOKEN)

    assert isinstance(result, ServerUnavailable)


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"data": {}},
        {"data": {"ServerGameState": "not a dict"}},
        {"data": "not a dict"},
        {"data": {"ServerGameState": {"TechTier": None}}},
        {"data": {"ServerGameState": {"TechTier": "six"}}},
    ],
)
def test_malformed_response_shapes_become_server_unavailable(response: dict[str, Any]) -> None:
    transport = _fake_transport(200, response)

    result = query_server_state(transport, _BASE_URL, _TOKEN)

    assert isinstance(result, ServerUnavailable)


def test_query_server_state_never_raises_on_bad_transport() -> None:
    def flaky_transport(url: str, body: dict[str, Any], headers: dict[str, str]):
        raise TransportError("DNS resolution failed")

    # The whole point of ServerUnavailable: this must not raise.
    result = query_server_state(flaky_transport, _BASE_URL, _TOKEN)
    assert isinstance(result, ServerUnavailable)
