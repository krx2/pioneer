"""`python -m pioneer.web [--host HOST] [--port PORT]` — the Pioneer web UI on a local port.

Answers from the same data the CLI does, kept current while it runs, verifies every answer (with
LLM-as-a-judge verdicts when `PIONEER_LLM_JUDGE` is on), and keeps player feedback, a response log
and the conversations under `data/`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from typing import Any

import uvicorn

from pioneer.app import DATA_DIR, ask, live_context
from pioneer.config import settings
from pioneer.icons import ICON_DIR
from pioneer.llm_client import context_window_warning
from pioneer.llm_client.judges import chat_judge, terrain_judge
from pioneer.map_image import map_image, map_tiles
from pioneer.orchestrator import verify_response
from pioneer.verification_feedback import JsonlFeedbackStore, ResponseLog
from pioneer.web.conversations import ConversationStore
from pioneer.web.server import create_app


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
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

    # Saves are read in the background, so that picking one in the page doesn't hold up its
    # requests -- but the first is waited for: the first question should know the factory.
    live = live_context(
        background=True, context_warning=lambda: context_window_warning(base_url, model)
    )
    _, status = live.current(wait=True)
    print(f"[{status}]", file=sys.stderr)

    judges: dict[str, Any] = {}
    if settings.llm_judge:
        judges = {
            "chat_judge": chat_judge(base_url, model, settings.llm_api_key),
            "terrain_judge": terrain_judge(base_url, model, settings.llm_api_key),
        }

    app = create_app(
        live=live,
        answer=ask,
        verify=lambda artifact, context: verify_response(artifact, context, **judges),
        feedback_store=JsonlFeedbackStore(DATA_DIR / "feedback.jsonl"),
        response_log=ResponseLog(DATA_DIR / "responses.jsonl"),
        icon_dir=ICON_DIR,
        conversations=ConversationStore(DATA_DIR / "conversations"),
        map_image=map_image(),
        map_tiles=map_tiles(),
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
