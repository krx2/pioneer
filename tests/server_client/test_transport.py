"""Tests for the real `post_json` transport, against a throwaway local HTTP server — plain HTTP,
since the transport's TLS handling (accepting a self-signed certificate) is the standard library's
job, not something worth standing up certificates to re-test."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from pioneer.contracts import GameState
from pioneer.server_client.client import TransportError, query_server_state
from pioneer.server_client.transport import post_json


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen.append((self.path, request, dict(self.headers)))  # type: ignore[attr-defined]
        status, reply = self.server.reply  # type: ignore[attr-defined]
        body = reply if isinstance(reply, bytes) else json.dumps(reply).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.seen = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _base_url(httpd: HTTPServer) -> str:
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def test_posts_json_with_the_given_headers_and_returns_the_reply(server) -> None:
    server.reply = (200, {"data": {"ServerGameState": {"TechTier": 4}}})

    status, reply = post_json(
        f"{_base_url(server)}/api/v1",
        {"function": "QueryServerState"},
        {"Authorization": "Bearer secret"},
    )

    assert (status, reply) == (200, {"data": {"ServerGameState": {"TechTier": 4}}})
    path, request, headers = server.seen[0]
    assert path == "/api/v1"
    assert request == {"function": "QueryServerState"}
    assert headers["Authorization"] == "Bearer secret"
    assert headers["Content-Type"] == "application/json"


def test_an_error_status_comes_back_with_its_body(server) -> None:
    server.reply = (401, {"errorCode": "invalid_token"})

    assert post_json(f"{_base_url(server)}/api/v1", {}, {}) == (401, {"errorCode": "invalid_token"})


def test_a_reply_that_is_not_a_json_object_is_none(server) -> None:
    server.reply = (200, b"<html>not json</html>")

    assert post_json(f"{_base_url(server)}/api/v1", {}, {}) == (200, None)


def test_an_unreachable_server_raises_transport_error() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]  # nothing listens here once the socket closes

    with pytest.raises(TransportError, match="could not reach"):
        post_json(f"http://127.0.0.1:{free_port}/api/v1", {}, {})


def test_query_server_state_over_real_http(server) -> None:
    server.reply = (
        200,
        {
            "data": {
                "ServerGameState": {
                    "ActiveSessionName": "stal mielec",
                    "GamePhase": "Phase_2",
                    "TechTier": 6,
                }
            }
        },
    )

    result = query_server_state(post_json, _base_url(server), "secret")

    assert result == GameState(phase="Phase_2", tech_tier=6, session_name="stal mielec")
