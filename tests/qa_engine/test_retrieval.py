"""Tests for retrieve, against a small hand-written corpus (a few recipe-style passages and a few
wiki-style paragraphs), per implementation.md Stage 11 — no dependency on the real Knowledge Base.
"""

from pioneer.qa_engine.retrieval import Passage, retrieve

_CORPUS = (
    Passage(
        passage_id="recipe_iron_ingot",
        text="The Smelter recipe for Iron Ingot converts 1 Iron Ore into 1 Iron Ingot.",
        source="Recipe: Iron Ingot",
    ),
    Passage(
        passage_id="recipe_reinforced_iron_plate",
        text="Reinforced Iron Plate is made in an Assembler from Iron Plate and Screw.",
        source="Recipe: Reinforced Iron Plate",
    ),
    Passage(
        passage_id="wiki_power_shards",
        text="Power Shards, found in Power Slugs, can overclock a machine above 100% clock speed.",
        source="Wiki: Power Shards",
    ),
    Passage(
        passage_id="wiki_conveyor_belts",
        text="Conveyor belts move items between machines; higher tiers carry more items per "
        "minute.",
        source="Wiki: Conveyor Belts",
    ),
)


def test_ranks_the_most_relevant_passage_first() -> None:
    result = retrieve("How do I make Iron Ingot?", _CORPUS)

    assert result[0].passage.passage_id == "recipe_iron_ingot"


def test_drops_passages_with_no_term_overlap() -> None:
    result = retrieve("How do I make Iron Ingot?", _CORPUS)

    ids = {scored.passage.passage_id for scored in result}
    assert "wiki_power_shards" not in ids
    assert "wiki_conveyor_belts" not in ids


def test_unrelated_question_returns_nothing() -> None:
    result = retrieve("What is the capital of France?", _CORPUS)

    assert result == ()


def test_empty_corpus_returns_nothing() -> None:
    result = retrieve("How do I make Iron Ingot?", ())

    assert result == ()


def test_top_k_limits_the_number_of_results() -> None:
    result = retrieve("machine belt items minute clock speed", _CORPUS, top_k=1)

    assert len(result) == 1


def test_scores_are_sorted_descending() -> None:
    result = retrieve("Reinforced Iron Plate Assembler Screw Iron Plate", _CORPUS)

    scores = [scored.score for scored in result]
    assert scores == sorted(scores, reverse=True)
