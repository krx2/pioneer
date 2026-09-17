"""Tests for FeedbackStore — feedback keyed to a response id, not a session
(implementation.md Stage 15)."""

import json

from pioneer.contracts import Feedback, ResponseArtifact
from pioneer.verification_feedback.feedback import (
    FeedbackStore,
    JsonlFeedbackStore,
    ResponseLog,
)
from pioneer.verification_feedback.scoring import ResponseScore


def test_recorded_feedback_is_retrievable_by_response_id() -> None:
    store = FeedbackStore()
    feedback = Feedback(thumbs_up=True, applied_plan=True)

    store.record("response_1", feedback)

    assert store.get("response_1") == feedback


def test_unknown_response_id_returns_none() -> None:
    store = FeedbackStore()

    assert store.get("never_recorded") is None


def test_recording_again_overwrites_the_previous_feedback() -> None:
    store = FeedbackStore()
    store.record("response_1", Feedback(thumbs_up=True))

    store.record("response_1", Feedback(thumbs_up=False))

    assert store.get("response_1") == Feedback(thumbs_up=False)


def test_feedback_for_different_responses_is_kept_separate() -> None:
    store = FeedbackStore()
    store.record("response_1", Feedback(thumbs_up=True))
    store.record("response_2", Feedback(thumbs_up=False))

    assert store.get("response_1") == Feedback(thumbs_up=True)
    assert store.get("response_2") == Feedback(thumbs_up=False)


def test_the_jsonl_store_keeps_feedback_across_a_restart(tmp_path) -> None:
    path = tmp_path / "data" / "feedback.jsonl"
    JsonlFeedbackStore(path).record("r1", Feedback(thumbs_up=True))

    JsonlFeedbackStore(path).record("r1", Feedback(thumbs_up=False, qualitative_score=2))

    assert JsonlFeedbackStore(path).get("r1") == Feedback(thumbs_up=False, qualitative_score=2)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_the_jsonl_store_skips_lines_it_cannot_read(tmp_path) -> None:
    path = tmp_path / "feedback.jsonl"
    path.write_text(
        'not json\n{"response_id": "r1", "feedback": {"mood": "?"}}\n', encoding="utf-8"
    )

    store = JsonlFeedbackStore(path)
    store.record("r2", Feedback(applied_plan=True))

    assert store.get("r1") is None
    assert JsonlFeedbackStore(path).get("r2") == Feedback(applied_plan=True)


def test_the_response_log_writes_one_line_per_answer(tmp_path) -> None:
    path = tmp_path / "responses.jsonl"
    log = ResponseLog(path)

    log.append(ResponseArtifact(response_id="r1", chat="hi", question="hello?"), None)
    log.append(ResponseArtifact(response_id="r2", map_locations=()), ResponseScore(map=()))

    first, second = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    assert (first["response_id"], first["question"], first["chat"]) == ("r1", "hello?", "hi")
    assert first["score"] is None
    assert second["map_locations"] == []
    assert second["score"] == {"chat": None, "graph": None, "map": []}
