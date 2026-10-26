"""Feedback capture, keyed to a `ResponseArtifact.response_id`: feedback is about a particular
answer, not a whole conversation. `FeedbackStore` keeps it in memory; `JsonlFeedbackStore` also
appends it to a JSON-lines file it reads back on start. `ResponseLog` records what was asked,
answered and scored, per response id.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pioneer.common import utc_timestamp
from pioneer.contracts import Feedback, ResponseArtifact
from pioneer.verification_feedback.scoring import ResponseScore


class FeedbackStore:
    def __init__(self) -> None:
        self._by_response_id: dict[str, Feedback] = {}

    def record(self, response_id: str, feedback: Feedback) -> None:
        """Overwrites any feedback already recorded for `response_id` — a player revising their
        thumbs up/down is expected to replace the old verdict, not accumulate alongside it."""
        self._by_response_id[response_id] = feedback

    def get(self, response_id: str) -> Feedback | None:
        return self._by_response_id.get(response_id)


class JsonlFeedbackStore(FeedbackStore):
    """A `FeedbackStore` backed by an append-only JSON-lines file: the latest line per response id
    wins, exactly as the latest `record` does in memory."""

    def __init__(self, path: Path | str) -> None:
        super().__init__()
        self._path = Path(path)
        for line in _read_jsonl(self._path):
            try:
                super().record(line["response_id"], Feedback(**line["feedback"]))
            except (KeyError, TypeError):
                continue  # a line this version doesn't understand -- keep the rest

    def record(self, response_id: str, feedback: Feedback) -> None:
        super().record(response_id, feedback)
        _append_jsonl(
            self._path,
            {
                "response_id": response_id,
                "recorded_at": utc_timestamp(),
                "feedback": asdict(feedback),
            },
        )


class ResponseLog:
    """Appends one JSON line per answered question: the question, the answer, what its graph and
    map channels held, and its verification scores — joinable with the feedback file by response
    id."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def response_ids(self) -> frozenset[str]:
        """Every answer the log holds — what feedback can still be given on after a restart."""
        return frozenset(
            str(record["response_id"])
            for record in _read_jsonl(self._path)
            if record.get("response_id") is not None
        )

    def append(self, artifact: ResponseArtifact, score: ResponseScore | None) -> None:
        locations = artifact.map_locations
        _append_jsonl(
            self._path,
            {
                "response_id": artifact.response_id,
                "recorded_at": utc_timestamp(),
                "question": artifact.question,
                "chat": artifact.chat,
                "graph_nodes": len(artifact.graph.nodes) if artifact.graph is not None else None,
                "map_locations": (
                    [location.resource_node_id for location in locations]
                    if locations is not None
                    else None
                ),
                "score": asdict(score) if score is not None else None,
            },
        )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn or hand-mangled line -- keep the rest
        if isinstance(record, dict):
            records.append(record)
    return records


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
