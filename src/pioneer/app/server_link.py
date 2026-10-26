"""The dedicated server the assistant asks -- the one the page set, else the one `.env` names -- and
its last answer, which is trusted for `ttl_seconds` and then asked again."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from pioneer.app.loading import ServerCheck, check_server, query_game_state, server_login
from pioneer.app.preferences import SERVER, PreferencesStore, ServerConfig
from pioneer.contracts import GameState
from pioneer.server_client import PostJson, ServerUnavailable

QueryServer = Callable[[ServerConfig], GameState | ServerUnavailable]


class ServerLink:
    """`refresh`, `answered` and the state they keep are used under the live context's lock;
    `ask`, `check` and `connection` go over the network and are called outside it."""

    def __init__(
        self,
        preferences: PreferencesStore,
        *,
        env_server: ServerConfig | None,
        post: PostJson,
        query_server: QueryServer | None,
        ttl_seconds: float,
        clock: Callable[[], float],
    ) -> None:
        self._preferences = preferences
        self._env_server = env_server
        self._post = post
        self._query = query_server or (lambda server: query_game_state(server, post))
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self.game_state: GameState | None = None
        self.error: str | None = None
        """Why its state isn't known: unreachable, no token, a token it refused."""
        self._asked_at: float | None = None
        self._asked_for: ServerConfig | None = None

    def config(self) -> ServerConfig | None:
        """The server asked: the one the page set, else the one `.env` names."""
        if SERVER in self._preferences:
            return ServerConfig.from_json(self._preferences.get(SERVER))
        return self._env_server

    def source(self) -> str | None:
        """Where `config` comes from: "page" once set in the web UI -- also when set to nothing --
        else "env", or `None` with no server at all."""
        return "page" if SERVER in self._preferences else "env" if self._env_server else None

    def store(self, server: ServerConfig | None) -> None:
        """Asks `server` from now on, instead of what `.env` names; `None` asks none."""
        self._preferences.put(SERVER, server.to_json() if server is not None else None)

    def ask(self, server: ServerConfig) -> GameState | ServerUnavailable:
        return self._query(server)

    def refresh(self) -> bool:
        """Asks the server again if its answer is stale, or it's another server now; whether
        that changed anything."""
        server = self.config()
        asked_at = self._asked_at if server == self._asked_for else None
        if server is not None and asked_at is not None:
            if self._clock() - asked_at < self._ttl_seconds:
                return False
        before = (self.game_state, self.error, self._asked_for)
        self.answered(server, self._query(server) if server is not None else None)
        return before != (self.game_state, self.error, self._asked_for)

    def answered(
        self, server: ServerConfig | None, result: GameState | ServerUnavailable | None
    ) -> None:
        self._asked_for = server
        self._asked_at = self._clock() if server is not None else None
        if isinstance(result, ServerUnavailable):
            self.game_state, self.error = None, result.reason
        else:
            self.game_state, self.error = result, None

    def check(self, host: str, port: int, token: str | None = None) -> ServerCheck:
        """Whether the server at `host`:`port` answers, and takes `token` -- or, without one, the
        token in use. Nothing changes: the server asked stays the one it was."""
        return check_server(ServerConfig(host, port, token or self._kept_token()), self._post)

    def connection(
        self, host: str, port: int, *, token: str | None = None, password: str | None = None
    ) -> tuple[ServerConfig, str | None]:
        """The server at `host`:`port` to ask, and a note for the player when there's something
        they should know. With `password`, its admin password, logs in for a token, which is
        kept, and the password not (see `server_client.log_in`); with neither, the token in use is
        kept. Raises `ValueError` when the login fails."""
        server = ServerConfig(host, port, token or self._kept_token())
        if not password:
            return server, None
        login = server_login(server, password, self._post)
        if isinstance(login, ServerUnavailable):
            raise ValueError(f"could not log in: {login.reason}")
        note = (
            None
            if login.lasting
            else "The server didn't hand out a lasting API token, so the login token is kept "
            "instead -- the server lets those lapse. If it stops answering, log in again, "
            "or paste a token from server.GenerateAPIToken in the server's console."
        )
        return replace(server, token=login.token), note

    def _kept_token(self) -> str | None:
        server = self.config()
        return server.token if server is not None else None
