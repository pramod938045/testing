"""Slack text helpers — no slack_bolt import, so these stay unit-testable.

Slack's mrkdwn is not Markdown: bold is *one* asterisk, links are <url|text>,
and there are no tables or headings.
"""

from __future__ import annotations

import re

MENTION_RE = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*)$")
BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
TABLE_SEPARATOR_RE = re.compile(r"^:?-{2,}:?$")


def clean_mention_text(text: str) -> str:
    """Strip `<@U123>` user mentions and tidy the whitespace around them."""
    return MENTION_RE.sub(" ", text or "").strip()


def to_mrkdwn(text: str) -> str:
    """Convert the agent's Markdown reply into Slack mrkdwn.

    Tables become bullet lines — Slack cannot render a table, and a raw pipe
    grid is unreadable on mobile.
    """
    out: list[str] = []
    table_header: list[str] | None = None

    for raw in (text or "").split("\n"):
        row = TABLE_ROW_RE.match(raw)
        if row:
            cells = [cell.strip() for cell in row.group(1).split("|")]
            if all(TABLE_SEPARATOR_RE.match(cell) for cell in cells if cell):
                continue  # |---|---| separator
            if table_header is None:
                table_header = cells
                continue
            pairs = [
                f"{label}: {value}" if label else value
                for label, value in zip(table_header + [""] * len(cells), cells)
                if value
            ]
            out.append("• " + "  |  ".join(pairs))
            continue
        table_header = None

        heading = HEADING_RE.match(raw)
        if heading:
            out.append(f"*{heading.group(1).strip()}*")
            continue

        bullet = BULLET_RE.match(raw)
        if bullet:
            out.append(f"{bullet.group(1)}• {bullet.group(2)}")
            continue

        out.append(raw)

    body = "\n".join(out)
    body = LINK_RE.sub(r"<\2|\1>", body)
    body = BOLD_RE.sub(r"*\1*", body)
    return body.strip()


def tool_context(tool_calls) -> str:
    """One-line audit trail of the Jira calls made, for a Slack context block."""
    if not tool_calls:
        return ""
    names = [f"{call.name}{'' if call.ok else ' (failed)'}" for call in tool_calls]
    return "Jira: " + ", ".join(names)


def truncate(text: str, limit: int = 2900) -> str:
    """Keep a message under Slack's per-block text limit."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0].rstrip() + "\n… (truncated)"
