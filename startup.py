"""Starts the local model, the Satisfactory dedicated server and the web UI in one go.

    python startup.py [--host 127.0.0.1] [--port 8000] [--no-model] [--no-game-server]

Everything lives in `pioneer.startup`; this is the short way to run it from the project root.
"""

from pioneer.startup import main

if __name__ == "__main__":
    raise SystemExit(main())
