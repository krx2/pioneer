"""The page's conversations, kept on disk so the sidebar can list them and any of them can be
picked up again — also after a restart, or in another browser.

One JSON file per conversation under `directory`: its title (the first question, shortened), when
it was started and last answered, and its turns, each the question and the answer exactly as the
page was sent it — rendered chat, verification badges, graph and map links — so reopening one
draws it as it was first shown. The graph and map pages those links point at are kept next to them
under `pages/`: the server holds only the newest answers in memory, and a conversation's panels
should outlive that.

A conversation also keeps its plan (see `contracts.plan`): an answer that planned something keeps
the plan it proposed next to it, as `web.plans` writes it, and accepting that proposal makes it the
conversation's plan -- only while it's still a proposal for the plan the conversation has, so a
revision of an older version, or a second first plan, can't overwrite a newer one. Each accepted
revision is noted in the plan's `history`, with the question that asked for it; what the player
marked built (`marked`, recipe id -> machines) stays with the plan across its revisions.

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


class PlanChanged(Exception):
    """The proposal isn't one for the plan the conversation has now: another one was accepted
    since."""


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
        self,
        conversation_id: str | None,
        turn: int,
        question: str,
        answer: dict[str, Any],
        plan_proposal: dict[str, Any] | None = None,
    ) -> str:
        """Puts `answer` to `question` at `turn` of the conversation — a new turn at its end, or
        in place of one answered again — with the plan it proposes, if any, and returns the
        conversation's id. Without an id, or with one no conversation has, a new conversation is
        started."""
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
        if plan_proposal is not None:
            entry["plan_proposal"] = plan_proposal
        if 0 <= turn < len(turns):
            turns[turn] = entry
        else:
            turns.append(entry)
        conversation["updated_at"] = now
        self._write(conversation)
        return conversation["id"]

    def plan(self, conversation_id: str) -> dict[str, Any] | None:
        """The conversation's plan as it was accepted: `plan`, the `response_id` of the answer that
        proposed it, its `history` and what the player `marked` built. `None` without a plan, or
        without the conversation."""
        conversation = self.get(conversation_id)
        return _accepted(conversation) if conversation is not None else None

    def accepted_plans(self) -> dict[str, dict[str, Any]]:
        """Every conversation's plan, as `plan` gives it, by conversation id -- those with one."""
        found = (self._read(path) for path in self._directory.glob("*.json"))
        return {
            conversation["id"]: accepted
            for conversation in found
            if conversation is not None and (accepted := _accepted(conversation)) is not None
        }

    def accept_plan(self, conversation_id: str, response_id: str) -> dict[str, Any] | None:
        """Makes the plan the answer `response_id` proposed the conversation's plan, and returns
        it as `plan` does. `None` when the conversation has no such answer, or it proposed no plan;
        `PlanChanged` when its proposal isn't for the plan the conversation has now."""
        conversation = self.get(conversation_id)
        if conversation is None:
            return None
        proposing = next(
            (
                turn
                for turn in conversation["turns"]
                if turn.get("answer", {}).get("response_id") == response_id
            ),
            {},
        )
        proposed = proposing.get("plan_proposal")
        if not isinstance(proposed, dict):
            return None
        current = _accepted(conversation)
        if current is not None and current.get("response_id") == response_id:
            return current  # accepted already
        revision = current["plan"].get("revision", 0) if current is not None else 0
        if proposed.get("revision") != revision + 1:
            raise PlanChanged(response_id)
        now = _now()
        before = current or {}
        conversation["plan"] = {
            "plan": proposed,
            "response_id": response_id,
            "accepted_at": now,
            "history": [
                *before.get("history", []),
                _revision(conversation, proposed, response_id, now),
            ],
            "marked": before.get("marked", {}),
        }
        self._write(conversation)
        return conversation["plan"]

    def mark_plan(
        self, conversation_id: str, recipe_id: str, machines: float
    ) -> dict[str, Any] | None:
        """Notes that the player marked `machines` of the plan's `recipe_id` stage built -- none
        clears the mark -- and returns the plan as `plan` does; `None` without one."""
        conversation = self.get(conversation_id)
        accepted = _accepted(conversation) if conversation is not None else None
        if conversation is None or accepted is None:
            return None
        marked = dict(accepted.get("marked") or {})
        if machines > 0:
            marked[recipe_id] = machines
        else:
            marked.pop(recipe_id, None)
        conversation["plan"] = {**accepted, "marked": marked}
        self._write(conversation)
        return conversation["plan"]

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

    def _write(self, conversation: dict[str, Any]) -> None:
        path = self._path(conversation["id"])
        assert path is not None  # the id is one this store made or checked
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(conversation, ensure_ascii=False), encoding="utf-8")

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


def _accepted(conversation: dict[str, Any]) -> dict[str, Any] | None:
    """The conversation's plan as it was accepted -- one accepted before revisions were noted with
    the one revision it is noted as its history."""
    accepted = conversation.get("plan")
    if not isinstance(accepted, dict):
        return None
    if isinstance(accepted.get("history"), list):
        return accepted
    plan = accepted.get("plan") if isinstance(accepted.get("plan"), dict) else {}
    noted = _revision(conversation, plan, accepted.get("response_id"), accepted.get("accepted_at"))
    return {**accepted, "history": [noted]}


def _revision(
    conversation: dict[str, Any], plan: dict[str, Any], response_id: Any, accepted_at: Any
) -> dict[str, Any]:
    """A revision of the plan as its history keeps it: which answer proposed it and when it was
    accepted, the question that answer was to, and what it changed."""
    question = next(
        (
            turn.get("question")
            for turn in conversation["turns"]
            if turn.get("answer", {}).get("response_id") == response_id
        ),
        None,
    )
    return {
        "revision": plan.get("revision"),
        "response_id": response_id,
        "accepted_at": accepted_at,
        "question": question,
        "changes": plan.get("changes", []),
    }


def _title(question: str) -> str:
    """The first question, on one line, cut at a word to about `_TITLE_CHARS`."""
    text = " ".join(question.split())
    if len(text) <= _TITLE_CHARS:
        return text
    cut = text[:_TITLE_CHARS].rsplit(" ", 1)[0]
    return f"{cut or text[:_TITLE_CHARS]}…"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
