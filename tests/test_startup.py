"""Tests for the launcher, against fake ports, fake processes and a fake clock — nothing is
actually started."""

from pathlib import Path

import pytest

from pioneer.startup import (
    Service,
    Started,
    game_server_service,
    ollama_service,
    start_services,
)


class _FakeProcess:
    def __init__(self, command) -> None:
        self.args = list(command)
        self.terminated = False
        self.killed = False
        self.running = True

    def poll(self):
        return None if self.running else 0

    def terminate(self) -> None:
        self.terminated = True
        self.running = False

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


class _World:
    """Ports that open a fixed number of polls after their service starts."""

    def __init__(self, already_up: set[int] = frozenset(), opens_after: int = 0) -> None:
        self.up = set(already_up)
        self.opens_after = opens_after
        self.started: list[_FakeProcess] = []
        self.polls: dict[int, int] = {}
        self.slept = 0.0
        self.now = 0.0
        self.log: list[str] = []

    def listening(self, host: str, port: int) -> bool:
        if port in self.up:
            return True
        if any(port == started.port for started in self.pending):
            self.polls[port] = self.polls.get(port, 0) + 1
            if self.polls[port] > self.opens_after:
                self.up.add(port)
                return True
        return False

    pending: list = []

    def spawn(self, command):
        process = _FakeProcess(command)
        self.started.append(process)
        return process

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds

    def clock(self) -> float:
        return self.now

    def run(self, services, **options):
        self.pending = list(services)
        return start_services(
            services,
            listening=self.listening,
            spawn=self.spawn,
            sleep=self.sleep,
            clock=self.clock,
            log=self.log.append,
            **options,
        )


_MODEL = Service(name="model", command=("ollama", "serve"), port=11434, ready_timeout=30)
_GAME = Service(name="game server", command=("FactoryServer.exe",), port=7777)


def test_a_service_already_listening_is_left_alone() -> None:
    world = _World(already_up={11434, 7777})

    started = world.run([_MODEL, _GAME])

    assert world.started == []
    assert started.processes == []
    assert world.log == [
        "model: already running on port 11434",
        "game server: already running on port 7777",
    ]


def test_a_service_that_is_not_up_is_started_and_waited_for() -> None:
    world = _World(opens_after=2)

    started = world.run([_MODEL])

    assert [process.args for process in started.processes] == [["ollama", "serve"]]
    assert world.slept == pytest.approx(0.5)  # polled, waited half a second, polled again
    assert world.log[-1] == "model: up on port 11434"


def test_a_service_with_no_timeout_is_started_without_waiting() -> None:
    world = _World(opens_after=99)

    world.run([_GAME])

    assert [process.args for process in world.started] == [["FactoryServer.exe"]]
    assert world.slept == 0
    assert "game server: starting FactoryServer.exe" in world.log


def test_a_service_that_never_answers_does_not_hold_up_the_rest() -> None:
    world = _World(opens_after=999)

    started = world.run([_MODEL, _GAME])

    assert len(started.processes) == 2
    assert "model: no answer on port 11434 yet -- carrying on" in world.log


def test_a_service_that_cannot_be_started_is_reported_and_skipped() -> None:
    world = _World()

    def refuse(command):
        if "ollama" in command[0]:
            raise OSError("not found")
        return world.spawn(command)

    started = start_services(
        [_MODEL, _GAME],
        listening=lambda host, port: False,
        spawn=refuse,
        sleep=world.sleep,
        clock=world.clock,
        log=world.log.append,
    )

    assert [process.args for process in started.processes] == [["FactoryServer.exe"]]
    assert "model: could not start it (not found)" in world.log


def test_stopping_terminates_what_is_still_running() -> None:
    running, finished = _FakeProcess(["a"]), _FakeProcess(["b"])
    finished.running = False
    started = Started(processes=[running, finished])

    started.stop(log=lambda message: None)

    assert (running.terminated, finished.terminated) == (True, False)


def test_the_model_service_points_at_the_configured_local_endpoint() -> None:
    service = ollama_service("http://localhost:11500/v1", log=lambda message: None)

    assert service is not None
    assert (service.host, service.port) == ("localhost", 11500)
    assert service.command[-1] == "serve"
    assert service.command[0].endswith(("ollama", "ollama.exe", "ollama.EXE"))


def test_a_model_served_elsewhere_or_unset_is_not_ours_to_start() -> None:
    log: list[str] = []

    assert ollama_service("http://gpu-box.lan:11434/v1", log=log.append) is None
    assert ollama_service(None, log=log.append) is None
    assert "gpu-box.lan isn't this machine -- leaving it alone" in log[0]
    assert "PIONEER_LLM_BASE_URL is not set" in log[1]


def test_the_game_server_is_found_by_folder_or_by_file(tmp_path: Path) -> None:
    executable = tmp_path / "FactoryServer.exe"
    executable.write_text("", encoding="utf-8")

    from_folder = game_server_service(tmp_path, log=lambda message: None)
    from_file = game_server_service(executable, log=lambda message: None)

    assert from_folder is not None and from_file is not None
    assert from_folder.command == from_file.command == (str(executable),)
    assert from_folder.port > 0
    assert from_folder.ready_timeout == 0  # it takes minutes: don't wait for it


def test_a_game_server_that_is_not_installed_says_where_it_looked(tmp_path: Path) -> None:
    log: list[str] = []

    assert game_server_service(tmp_path, log=log.append) is None
    assert str(tmp_path / "FactoryServer.exe") in log[0]
    assert "PIONEER_SERVER_EXE" in log[0]
