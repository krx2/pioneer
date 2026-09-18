"""`python -m pioneer.web [--host HOST] [--port PORT]` — the Pioneer web UI on a local port.

Loads the same data the CLI does, kept current while it runs (`app.LiveContext`: a newer save or
a stale server answer is picked up on the next question), answers through the same `app.ask`,
verifies every answer with `orchestrator.verify_response` — with LLM-as-a-judge verdicts when
`PIONEER_LLM_JUDGE` is on — and keeps player feedback and a response log under `data/`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

import uvicorn

from pioneer.app import DATA_DIR, ask, live_context
from pioneer.config import settings
from pioneer.icons import ICON_DIR
from pioneer.llm_client import context_window_warning
from pioneer.llm_client.judges import chat_judge, terrain_judge
from pioneer.orchestrator import OrchestratorContext, verify_response
from pioneer.verification_feedback import JsonlFeedbackStore, ResponseLog
from pioneer.web.server import create_app


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pioneer.web", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    base_url, model = settings.llm_base_url, settings.llm_model
    if not base_url or not model:
        print(
            "PIONEER_LLM_BASE_URL / PIONEER_LLM_MODEL are not set -- copy .env.example to .env "
            "and point them at your local LLM server first.",
            file=sys.stderr,
        )
        return 1

    live = live_context()

    def current() -> tuple[OrchestratorContext, str]:
        """The live context, its status also saying when the model's window is too small."""
        context, status = live.current()
        warning = context_window_warning(base_url, model)
        return context, f"{status} | {warning}" if warning else status

    _, status = current()
    print(f"[{status}]", file=sys.stderr)

    judges: dict[str, Any] = {}
    if settings.llm_judge:
        judges = {
            "chat_judge": chat_judge(base_url, model, settings.llm_api_key),
            "terrain_judge": terrain_judge(base_url, model, settings.llm_api_key),
        }

    app = create_app(
        context=current,
        answer=lambda question, context, history: ask(question, context, history),
        verify=lambda artifact, context: verify_response(artifact, context, **judges),
        feedback_store=JsonlFeedbackStore(DATA_DIR / "feedback.jsonl"),
        response_log=ResponseLog(DATA_DIR / "responses.jsonl"),
        icon_dir=ICON_DIR,
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
