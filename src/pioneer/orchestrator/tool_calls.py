"""Running the model's tool calls: reading ones written as text, matching near-miss tool
and argument names, and turning every result -- or failure -- into a tool message."""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Mapping
from typing import Any

from pioneer.orchestrator.base import _Tool, _ToolError
from pioneer.orchestrator.items import _names_mentioned

_JSON_START = re.compile(r"[\[{]")


_NAME_MATCH_CUTOFF = 0.6
_ARGUMENT_MATCH_CUTOFF = 0.5


def _resolve_tool_name(name: object, tools_by_name: Mapping[str, _Tool]) -> str | None:
    """`name` if it's a real tool, else the closest real tool name it's a near-miss of --
    'expand_factory' for `expand_existing_factory`, say. A model that gets the call itself right
    but fumbles the exact registered spelling still shouldn't lose the whole tool call."""
    if not isinstance(name, str):
        return None
    if name in tools_by_name:
        return name
    matches = difflib.get_close_matches(name, tools_by_name.keys(), n=1, cutoff=_NAME_MATCH_CUTOFF)
    return matches[0] if matches else None


def _normalize_arguments(arguments: dict[str, Any], tool: _Tool) -> dict[str, Any]:
    """Remaps an argument key a model wrote under a plausible-but-wrong name ('item_id' for
    `target_item_id`, 'target_amount_per_minute' for `target_rate_per_minute', ...) onto the
    tool's actual parameter name, so a close-but-not-exact call still runs instead of failing on a
    missing required key. A key close to none of them is passed through unchanged -- the handler,
    not this heuristic, is what should reject it."""
    declared = tool.parameters.get("properties", {})
    if not declared:
        return arguments
    normalized: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in declared:
            normalized[key] = value
            continue
        match = difflib.get_close_matches(key, declared.keys(), n=1, cutoff=_ARGUMENT_MATCH_CUTOFF)
        normalized[match[0] if match else key] = value
    return normalized


def _tool_calls_written_as_text(
    content: str | None, tools_by_name: Mapping[str, _Tool]
) -> list[dict[str, Any]]:
    """Tool calls a model wrote into its answer instead of the API's `tool_calls` field -- local
    models do that often enough to be worth reading, rather than handing the player a line of JSON
    as their answer. Only a call naming (or near-naming, see `_resolve_tool_name`) a real tool
    counts; anything else is just an answer that happens to contain braces.

    Every JSON value in the text is read, one after another, whatever stands between them: prose,
    code fences, Qwen's `<tool_call>` tags, or the stray tokens it sometimes writes in place of
    the opening tag -- which is also what keeps the backend from recognizing the call itself."""
    written = [
        entry
        for value in _json_values(content or "")
        for entry in (value if isinstance(value, list) else [value])
    ]
    calls = []
    for call in written:
        if not isinstance(call, dict):
            continue
        call = call.get("function", call)
        resolved_name = _resolve_tool_name(call.get("name"), tools_by_name)
        arguments = call.get("arguments", call.get("parameters", {}))
        if resolved_name is not None and isinstance(arguments, dict):
            calls.append(
                {"id": f"text_call_{len(calls)}", "name": resolved_name, "arguments": arguments}
            )
    return calls


def _json_values(text: str) -> list[Any]:
    """Every JSON object or list in `text`, left to right."""
    decoder = json.JSONDecoder()
    values: list[Any] = []
    index = 0
    while (start := _JSON_START.search(text, index)) is not None:
        try:
            value, index = decoder.raw_decode(text, start.start())
        except json.JSONDecodeError:
            index = start.start() + 1
            continue
        values.append(value)
    return values


def _assistant_message(message: dict[str, Any], tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": message.get("content"),
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call.get("arguments") or {}),
                },
            }
            for call in tool_calls
        ],
    }


def _execute_tool(
    call: dict[str, Any], tools_by_name: dict[str, _Tool], names: Mapping[str, str]
) -> dict[str, Any]:
    """Runs one call and packages its result, with a `names` map for its ids, for the model. The
    name is resolved leniently (see `_resolve_tool_name`) because even the API's own `tool_calls`
    field isn't always the exact registered name with every local backend."""
    tool_name = _resolve_tool_name(call["name"], tools_by_name)
    tool = tools_by_name.get(tool_name) if tool_name else None
    if tool is None:
        result: dict[str, Any] = {"error": f"unknown tool {call['name']!r}"}
    else:
        result = _run_tool(tool, _normalize_arguments(call.get("arguments") or {}, tool))
    mentioned = _names_mentioned(json.dumps(result), names)
    if mentioned:
        result = {**result, "names": mentioned}
    return {
        "role": "tool",
        "tool_call_id": call["id"],
        "name": call["name"],
        "content": json.dumps(result),
    }


def _run_tool(tool: _Tool, arguments: dict[str, Any]) -> dict[str, Any]:
    """Runs one tool, turning any failure into an error result for the model rather than letting
    it escape `handle_query`. Deliberately catches everything: a bug in a module, or real data it
    didn't anticipate, should cost the player one tool call's worth of answer -- explained to them
    by the model -- not the whole response."""
    try:
        return tool.handler(arguments)
    except _ToolError as error:
        return {"error": str(error), **error.details}
    except Exception as error:
        return {"error": f"{tool.name} failed: {type(error).__name__}: {error}"}
