"""Composition root — wires config, the real LLM transport, and the Orchestrator into a runnable
assistant. Everything below this module is transport-agnostic and independently testable; this is
the one place a concrete LLM backend actually gets picked (implementation.md Stage 16).

Run as:

    python -m pioneer.app "I want to produce 10/min of Iron Plate"

Requires a local OpenAI-compatible LLM server (Ollama, llama.cpp, vLLM, ...) reachable at
`PIONEER_LLM_BASE_URL` -- see `.env.example`. Runs with an empty `OrchestratorContext` (no
knowledge base / save / resource DB wired in yet), so every tool that needs game data reports that
limitation gracefully rather than fabricating it -- wire a real `OrchestratorContext` once the
Knowledge Base, Resource DB, Save Parser and Dedicated Server Client are loaded at startup.
"""

from __future__ import annotations

import sys
import uuid

from pioneer.config import settings
from pioneer.contracts import ResponseArtifact
from pioneer.llm_client import chat_completion, tool_calling_chat_completion
from pioneer.orchestrator import OrchestratorContext, OrchestratorUnavailable, handle_query


def ask(
    question: str, context: OrchestratorContext | None = None
) -> ResponseArtifact | OrchestratorUnavailable:
    if not settings.llm_base_url or not settings.llm_model:
        raise RuntimeError(
            "PIONEER_LLM_BASE_URL / PIONEER_LLM_MODEL are not set -- copy .env.example to .env "
            "and point them at your local Ollama/llama.cpp/vLLM server first."
        )
    return handle_query(
        tool_calling_chat_completion,
        chat_completion,
        question,
        context or OrchestratorContext(),
        llm_base_url=settings.llm_base_url,
        llm_model=settings.llm_model,
        llm_api_key=settings.llm_api_key,
        response_id=str(uuid.uuid4()),
    )


def main() -> None:
    if len(sys.argv) < 2:
        print('usage: python -m pioneer.app "your question"')
        raise SystemExit(1)
    question = " ".join(sys.argv[1:])
    result = ask(question)
    if isinstance(result, OrchestratorUnavailable):
        print(f"Pioneer is unavailable: {result.reason}")
        raise SystemExit(1)
    print(result.chat or "(no answer)")


if __name__ == "__main__":
    main()
