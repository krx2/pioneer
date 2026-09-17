"""Starts everything the assistant needs, then serves the web UI: the local model (Ollama), the
Satisfactory dedicated server, and `pioneer.web`.

    python startup.py            # or: python -m pioneer.startup

Each piece is optional and started only if nothing is already listening on its port, so running
this twice, or alongside a model you started yourself, is harmless. Whatever this script starts,
it also stops when the web UI exits; anything that was already running is left alone.

Paths and ports come from the environment (see `.env.example`): `PIONEER_LLM_BASE_URL` says where
the model is, `PIONEER_SERVER_HOST`/`PIONEER_SERVER_PORT` where the game server is, and
`PIONEER_SERVER_EXE` where its executable lives — `_DEFAULT_SERVER_EXE` is the usual Steam
location. A piece that isn't installed is reported and skipped: the assistant degrades to
whatever data it has (architecture.md invariant #5), so it's still worth serving the UI.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from pioneer.app import DEFAULT_SERVER_PORT
from pioneer.config import settings
from pioneer.web.__main__ import main as serve_web

_DEFAULT_SERVER_EXE = Path(r"D:\SteamLibrary\steamapps\common\SatisfactoryDedicatedServer")
"""Where Steam puts the dedicated server by default on this project's own machine — override with
`PIONEER_SERVER_EXE`, which may name either the folder or `FactoryServer.exe` itself."""
_SERVER_EXE_NAME = "FactoryServer.exe"
_OLLAMA_READY_TIMEOUT = 60.0
"""The model server answers in a second or two; the timeout is only there to fail rather than
hang. The game server isn't waited for at all — it takes minutes to load a save, and the
assistant answers without it."""
_POLL_SECONDS = 0.5


@dataclass(frozen=True)
class Service:
    """One process to start, and the port that tells whether it's up."""

    name: str
    command: tuple[str, ...]
    port: int
    host: str = "127.0.0.1"
    ready_timeout: float = 0.0
    """How long to wait for the port to open. `0`: start it and move on."""


@dataclass
class Started:
    """What `start_services` actually launched, so the caller can stop it again."""

    processes: list[subprocess.Popen[bytes]] = field(default_factory=list)

    def stop(self, log: Callable[[str], None] = print) -> None:
        for process in self.processes:
            if process.poll() is not None:
                continue
            log(f"stopping {process.args[0]}")
            process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def is_listening(host: str, port: int, *, timeout: float = 0.5) -> bool:
    """Whether something already answers on `host:port` — how every check here is made, since it
    works the same for a model server, a game server and a web server."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def start_services(
    services: Iterable[Service],
    *,
    listening: Callable[[str, int], bool] = is_listening,
    spawn: Callable[[Sequence[str]], subprocess.Popen[bytes]] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = print,
) -> Started:
    """Starts each service that isn't already up, waiting for the ones that ask to be waited for.
    A service that fails to start is reported and skipped -- the rest still run."""
    started = Started()
    for service in services:
        if listening(service.host, service.port):
            log(f"{service.name}: already running on port {service.port}")
            continue
        log(f"{service.name}: starting {' '.join(service.command)}")
        try:
            started.processes.append(spawn(service.command))
        except OSError as error:
            log(f"{service.name}: could not start it ({error})")
            continue
        if service.ready_timeout > 0 and not _wait_until_listening(
            service, listening, sleep, clock, log
        ):
            log(f"{service.name}: no answer on port {service.port} yet -- carrying on")
    return started


def _wait_until_listening(
    service: Service,
    listening: Callable[[str, int], bool],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    log: Callable[[str], None],
) -> bool:
    deadline = clock() + service.ready_timeout
    while clock() < deadline:
        if listening(service.host, service.port):
            log(f"{service.name}: up on port {service.port}")
            return True
        sleep(_POLL_SECONDS)
    return listening(service.host, service.port)


def ollama_service(base_url: str | None, log: Callable[[str], None] = print) -> Service | None:
    """The local model server named by `PIONEER_LLM_BASE_URL`, if that's a local one and Ollama is
    installed. A model served elsewhere isn't this script's to start."""
    if not base_url:
        log("model: PIONEER_LLM_BASE_URL is not set -- see .env.example")
        return None
    url = urlparse(base_url)
    host, port = url.hostname or "127.0.0.1", url.port or (443 if url.scheme == "https" else 80)
    if host not in ("127.0.0.1", "localhost", "::1"):
        log(f"model: {host} isn't this machine -- leaving it alone")
        return None
    executable = shutil.which("ollama")
    if executable is None:
        log("model: ollama isn't on PATH -- start your model server yourself")
        return None
    return Service(
        name="model",
        command=(executable, "serve"),
        host=host,
        port=port,
        ready_timeout=_OLLAMA_READY_TIMEOUT,
    )


def game_server_service(
    executable: Path | str | None, log: Callable[[str], None] = print
) -> Service | None:
    """The Satisfactory dedicated server, if its executable is where we're told to look."""
    path = Path(executable) if executable else _DEFAULT_SERVER_EXE
    if path.is_dir():
        path = path / _SERVER_EXE_NAME
    if not path.is_file():
        log(f"game server: {path} isn't there -- set PIONEER_SERVER_EXE to skip or fix this")
        return None
    return Service(
        name="game server",
        command=(str(path),),
        host=settings.dedicated_server_host or "127.0.0.1",
        port=settings.dedicated_server_port or DEFAULT_SERVER_PORT,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python startup.py", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="web UI address")
    parser.add_argument("--port", type=int, default=8000, help="web UI port")
    parser.add_argument("--no-model", action="store_true", help="don't start Ollama")
    parser.add_argument("--no-game-server", action="store_true", help="don't start the game server")
    parser.add_argument(
        "--server-exe",
        default=None,
        help=f"the dedicated server's folder or {_SERVER_EXE_NAME} (default: PIONEER_SERVER_EXE)",
    )
    args = parser.parse_args(argv)

    services = []
    if not args.no_model:
        services.append(ollama_service(settings.llm_base_url))
    if not args.no_game_server:
        services.append(game_server_service(args.server_exe or settings.dedicated_server_exe))

    started = start_services([service for service in services if service is not None])
    try:
        return serve_web(["--host", args.host, "--port", str(args.port)])
    except KeyboardInterrupt:
        return 0
    finally:
        started.stop()


if __name__ == "__main__":
    raise SystemExit(main())
