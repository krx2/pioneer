"""The real LLM transport: OpenAI-compatible chat/completions against the local backend
`pioneer.config.Settings` names. `chat_completion` serves `qa_engine.engine.ChatCompletion`,
`tool_calling_chat_completion` serves `orchestrator.base.ToolCallingLLM`.
"""

from pioneer.llm_client.transport import (
    MIN_CONTEXT_TOKENS,
    TransportError,
    chat_completion,
    context_window_warning,
    post_chat_completion,
    served_context_length,
    stream_chat_completion,
    streaming_tool_calling_chat_completion,
    tool_calling_chat_completion,
)

__all__ = [
    "MIN_CONTEXT_TOKENS",
    "TransportError",
    "chat_completion",
    "context_window_warning",
    "post_chat_completion",
    "served_context_length",
    "stream_chat_completion",
    "streaming_tool_calling_chat_completion",
    "tool_calling_chat_completion",
]
