"""`python -m pioneer.app "I want to produce 10/min of Iron Plate"` — one question, in the
terminal, answered from the same data and choices the web UI uses."""

from __future__ import annotations

import logging
import sys

from pioneer.app.runtime import ask, load_context
from pioneer.orchestrator import OrchestratorUnavailable


def main() -> None:
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2:
        print('usage: python -m pioneer.app "your question"')
        raise SystemExit(1)

    context, summary = load_context()
    print(f"[{summary}]", file=sys.stderr)

    result = ask(" ".join(sys.argv[1:]), context)
    if isinstance(result, OrchestratorUnavailable):
        print(f"Pioneer is unavailable: {result.reason}")
        raise SystemExit(1)
    print(result.chat or "(no answer)")


if __name__ == "__main__":
    main()
