"""Tests for the chat renderer, against hand-written `ResponseArtifact.chat` samples — no
orchestrator, per implementation.md Stage 12."""

from pioneer.chat_presentation.renderer import render_message, render_page


def test_renders_text_as_a_paragraph() -> None:
    result = render_message("Build two more smelters.")

    assert '<div class="message message-assistant">' in result
    assert "<p>Build two more smelters.</p>" in result


def test_blank_lines_split_into_separate_paragraphs() -> None:
    result = render_message("First, extend the smelters.\n\nThen add an assembler.")

    assert "<p>First, extend the smelters.</p>" in result
    assert "<p>Then add an assembler.</p>" in result


def test_escapes_html_in_the_message() -> None:
    result = render_message("Use <Constructor> & don't forget power")

    assert "<Constructor>" not in result
    assert "&lt;Constructor&gt;" in result
    assert "&amp;" in result


def test_none_chat_renders_a_placeholder_instead_of_disappearing() -> None:
    result = render_message(None)

    assert "message-assistant" in result
    assert "(no response)" in result


def test_user_role_gets_the_user_css_class() -> None:
    result = render_message("How do I make Iron Plates?", role="user")

    assert "message-user" in result


def test_render_page_includes_every_message_in_order() -> None:
    page = render_page(
        [
            ("user", "How do I make Iron Plates?"),
            ("assistant", "Smelt Iron Ore into Iron Ingots, then constructor them into plates."),
        ]
    )

    assert page.index("How do I make Iron Plates?") < page.index("Smelt Iron Ore")
    assert "<!doctype html>" in page.lower()
