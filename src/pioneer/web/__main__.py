"""`python -m pioneer.web [--host HOST] [--port PORT]` — the Pioneer web UI on a local port.

Loads the same data the CLI does (`app.load_context`), answers through the same `app.ask`,
verifies every answer with `orchestrator.verify_response` — with LLM-as-a-judge verdicts when
`PIONEER_LLM_JUDGE` is on — and keeps player feedback and a response log under `data/`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

import uvicorn

from pioneer.app import DATA_DIR, ask, load_context
from pioneer.config import settings
from pioneer.llm_client.judges import chat_judge, terrain_judge
from pioneer.orchestrator import verify_response
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

    context, status = load_context()
    print(f"[{status}]", file=sys.stderr)

    judges: dict[str, Any] = {}
    if settings.llm_judge:
        judges = {
            "chat_judge": chat_judge(base_url, model, settings.llm_api_key),
            "terrain_judge": terrain_judge(base_url, model, settings.llm_api_key),
        }

    app = create_app(
        context=context,
        answer=lambda question: ask(question, context),
        verify=lambda artifact: verify_response(artifact, context, **judges),
        feedback_store=JsonlFeedbackStore(DATA_DIR / "feedback.jsonl"),
        response_log=ResponseLog(DATA_DIR / "responses.jsonl"),
        status=status,
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
