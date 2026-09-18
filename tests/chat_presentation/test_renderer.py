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


_ICONS = {
    "Iron Plate": "/icons/Desc_IronPlate_C.png",
    "Coal": "/icons/Desc_Coal_C.png",
    "Coal-Powered Generator": "/icons/Build_GeneratorCoal_C.png",
    "Miner Mk.1": "/icons/Build_MinerMk1_C.png",
}


def test_a_named_item_gets_its_icon_in_front_of_it() -> None:
    result = render_message("Build **3 Iron Plates** a minute.", icons=_ICONS)

    assert (
        '<strong>3 <span class="ref"><img class="icon" alt="" '
        'src="/icons/Desc_IronPlate_C.png">Iron Plates</span></strong>'
    ) in result


def test_the_longest_name_wins_and_part_of_a_word_is_not_a_name() -> None:
    result = render_message("A Coal-Powered Generator burns Coal, not Coalition.", icons=_ICONS)

    assert result.count("Build_GeneratorCoal_C.png") == 1
    assert result.count("Desc_Coal_C.png") == 1
    assert "Coalition" in result and "Coalition</span>" not in result


def test_names_with_regex_characters_match_only_themselves() -> None:
    result = render_message("Place a Miner Mk.1, not a Miner Mk11.", icons=_ICONS)

    assert result.count("Build_MinerMk1_C.png") == 1


def test_code_keeps_its_names_plain() -> None:
    result = render_message("Try `Iron Plate` in the search.\n\n```\nIron Plate\n```", icons=_ICONS)

    assert "Desc_IronPlate_C.png" not in result


def test_without_icons_nothing_changes() -> None:
    assert render_message("Iron Plate") == render_message("Iron Plate", icons={})
    assert "<img" not in render_message("Iron Plate")


def test_a_markdown_table_becomes_a_table() -> None:
    result = render_message(
        "Needs:\n"
        "| Component | Per minute | Note |\n"
        "|:----------|-----------:|:----:|\n"
        "| **Motor** | 8 | `new` |\n"
        "| Rubber | 60 |\n"
        r"| a \| b | 1 | x | extra |"
        "\n"
        "\n"
        "Done."
    )

    assert "<p>Needs:</p>" in result
    assert (
        '<div class="table"><table><thead><tr><th style="text-align:left">Component</th>'
        '<th style="text-align:right">Per minute</th><th style="text-align:center">Note</th>'
        "</tr></thead><tbody>"
    ) in result
    assert '<td style="text-align:left"><strong>Motor</strong></td>' in result
    assert '<td style="text-align:center"><code>new</code></td>' in result
    assert '<td style="text-align:center"></td></tr>' in result  # the short row is padded
    assert '<td style="text-align:left">a | b</td>' in result
    assert "extra" not in result  # the long row is cut to the header's columns
    assert result.endswith("</tbody></table></div><p>Done.</p></div>")


def test_a_table_needs_its_delimiter_row() -> None:
    result = render_message("a | b\nc | d")

    assert "<table>" not in result
    assert "<p>a | b<br>c | d</p>" in result


def test_a_table_without_outer_pipes_and_with_icons() -> None:
    result = render_message("Item | Rate\n--- | ---\nIron Plate | 30", icons=_ICONS)

    assert "<th>Item</th><th>Rate</th>" in result
    assert "Desc_IronPlate_C.png" in result
