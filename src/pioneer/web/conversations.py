"""The page's conversations, kept on disk so the sidebar can list them and any of them can be
picked up again — also after a restart, or in another browser.

One JSON file per conversation under `directory`: its title (the first question, shortened), when
it was started and last answered, and its turns, each the question and the answer exactly as the
page was sent it — rendered chat, verification badges, graph and map links — so reopening one
draws it as it was first shown. The graph and map pages those links point at are kept next to them
under `pages/`: the server holds only the newest answers in memory, and a conversation's panels
should outlive that.

Every id is checked against the shape the server itself hands out before it's made into a path,
so no request can name a file outside `directory`.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

PageKind = Literal["graph", "map"]

_ID = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$")
_TITLE_CHARS = 60


class ConversationStore:
    """Not thread-safe by itself: the server calls it under its own lock."""

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)
        self._pages = self._directory / "pages"

    def list(self) -> list[dict[str, Any]]:
        """Every conversation's id, title and last answer time, the most recent first."""
        found = [self._read(path) for path in self._directory.glob("*.json")]
        summaries = [
            {"id": c["id"], "title": c["title"], "updated_at": c["updated_at"]}
            for c in found
            if c is not None
        ]
        return sorted(summaries, key=lambda c: c["updated_at"], reverse=True)

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        path = self._path(conversation_id)
        return self._read(path) if path is not None else None

    def save_turn(
        self, conversation_id: str | None, turn: int, question: str, answer: dict[str, Any]
    ) -> str:
        """Puts `answer` to `question` at `turn` of the conversation — a new turn at its end, or
        in place of one answered again — and returns the conversation's id. Without an id, or
        with one no conversation has, a new conversation is started."""
        now = _now()
        conversation = self.get(conversation_id) if conversation_id else None
        if conversation is None:
            conversation = {
                "id": conversation_id if self._path(conversation_id) else str(uuid.uuid4()),
                "title": _title(question),
                "created_at": now,
                "turns": [],
            }
        turns: list[dict[str, Any]] = conversation["turns"]
        entry = {"question": question, "answer": answer}
        if 0 <= turn < len(turns):
            turns[turn] = entry
        else:
            turns.append(entry)
        conversation["updated_at"] = now
        path = self._path(conversation["id"])
        assert path is not None  # the id is one this store made or checked
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(conversation, ensure_ascii=False), encoding="utf-8")
        return conversation["id"]

    def delete(self, conversation_id: str) -> bool:
        """Removes a conversation and the pages of its answers; `False` if there was none."""
        conversation = self.get(conversation_id)
        path = self._path(conversation_id)
        if conversation is None or path is None:
            return False
        for turn in conversation["turns"]:
            response_id = turn.get("answer", {}).get("response_id", "")
            for kind in ("graph", "map"):
                page = self._page_path(response_id, kind)
                if page is not None:
                    page.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        return True

    def save_page(self, response_id: str, kind: PageKind, page: str) -> None:
        path = self._page_path(response_id, kind)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page, encoding="utf-8")

    def page(self, response_id: str, kind: PageKind) -> str | None:
        path = self._page_path(response_id, kind)
        if path is None or not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def _path(self, conversation_id: str | None) -> Path | None:
        if not conversation_id or not _ID.match(conversation_id):
            return None
        return self._directory / f"{conversation_id}.json"

    def _page_path(self, response_id: str, kind: PageKind) -> Path | None:
        if not _ID.match(response_id):
            return None
        return self._pages / f"{response_id}-{kind}.html"

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            conversation = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None  # gone, torn or hand-mangled: leave it out
        if not isinstance(conversation, dict) or not isinstance(conversation.get("turns"), list):
            return None
        if not all(key in conversation for key in ("id", "title", "updated_at")):
            return None
        return conversation


def _title(question: str) -> str:
    """The first question, on one line, cut at a word to about `_TITLE_CHARS`."""
    text = " ".join(question.split())
    if len(text) <= _TITLE_CHARS:
        return text
    cut = text[:_TITLE_CHARS].rsplit(" ", 1)[0]
    return f"{cut or text[:_TITLE_CHARS]}…"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
