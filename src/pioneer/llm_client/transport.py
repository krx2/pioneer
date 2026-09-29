"""Real HTTP transport for the local OpenAI-compatible LLM endpoint (architecture.md §3.1).

This is the one place in the project that actually speaks HTTP to the LLM. Every module that needs
one (`qa_engine.engine`, `orchestrator.orchestrator`) receives it as a plain callable conforming to
its own Protocol -- the same transport-injection pattern `server_client.client` uses for the
dedicated server -- so this is where the concrete local backend (Ollama, llama.cpp, vLLM, ...)
actually gets picked, per implementation.md Stage 16 ("reuse that connection, don't stand up a
second LLM client"). Uses only the standard library (`urllib`) so pointing this at a local server
never needs a new project dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

from pioneer.contracts import TransportError

_TIMEOUT_SECONDS = 120.0
_PROBE_TIMEOUT_SECONDS = 1.0

MIN_CONTEXT_TOKENS = 16384
"""The context window a conversation needs: the system prompt and tool schemas alone come to about
2k tokens, before the conversation so far, every tool result and the answer itself."""


Delta = Callable[[str, str], None]
"""Told each piece of a streamed reply as it arrives: `("text", ...)` for the answer itself,
`("thinking", ...)` for a reasoning model's thoughts before it."""


def _chat_request(
    base_url: str, api_key: str | None, payload: dict[str, Any]
) -> tuple[str, urllib.request.Request]:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    return url, request


def post_chat_completion(
    base_url: str, api_key: str | None, payload: dict[str, Any]
) -> dict[str, Any]:
    """POSTs `payload` (an OpenAI chat/completions request body) to `{base_url}/chat/completions`
    and returns the parsed JSON response. Raises `contracts.TransportError` if the request couldn't
    complete at all, or didn't come back as valid JSON."""
    url, request = _chat_request(base_url, api_key, payload)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TransportError(f"could not reach {url}: {error}") from error
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise TransportError(f"invalid JSON from {url}: {error}") from error


def stream_chat_completion(
    base_url: str, api_key: str | None, payload: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    """Like `post_chat_completion` with `"stream": true`: yields each server-sent chunk of the
    reply, parsed, as it arrives. Raises `contracts.TransportError` if the request couldn't
    complete, or a chunk isn't valid JSON."""
    url, request = _chat_request(base_url, api_key, {**payload, "stream": True})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            for raw in response:
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue  # blank separators, SSE comments
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except json.JSONDecodeError as error:
                    raise TransportError(f"invalid JSON chunk from {url}: {error}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TransportError(f"could not reach {url}: {error}") from error


def served_context_length(base_url: str, model: str) -> int | None:
    """The context window Ollama has `model` loaded with, from its `/api/ps`; `None` when that
    can't be told -- the model isn't loaded yet, or the server isn't Ollama.

    Ollama cuts a prompt longer than the window from the front without an error, and the front is
    the system prompt and the tool schemas: the model then answers from the conversation alone,
    with no tools and none of the rules, and makes up the rest. Its OpenAI-compatible API has no
    way to ask for a bigger window (that's Ollama's own setting), so this is how Pioneer notices."""
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        with urllib.request.urlopen(f"{root}/api/ps", timeout=_PROBE_TIMEOUT_SECONDS) as response:
            loaded = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    models = loaded.get("models") if isinstance(loaded, dict) else None
    for entry in models if isinstance(models, list) else []:
        if isinstance(entry, dict) and model in (entry.get("name"), entry.get("model")):
            length = entry.get("context_length")
            return length if isinstance(length, int) else None
    return None


def context_window_warning(base_url: str, model: str) -> str | None:
    """A line for the page's status when the model's window is too small to be trusted."""
    window = served_context_length(base_url, model)
    if window is None or window >= MIN_CONTEXT_TOKENS:
        return None
    return (
        f"model context only {window} tokens, needs {MIN_CONTEXT_TOKENS}: "
        "answers lose their tools (see README)"
    )


def _first_message(response: dict[str, Any]) -> dict[str, Any]:
    try:
        return response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as error:
        raise TransportError(
            f"unexpected response shape from LLM endpoint: {response!r}"
        ) from error


def chat_completion(
    base_url: str, model: str, messages: list[dict[str, str]], api_key: str | None
) -> str:
    """Conforms to `pioneer.qa_engine.engine.ChatCompletion`."""
    message = _first_message(
        post_chat_completion(base_url, api_key, {"model": model, "messages": messages})
    )
    return message.get("content") or ""


def tool_calling_chat_completion(
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    api_key: str | None,
) -> dict[str, Any]:
    """Conforms to `pioneer.orchestrator.base.ToolCallingLLM`. Returns
    `{"content": str | None, "tool_calls": [{"id", "name", "arguments": dict}, ...]}`, with each
    tool call's JSON-string `arguments` already parsed so the orchestrator never touches the wire
    format directly."""
    response = post_chat_completion(
        base_url, api_key, {"model": model, "messages": messages, "tools": tools}
    )
    message = _first_message(response)
    return {
        "content": message.get("content"),
        "tool_calls": [_parsed_tool_call(call) for call in message.get("tool_calls") or []],
    }


def streaming_tool_calling_chat_completion(
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    api_key: str | None,
    *,
    on_delta: Delta,
) -> dict[str, Any]:
    """`tool_calling_chat_completion`, streamed: `on_delta` hears the answer's text, and a
    reasoning model's thoughts, as they arrive, and the whole reply is returned in the same shape
    once it's complete. Tool calls come in pieces too -- a call's id and name first, its arguments
    a fragment at a time, by `index` -- and are put back together before they're parsed.

    A round that ends in tool calls may have streamed some text first, or have written the call
    itself as text: what's been heard is only a draft until the reply turns out to be the answer."""
    content: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    for chunk in stream_chat_completion(
        base_url, api_key, {"model": model, "messages": messages, "tools": tools}
    ):
        choices = chunk.get("choices") if isinstance(chunk, dict) else None
        if not choices:
            continue  # a usage-only or keep-alive chunk
        delta = choices[0].get("delta") or {}
        thinking = delta.get("reasoning_content") or delta.get("reasoning")
        if thinking:
            on_delta("thinking", thinking)
        if delta.get("content"):
            content.append(delta["content"])
            on_delta("text", delta["content"])
        for piece in delta.get("tool_calls") or []:
            call = calls.setdefault(
                piece.get("index", len(calls)),
                {"id": "", "function": {"name": "", "arguments": ""}},
            )
            call["id"] = piece.get("id") or call["id"]
            function = piece.get("function") or {}
            call["function"]["name"] += function.get("name") or ""
            arguments = function.get("arguments")
            if isinstance(arguments, dict):  # some backends send them already parsed
                call["function"]["arguments"] = arguments
            elif arguments:
                call["function"]["arguments"] += arguments
    return {
        "content": "".join(content) or None,
        "tool_calls": [_parsed_tool_call(calls[index]) for index in sorted(calls)],
    }


def _parsed_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function", {})
    arguments_raw = function.get("arguments") or "{}"
    try:
        arguments = json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw
    except json.JSONDecodeError as error:
        raise TransportError(
            f"LLM returned malformed tool-call arguments for {function.get('name')!r}: {error}"
        ) from error
    return {"id": call.get("id", ""), "name": function.get("name", ""), "arguments": arguments}
