"""Tests for the real HTTP transport, with `urllib.request.urlopen` monkeypatched. This is the one
module in the project allowed to touch real networking, so its own tests fake the socket layer
directly instead of injecting a Protocol -- there's no Protocol to inject here, this *is* the
Protocol implementation everything else's tests fake out."""

import json
from typing import Any

import pytest

from pioneer.contracts import TransportError
from pioneer.llm_client import transport

_BASE_URL = "http://localhost:11434/v1"
_MODEL = "test-model"


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _install_fake_urlopen(
    monkeypatch: pytest.MonkeyPatch, response_body: dict[str, Any]
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        captured["request"] = request
        return _FakeResponse(json.dumps(response_body).encode("utf-8"))

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    return captured


def _install_failing_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        raise OSError("connection refused")

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)


def test_chat_completion_extracts_reply_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_urlopen(
        monkeypatch, {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}
    )

    reply = transport.chat_completion(_BASE_URL, _MODEL, [{"role": "user", "content": "hi"}], None)

    assert reply == "hello"


def test_chat_completion_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_fake_urlopen(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})

    transport.chat_completion(_BASE_URL, _MODEL, [{"role": "user", "content": "hi"}], "secret-key")

    request = captured["request"]
    assert request.full_url == f"{_BASE_URL}/chat/completions"
    assert json.loads(request.data) == {
        "model": _MODEL,
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert request.get_header("Authorization") == "Bearer secret-key"


def test_chat_completion_without_api_key_sends_no_auth_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_urlopen(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})

    transport.chat_completion(_BASE_URL, _MODEL, [], None)

    assert captured["request"].get_header("Authorization") is None


def test_chat_completion_unreachable_raises_qa_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_failing_urlopen(monkeypatch)

    with pytest.raises(TransportError):
        transport.chat_completion(_BASE_URL, _MODEL, [], None)


def test_chat_completion_malformed_response_raises_qa_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_urlopen(monkeypatch, {"unexpected": "shape"})

    with pytest.raises(TransportError):
        transport.chat_completion(_BASE_URL, _MODEL, [], None)


def test_tool_calling_chat_completion_parses_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_urlopen(
        monkeypatch,
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "plan_production",
                                    "arguments": (
                                        '{"target_item_id": "Desc_IronPlate_C", '
                                        '"target_rate_per_minute": 10}'
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        },
    )

    result = transport.tool_calling_chat_completion(
        _BASE_URL, _MODEL, [{"role": "user", "content": "plan iron plates"}], [], None
    )

    assert result == {
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "name": "plan_production",
                "arguments": {"target_item_id": "Desc_IronPlate_C", "target_rate_per_minute": 10},
            }
        ],
    }


def test_tool_calling_chat_completion_with_no_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_urlopen(
        monkeypatch, {"choices": [{"message": {"content": "Iron Ore smelts 1:1 into Iron Ingot."}}]}
    )

    result = transport.tool_calling_chat_completion(_BASE_URL, _MODEL, [], [], None)

    assert result == {"content": "Iron Ore smelts 1:1 into Iron Ingot.", "tool_calls": []}


def test_tool_calling_chat_completion_unreachable_raises_orchestrator_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_failing_urlopen(monkeypatch)

    with pytest.raises(TransportError):
        transport.tool_calling_chat_completion(_BASE_URL, _MODEL, [], [], None)


def test_tool_calling_chat_completion_malformed_arguments_raises_orchestrator_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_urlopen(
        monkeypatch,
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {"name": "plan_production", "arguments": "not json"},
                            }
                        ],
                    }
                }
            ]
        },
    )

    with pytest.raises(TransportError):
        transport.tool_calling_chat_completion(_BASE_URL, _MODEL, [], [], None)


def test_the_served_context_window_is_read_from_ollamas_loaded_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_urlopen(
        monkeypatch,
        {
            "models": [
                {"name": "other:7b", "context_length": 32768},
                {"name": _MODEL, "model": _MODEL, "context_length": 4096},
            ]
        },
    )

    assert transport.served_context_length(_BASE_URL, _MODEL) == 4096
    assert captured["request"] == "http://localhost:11434/api/ps"
    assert "4096 tokens, needs 16384" in (transport.context_window_warning(_BASE_URL, _MODEL) or "")


def test_a_big_enough_or_unknown_window_gives_no_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_urlopen(monkeypatch, {"models": [{"name": _MODEL, "context_length": 16384}]})
    assert transport.context_window_warning(_BASE_URL, _MODEL) is None

    _install_fake_urlopen(monkeypatch, {"models": []})  # not loaded yet
    assert transport.served_context_length(_BASE_URL, _MODEL) is None

    _install_fake_urlopen(monkeypatch, {"error": "not Ollama"})
    assert transport.served_context_length(_BASE_URL, _MODEL) is None

    _install_failing_urlopen(monkeypatch)
    assert transport.context_window_warning(_BASE_URL, _MODEL) is None


class _FakeStream(_FakeResponse):
    """A streamed reply: iterated line by line, as `http.client.HTTPResponse` is."""

    def __init__(self, lines: list[bytes]) -> None:
        super().__init__(b"".join(lines))
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)


def _install_fake_stream(monkeypatch: pytest.MonkeyPatch, chunks: list[Any]) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    lines = [b": keep-alive\n", b"\n"]
    lines += [f"data: {json.dumps(chunk)}\n".encode() for chunk in chunks]
    lines += [b"\n", b"data: [DONE]\n"]

    def fake_urlopen(request: Any, timeout: float) -> _FakeStream:
        captured["request"] = request
        return _FakeStream(lines)

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    return captured


def _delta(**delta: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta}]}


def test_a_streamed_answer_is_heard_as_it_comes_and_returned_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_stream(
        monkeypatch,
        [
            _delta(role="assistant", reasoning="Plates need "),
            _delta(reasoning_content="ingots."),
            _delta(content="You need "),
            {"choices": []},  # a usage-only chunk
            _delta(content="2 Smelters."),
        ],
    )
    heard: list[tuple[str, str]] = []

    reply = transport.streaming_tool_calling_chat_completion(
        _BASE_URL, _MODEL, [], [], None, on_delta=lambda kind, text: heard.append((kind, text))
    )

    assert reply == {"content": "You need 2 Smelters.", "tool_calls": []}
    assert heard == [
        ("thinking", "Plates need "),
        ("thinking", "ingots."),
        ("text", "You need "),
        ("text", "2 Smelters."),
    ]
    assert json.loads(captured["request"].data)["stream"] is True


def test_streamed_tool_calls_are_put_back_together_by_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_stream(
        monkeypatch,
        [
            _delta(
                tool_calls=[
                    {"index": 0, "id": "call_a", "function": {"name": "plan_", "arguments": ""}}
                ]
            ),
            _delta(tool_calls=[{"index": 0, "function": {"name": "production"}}]),
            _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"target_item_id": '}}]),
            _delta(
                tool_calls=[
                    {"index": 1, "id": "call_b", "function": {"name": "factory_power"}},
                    {"index": 0, "function": {"arguments": '"Desc_IronPlate_C"}'}},
                ]
            ),
            _delta(tool_calls=[{"index": 1, "function": {"arguments": {}}}]),  # already parsed
        ],
    )

    reply = transport.streaming_tool_calling_chat_completion(
        _BASE_URL, _MODEL, [], [], None, on_delta=lambda kind, text: None
    )

    assert reply == {
        "content": None,
        "tool_calls": [
            {
                "id": "call_a",
                "name": "plan_production",
                "arguments": {"target_item_id": "Desc_IronPlate_C"},
            },
            {"id": "call_b", "name": "factory_power", "arguments": {}},
        ],
    }


def test_a_stream_that_fails_or_is_garbled_is_a_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_failing_urlopen(monkeypatch)
    with pytest.raises(TransportError, match="could not reach"):
        transport.streaming_tool_calling_chat_completion(
            _BASE_URL, _MODEL, [], [], None, on_delta=lambda kind, text: None
        )

    def garbled(request: Any, timeout: float) -> _FakeStream:
        return _FakeStream([b"data: {not json\n"])

    monkeypatch.setattr(transport.urllib.request, "urlopen", garbled)
    with pytest.raises(TransportError, match="invalid JSON chunk"):
        transport.streaming_tool_calling_chat_completion(
            _BASE_URL, _MODEL, [], [], None, on_delta=lambda kind, text: None
        )
