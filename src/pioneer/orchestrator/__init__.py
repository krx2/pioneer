"""LLM Orchestrator — final integration layer (implementation.md Stage 16).

The only piece of the system allowed to depend on every other module. Routes tool calls from a
local, OpenAI-compatible LLM to the deterministic Stage 2-11 modules, runs the Verifier where a
tool needs it, and composes the final `ResponseArtifact`; `verify_response` then scores that
artifact (architecture.md §6). The concrete LLM transport is injected (`pioneer.llm_client`
supplies the real one) -- this module never imports an HTTP library itself.

Layout, each module importing only the ones above it:
- `base`: the context, the typed failure, the tool shape and the artifact accumulator.
- `items`: item names and ids -- resolving what the model passes, and the `names` map.
- `existing_factory`: the save's factory as the Verifier sees it -- balance, power, site graphs.
- `summaries`: compact summaries of a production graph for the model.
- `factory_tools`, `planning_tools`: the tool handlers.
- `tools`: the tool schemas offered to the model, bound to their handlers.
- `tool_calls`: running the model's tool calls, including ones written as text.
- `orchestrator`: the routing loop, the system prompt and the conversation history.
- `verification`: scoring a finished answer.
"""

from pioneer.contracts import TransportError
from pioneer.orchestrator.base import OrchestratorContext, OrchestratorUnavailable, ToolCallingLLM
from pioneer.orchestrator.items import display_names
from pioneer.orchestrator.orchestrator import handle_query
from pioneer.orchestrator.verification import verify_response

__all__ = [
    "OrchestratorContext",
    "OrchestratorUnavailable",
    "ToolCallingLLM",
    "TransportError",
    "display_names",
    "handle_query",
    "verify_response",
]
