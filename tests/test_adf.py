from app.adf import adf_to_text, render, text_to_adf


def test_single_paragraph():
    doc = text_to_adf("Hello world")
    assert doc == {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}],
    }


def test_blank_line_splits_paragraphs_and_newline_is_hard_break():
    doc = text_to_adf("one\ntwo\n\nthree")
    assert len(doc["content"]) == 2
    first = doc["content"][0]["content"]
    assert [block["type"] for block in first] == ["text", "hardBreak", "text"]
    assert doc["content"][1]["content"] == [{"type": "text", "text": "three"}]


def test_empty_text_still_produces_a_valid_document():
    assert text_to_adf("") == {"type": "doc", "version": 1, "content": [{"type": "paragraph"}]}


def test_round_trip_preserves_the_text():
    original = "Deploy failed\n\nSee the logs"
    assert adf_to_text(text_to_adf(original)).strip() == original


def test_adf_to_text_handles_lists_mentions_and_code():
    doc = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Blocked by:"}]},
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "infra"}]}],
                    }
                ],
            },
            {"type": "paragraph", "content": [{"type": "mention", "attrs": {"text": "@Sam"}}]},
            {"type": "codeBlock", "content": [{"type": "text", "text": "npm test"}]},
        ],
    }
    text = adf_to_text(doc)
    assert "Blocked by:" in text
    assert "- infra" in text
    assert "@Sam" in text
    assert "```\nnpm test\n```" in text


def test_unknown_node_types_still_render_their_children():
    doc = {"type": "panel", "content": [{"type": "text", "text": "keep me"}]}
    assert adf_to_text(doc) == "keep me"


def test_render_truncates_and_collapses_blank_lines():
    assert render(None) == ""
    long_doc = text_to_adf("x" * 300)
    out = render(long_doc, limit=50)
    assert out.endswith("chars total]")
    assert len(out) < 120
    assert "\n\n\n" not in render(text_to_adf("a\n\n\n\nb"))


def test_plain_string_bodies_are_accepted():
    assert adf_to_text("already plain") == "already plain"
