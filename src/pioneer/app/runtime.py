"""The assistant as configured: a `LiveContext` over what `.env` and the player's choices name,
and `ask`, which answers one question with the configured model."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from functools import partial

from pioneer.app.live import LiveContext
from pioneer.app.loading import (
    DEFAULT_SERVER_PORT,
    SETTINGS_JSON,
    load_knowledge_base,
    load_resource_nodes,
)
from pioneer.app.preferences import PreferencesStore, ServerConfig
from pioneer.config import settings
from pioneer.contracts import ResponseArtifact
from pioneer.llm_client import (
    chat_completion,
    streaming_tool_calling_chat_completion,
    tool_calling_chat_completion,
)
from pioneer.orchestrator import (
    OrchestratorContext,
    OrchestratorUnavailable,
    ProgressSink,
    handle_query,
)


def env_server() -> ServerConfig | None:
    """The dedicated server `.env` names, if it names one."""
    host = settings.dedicated_server_host
    if not host:
        return None
    return ServerConfig(
        host=host,
        port=settings.dedicated_server_port or DEFAULT_SERVER_PORT,
        token=settings.dedicated_server_api_token or None,
    )


def live_context(
    *, background: bool = False, context_warning: Callable[[], str | None] | None = None
) -> LiveContext:
    """A `LiveContext` over what's on disk, the server `.env` names and the choices the player
    made in the web UI (`data/settings.json`)."""
    return LiveContext(
        load_knowledge_base(),
        load_resource_nodes(),
        save_directory=settings.save_directory,
        save_root=settings.save_root,
        server=env_server(),
        preferences=PreferencesStore(SETTINGS_JSON),
        background=background,
        context_warning=context_warning,
    )


def load_context() -> tuple[OrchestratorContext, str]:
    """The context built from what's on disk right now, plus a one-line summary of what actually
    got loaded, for the CLI to report."""
    return live_context().current()


def ask(
    question: str,
    context: OrchestratorContext | None = None,
    history: Sequence[tuple[str, str]] = (),
    on_event: ProgressSink | None = None,
) -> ResponseArtifact | OrchestratorUnavailable:
    """One answer from the configured model; `history` is the conversation so far, as
    (question, answer) pairs. With `on_event`, the answer is streamed: the sink hears what the
    Orchestrator is doing and the model's text as it's written (see `orchestrator.ProgressSink`)."""
    if not settings.llm_base_url or not settings.llm_model:
        raise RuntimeError(
            "PIONEER_LLM_BASE_URL / PIONEER_LLM_MODEL are not set -- copy .env.example to .env "
            "and point them at your local Ollama/llama.cpp/vLLM server first."
        )
    if context is None:
        context, _ = load_context()
    transport = (
        partial(
            streaming_tool_calling_chat_completion,
            on_delta=lambda kind, text: on_event({"type": kind, "text": text}),
        )
        if on_event is not None
        else tool_calling_chat_completion
    )
    return handle_query(
        transport,
        chat_completion,
        question,
        context,
        llm_base_url=settings.llm_base_url,
        llm_model=settings.llm_model,
        llm_api_key=settings.llm_api_key,
        response_id=str(uuid.uuid4()),
        history=history,
        on_event=on_event,
    )
