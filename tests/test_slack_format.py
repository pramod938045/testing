from dataclasses import dataclass

from app.slack_format import clean_mention_text, to_mrkdwn, tool_context, truncate


@dataclass
class Call:
    name: str
    ok: bool = True


def test_clean_mention_text_strips_the_bot_mention():
    assert clean_mention_text("<@U123ABC> what's on my plate?") == "what's on my plate?"
    assert clean_mention_text("<@U123ABC|bot> hi") == "hi"
    assert clean_mention_text("no mention here") == "no mention here"
    assert clean_mention_text("") == ""


def test_bold_and_links_become_slack_syntax():
    out = to_mrkdwn("**ABC-1** — see [the issue](https://x.atlassian.net/browse/ABC-1)")
    assert "*ABC-1*" in out
    assert "<https://x.atlassian.net/browse/ABC-1|the issue>" in out
    assert "**" not in out


def test_bullets_and_headings():
    out = to_mrkdwn("## Sprint 12\n- ABC-1 done\n- ABC-2 open")
    assert out.startswith("*Sprint 12*")
    assert "• ABC-1 done" in out
    assert "-" not in out.split("\n")[1][:2]


def test_nested_bullet_indentation_is_kept():
    assert "  • child" in to_mrkdwn("- parent\n  - child")


def test_tables_become_bullet_lines_with_labels():
    table = "| Key | Status |\n| --- | --- |\n| ABC-1 | Done |\n| ABC-2 | To Do |"
    out = to_mrkdwn(table)

    assert "|" not in out.replace("  |  ", "")  # only the separator we insert
    assert "• Key: ABC-1  |  Status: Done" in out
    assert "• Key: ABC-2  |  Status: To Do" in out


def test_text_after_a_table_is_not_treated_as_table_rows():
    out = to_mrkdwn("| A |\n| --- |\n| 1 |\n\nDone.")
    assert out.endswith("Done.")


def test_tool_context_lists_calls_and_flags_failures():
    assert tool_context([]) == ""
    assert tool_context([Call("search_issues")]) == "Jira: search_issues"
    assert "get_issue (failed)" in tool_context([Call("search_issues"), Call("get_issue", ok=False)])


def test_truncate_respects_the_block_limit_and_breaks_on_a_line():
    text = "\n".join(f"line {i}" for i in range(500))
    out = truncate(text, limit=100)

    assert len(out) < 130
    assert out.endswith("… (truncated)")
    assert truncate("short", limit=100) == "short"
