"""Tests for checking a server and logging in to it, against a fake server that answers each API
function the way `DedicatedServerAPIDocs.md` says the real one does — no networking."""

import base64
import json
from typing import Any

from pioneer.server_client import (
    LoginToken,
    ServerHealth,
    ServerUnavailable,
    TransportError,
    application_token_in,
    health_check,
    log_in,
    token_privilege,
    verify_token,
)

_BASE_URL = "https://factory.local:7777"


def _token(privilege: str, fingerprint: str = "0a1b2c3d4e5f") -> str:
    payload = base64.b64encode(json.dumps({"pl": privilege}).encode()).decode().rstrip("=")
    return f"{payload}.{fingerprint}"


_ADMIN = _token("Administrator")
_API = _token("APIToken", "ffee00112233")


class _FakeServer:
    """Answers by function name; records what it was asked."""

    def __init__(self, answers: dict[str, tuple[int, dict[str, Any] | None]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(self, url: str, body: dict[str, Any], headers: dict[str, str]):
        assert url == f"{_BASE_URL}/api/v1"
        self.calls.append((body["function"], body.get("data", {}), headers))
        return self.answers[body["function"]]


def _unreachable(url, body, headers):
    raise TransportError("connection refused")


def test_a_health_check_needs_no_token() -> None:
    server = _FakeServer({"HealthCheck": (200, {"data": {"Health": "slow"}})})

    assert health_check(server, _BASE_URL) == ServerHealth(health="slow")
    assert server.calls == [("HealthCheck", {"ClientCustomData": ""}, {})]


def test_an_unreachable_or_odd_server_is_a_typed_failure() -> None:
    odd = _FakeServer({"HealthCheck": (200, {"data": {}})})

    assert health_check(_unreachable, _BASE_URL) == ServerUnavailable(
        reason="could not reach dedicated server: connection refused"
    )
    assert health_check(odd, _BASE_URL) == ServerUnavailable(
        reason="malformed response: missing 'Health'"
    )


def test_a_token_is_verified_by_the_server() -> None:
    taken = _FakeServer({"VerifyAuthenticationToken": (204, None)})
    refused = _FakeServer({"VerifyAuthenticationToken": (401, None)})

    assert verify_token(taken, _BASE_URL, _API) is True
    assert verify_token(refused, _BASE_URL, "stale") is False
    assert taken.calls[0][2] == {"Authorization": f"Bearer {_API}"}
    assert isinstance(verify_token(_unreachable, _BASE_URL, _API), ServerUnavailable)


def test_the_admin_password_buys_a_lasting_application_token() -> None:
    server = _FakeServer(
        {
            "PasswordLogin": (200, {"data": {"AuthenticationToken": _ADMIN}}),
            "RunCommand": (200, {"data": {"CommandResult": f"Generated API Token: {_API}\n"}}),
        }
    )

    result = log_in(server, _BASE_URL, "hunter2")

    assert result == LoginToken(token=_API, lasting=True)
    login, command = server.calls
    assert login == (
        "PasswordLogin",
        {"MinimumPrivilegeLevel": "Administrator", "Password": "hunter2"},
        {},
    )
    assert command == (
        "RunCommand",
        {"Command": "server.GenerateAPIToken"},
        {"Authorization": f"Bearer {_ADMIN}"},
    )


def test_without_an_application_token_the_login_token_is_kept_as_one_that_lapses() -> None:
    for answer in (
        (200, {"data": {"CommandResult": "Unknown command"}}),
        (403, {"errorCode": "insufficient_scope"}),
    ):
        server = _FakeServer(
            {
                "PasswordLogin": (200, {"data": {"AuthenticationToken": _ADMIN}}),
                "RunCommand": answer,
            }
        )
        assert log_in(server, _BASE_URL, "hunter2") == LoginToken(token=_ADMIN, lasting=False)


def test_a_wrong_password_is_said_so_without_echoing_it() -> None:
    server = _FakeServer(
        {
            "PasswordLogin": (
                401,
                {"errorCode": "wrong_password", "errorMessage": "Wrong password"},
            )
        }
    )

    result = log_in(server, _BASE_URL, "hunter2")

    assert result == ServerUnavailable(reason="wrong password")
    assert [call[0] for call in server.calls] == ["PasswordLogin"]


def test_tokens_are_recognised_by_their_documented_shape() -> None:
    assert token_privilege(_API) == "APIToken"
    assert token_privilege(_ADMIN) == "Administrator"
    assert token_privilege("not-a-token") is None
    assert application_token_in(f"tokens: {_ADMIN} and {_API}") == _API
    assert application_token_in("no token here") is None
