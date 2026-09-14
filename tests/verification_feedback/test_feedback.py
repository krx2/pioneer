"""Tests for FeedbackStore — feedback keyed to a response id, not a session
(implementation.md Stage 15)."""

from pioneer.contracts import Feedback
from pioneer.verification_feedback.feedback import FeedbackStore


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
