"""Tests for the conversation store, on a temporary folder."""

import json

from pioneer.web.conversations import ConversationStore

_ID = "0123abcd-0000-4000-8000-000000000000"


def test_a_long_first_question_is_cut_at_a_word_for_the_title(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    question = "I want to produce ten per minute of Reinforced Iron Plate\nstarting from ore nodes"

    conversation_id = store.save_turn(None, 0, question, {"chat": "ok"})

    title = store.get(conversation_id)["title"]
    assert title == "I want to produce ten per minute of Reinforced Iron Plate…"


def test_ids_that_are_not_the_stores_own_never_become_paths(tmp_path) -> None:
    store = ConversationStore(tmp_path / "conversations")
    (tmp_path / "secret.json").write_text(
        json.dumps({"id": "x", "title": "t", "updated_at": "", "turns": []}), encoding="utf-8"
    )

    assert store.get("../secret") is None
    assert store.delete("../secret") is False
    assert store.page("../../secret", "graph") is None
    store.save_page("../evil", "graph", "<p>")
    assert not (tmp_path / "evil-graph.html").exists()
    # An unknown or malformed id starts a conversation of the store's own.
    assert store.save_turn("../secret", 0, "q", {}) != "../secret"
    assert store.save_turn(_ID, 0, "q", {}) == _ID


def test_unreadable_files_are_left_out(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    kept = store.save_turn(None, 0, "q", {})
    (tmp_path / f"{_ID}.json").write_text("{torn", encoding="utf-8")

    assert [c["id"] for c in store.list()] == [kept]
    assert store.get(_ID) is None
