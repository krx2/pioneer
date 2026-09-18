"""Real LLM transport module (implementation.md Stage 16's concrete-backend choice).

The only module in the project that imports an HTTP-capable standard-library module to actually
talk to the LLM. Speaks the OpenAI-compatible chat/completions wire format that architecture.md
§3.1 fixes as the interface, against whatever local backend (Ollama, llama.cpp, vLLM, ...) is named
by `pioneer.config.Settings`. Exposes one adapter per Protocol a caller needs: `chat_completion` for
`qa_engine.engine.ChatCompletion`, `tool_calling_chat_completion` for
`orchestrator.orchestrator.ToolCallingLLM` -- both hit the same endpoint, so wiring both from the
same `Settings` reuses one logical connection rather than standing up two.
"""

from pioneer.llm_client.transport import (
    MIN_CONTEXT_TOKENS,
    TransportError,
    chat_completion,
    context_window_warning,
    post_chat_completion,
    served_context_length,
    tool_calling_chat_completion,
)

__all__ = [
    "MIN_CONTEXT_TOKENS",
    "TransportError",
    "chat_completion",
    "context_window_warning",
    "post_chat_completion",
    "served_context_length",
    "tool_calling_chat_completion",
]
