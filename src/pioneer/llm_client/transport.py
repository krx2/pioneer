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
from typing import Any

from pioneer.contracts import TransportError

_TIMEOUT_SECONDS = 120.0
_PROBE_TIMEOUT_SECONDS = 1.0

MIN_CONTEXT_TOKENS = 16384
"""The context window a conversation needs: the system prompt and tool schemas alone come to about
2k tokens, before the conversation so far, every tool result and the answer itself."""


def post_chat_completion(
    base_url: str, api_key: str | None, payload: dict[str, Any]
) -> dict[str, Any]:
    """POSTs `payload` (an OpenAI chat/completions request body) to `{base_url}/chat/completions`
    and returns the parsed JSON response. Raises `contracts.TransportError` if the request couldn't
    complete at all, or didn't come back as valid JSON."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TransportError(f"could not reach {url}: {error}") from error
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise TransportError(f"invalid JSON from {url}: {error}") from error


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
    """Conforms to `pioneer.orchestrator.orchestrator.ToolCallingLLM`. Returns
    `{"content": str | None, "tool_calls": [{"id", "name", "arguments": dict}, ...]}`, with each
    tool call's JSON-string `arguments` already parsed so the orchestrator never touches the wire
    format directly."""
    response = post_chat_completion(
        base_url, api_key, {"model": model, "messages": messages, "tools": tools}
    )
    message = _first_message(response)

    tool_calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function", {})
        arguments_raw = function.get("arguments") or "{}"
        try:
            arguments = (
                json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw
            )
        except json.JSONDecodeError as error:
            raise TransportError(
                f"LLM returned malformed tool-call arguments for {function.get('name')!r}: {error}"
            ) from error
        tool_calls.append(
            {"id": call.get("id", ""), "name": function.get("name", ""), "arguments": arguments}
        )

    return {"content": message.get("content"), "tool_calls": tool_calls}
