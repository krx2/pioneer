"""LLM Orchestrator — final integration layer (implementation.md Stage 16).

The only piece of the system allowed to depend on every other module. Routes tool calls from a
local, OpenAI-compatible LLM to the deterministic Stage 2-11 modules, runs the Verifier where a
tool needs it, and composes the final `ResponseArtifact`; `verify_response` then scores that
artifact (architecture.md §6). The concrete LLM transport is injected (`pioneer.llm_client`
supplies the real one) -- this module never imports an HTTP library itself.
"""

from pioneer.orchestrator.orchestrator import (
    OrchestratorContext,
    OrchestratorUnavailable,
    ToolCallingLLM,
    TransportError,
    display_names,
    handle_query,
)
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
