from app.adf import adf_to_text, render


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
    long_doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "x" * 300}]}]}
    out = render(long_doc, limit=50)
    assert out.endswith("chars total]")
    assert len(out) < 120


def test_plain_string_bodies_are_accepted():
    assert adf_to_text("already plain") == "already plain"
