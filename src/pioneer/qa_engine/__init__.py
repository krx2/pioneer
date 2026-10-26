"""Q&A Engine: answers free-form game-mechanics questions by retrieval-augmented generation."""

from pioneer.qa_engine.corpus import build_corpus
from pioneer.qa_engine.engine import (
    ChatCompletion,
    Citation,
    LLMUnavailable,
    NoRelevantPassages,
    QAAnswer,
    TransportError,
    answer_question,
)
from pioneer.qa_engine.retrieval import Passage, ScoredPassage, retrieve, significant_words

__all__ = [
    "ChatCompletion",
    "Citation",
    "LLMUnavailable",
    "NoRelevantPassages",
    "Passage",
    "QAAnswer",
    "ScoredPassage",
    "TransportError",
    "answer_question",
    "build_corpus",
    "retrieve",
    "significant_words",
]
