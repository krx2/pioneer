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


def test_bold_and_italic_are_rendered() -> None:
    result = render_message("Build **two** more *smelters*.")

    assert "<strong>two</strong>" in result
    assert "<em>smelters</em>" in result


def test_inline_code_is_escaped_and_not_reinterpreted_as_markdown() -> None:
    result = render_message("Use `*Constructor*` for this.")

    assert "<code>*Constructor*</code>" in result
    assert "<em>" not in result


def test_unordered_list_renders_as_ul() -> None:
    result = render_message("- Smelter\n- Constructor\n- Assembler")

    assert "<ul><li>Smelter</li><li>Constructor</li><li>Assembler</li></ul>" in result


def test_ordered_list_renders_as_ol() -> None:
    result = render_message("1. Mine ore\n2. Smelt ingots")

    assert "<ol><li>Mine ore</li><li>Smelt ingots</li></ol>" in result


def test_heading_renders_as_heading_tag() -> None:
    result = render_message("## Production Plan")

    assert "<h2>Production Plan</h2>" in result


def test_heading_directly_followed_by_a_list_needs_no_blank_line() -> None:
    result = render_message("### Summary\n- Assemblers: 1\n- Smelters: 2")

    assert "<h3>Summary</h3>" in result
    assert "<ul><li>Assemblers: 1</li><li>Smelters: 2</li></ul>" in result


def test_numbered_item_with_unindented_sub_bullets_nests_them() -> None:
    result = render_message(
        "1. Desc_Computer_C:\n- Amount: 12.5/min\n- Suggestion: use it\n2. Desc_Wire_C:\n"
        "- Amount: 160/min"
    )

    assert result.count("<ol>") == 1
    assert "<li>Desc_Computer_C:<ul><li>Amount: 12.5/min</li>" in result
    assert "<li>Desc_Wire_C:<ul><li>Amount: 160/min</li></ul></li>" in result


def test_fenced_code_block_preserves_internal_blank_lines() -> None:
    result = render_message("```\nline one\n\nline two\n```")

    assert "<pre><code>line one\n\nline two</code></pre>" in result


def test_render_page_includes_every_message_in_order() -> None:
    page = render_page(
        [
            ("user", "How do I make Iron Plates?"),
            ("assistant", "Smelt Iron Ore into Iron Ingots, then constructor them into plates."),
        ]
    )

    assert page.index("How do I make Iron Plates?") < page.index("Smelt Iron Ore")
    assert "<!doctype html>" in page.lower()
