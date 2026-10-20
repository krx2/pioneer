"""Getting at the Dedicated Server API: whether a server answers at all, whether it takes a token,
and getting a token out of its admin password.

Shapes per `CommunityResources/DedicatedServerAPIDocs.md`, shipped with the game:

- `HealthCheck` needs no token and answers `{"Health": "healthy" | "slow"}`.
- `VerifyAuthenticationToken` answers 204 No Content for a token the server takes, 401 for one
  it doesn't.
- `PasswordLogin` turns a password into a token — but the docs call the tokens it issues
  short-lived and bound to a player, and tell third-party applications to use an application
  token instead: one that never expires, made by the console command `server.GenerateAPIToken`.
  So `log_in` logs in as administrator only to run that command through `RunCommand`, and hands
  back the application token it prints. Should the command's output not hold one this module can
  recognise, it hands back the login token, saying it won't last.

Tokens are recognised by their documented shape: Base64-encoded JSON, a dot, a hex fingerprint;
the JSON's `pl` is the privilege the token grants (`APIToken` for an application token). No
token is ever logged, nor put in a `ServerUnavailable` reason.

Like `client.query_server_state`, nothing here raises: every failure is a `ServerUnavailable`.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any

from pioneer.server_client.client import PostJson, ServerUnavailable, TransportError

_TOKEN_SHAPE = re.compile(r"ey[A-Za-z0-9+/=_-]+\.[0-9A-Fa-f]+")
_ERRORS = {
    "wrong_password": "wrong password",
    "passwordless_login_not_possible": "the server needs a password",
}


@dataclass(frozen=True)
class ServerHealth:
    health: str
    """"healthy", or "slow" when the server ticks fewer than ten times a second."""


@dataclass(frozen=True)
class LoginToken:
    token: str
    lasting: bool
    """`True` for an application token, which never expires; `False` for the login token itself,
    which the server lets lapse."""


def health_check(post_json: PostJson, base_url: str) -> ServerHealth | ServerUnavailable:
    answer = _call(post_json, base_url, "HealthCheck", {"ClientCustomData": ""})
    if isinstance(answer, ServerUnavailable):
        return answer
    health = answer.get("Health")
    if not isinstance(health, str):
        return ServerUnavailable(reason="malformed response: missing 'Health'")
    return ServerHealth(health=health)


def verify_token(post_json: PostJson, base_url: str, token: str) -> bool | ServerUnavailable:
    """Whether the server takes `token`."""
    try:
        status_code, _ = post_json(
            f"{base_url}/api/v1",
            {"function": "VerifyAuthenticationToken"},
            {"Authorization": f"Bearer {token}"},
        )
    except TransportError as error:
        return ServerUnavailable(reason=f"could not reach dedicated server: {error}")
    if status_code in (200, 204):
        return True
    if status_code in (401, 403):
        return False
    return ServerUnavailable(reason=f"dedicated server returned HTTP {status_code}")


def log_in(post_json: PostJson, base_url: str, password: str) -> LoginToken | ServerUnavailable:
    """A token for the server whose admin password is `password` — a lasting application token
    when the server makes one (see the module docstring)."""
    answer = _call(
        post_json,
        base_url,
        "PasswordLogin",
        {"MinimumPrivilegeLevel": "Administrator", "Password": password},
    )
    if isinstance(answer, ServerUnavailable):
        return answer
    login_token = answer.get("AuthenticationToken")
    if not isinstance(login_token, str) or not login_token:
        return ServerUnavailable(reason="malformed response: missing 'AuthenticationToken'")

    made = _call(
        post_json, base_url, "RunCommand", {"Command": "server.GenerateAPIToken"}, login_token
    )
    output = made.get("CommandResult") if isinstance(made, dict) else None
    api_token = application_token_in(output) if isinstance(output, str) else None
    if api_token is None:
        return LoginToken(token=login_token, lasting=False)
    return LoginToken(token=api_token, lasting=True)


def application_token_in(text: str) -> str | None:
    """The first application token in `text` — what `server.GenerateAPIToken` prints."""
    for candidate in _TOKEN_SHAPE.findall(text):
        if token_privilege(candidate) == "APIToken":
            return candidate
    return None


def token_privilege(token: str) -> str | None:
    """The privilege level `token` says it grants, or `None` if it isn't shaped like a token."""
    payload = token.split(".", 1)[0]
    padded = payload + "=" * (-len(payload) % 4)
    for decode in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            claims = json.loads(decode(padded))
        except (binascii.Error, ValueError):
            continue
        if isinstance(claims, dict) and isinstance(claims.get("pl"), str):
            return claims["pl"]
    return None


def _call(
    post_json: PostJson,
    base_url: str,
    function: str,
    data: dict[str, Any],
    token: str | None = None,
) -> dict[str, Any] | ServerUnavailable:
    """One API function's response data, or why there's none."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        status_code, response = post_json(
            f"{base_url}/api/v1", {"function": function, "data": data}, headers
        )
    except TransportError as error:
        return ServerUnavailable(reason=f"could not reach dedicated server: {error}")
    code = response.get("errorCode") if isinstance(response, dict) else None
    if isinstance(code, str):
        message = response.get("errorMessage") if isinstance(response, dict) else None
        return ServerUnavailable(reason=_ERRORS.get(code) or message or code)
    if status_code in (401, 403):
        return ServerUnavailable(reason=f"the server refused the request (HTTP {status_code})")
    if not 200 <= status_code < 300:
        return ServerUnavailable(reason=f"dedicated server returned HTTP {status_code}")
    data_out = response.get("data") if isinstance(response, dict) else None
    return data_out if isinstance(data_out, dict) else {}
