"""Turning Jira context into Markdown.

Shared by the CLI and the MCP server so both produce identical documents.
"""

from __future__ import annotations

from typing import Any


def issue_block(issue: dict[str, Any]) -> list[str]:
    """One issue: metadata, description, then subtasks and links if any."""
    lines = [
        f"## {issue['key']} — {issue['summary']}",
        "",
        f"Type: {issue['type']} | Status: {issue['status']} | "
        f"Priority: {issue.get('priority') or '—'}",
    ]
    if issue.get("labels"):
        lines.append("Labels: " + ", ".join(issue["labels"]))
    lines.append(f"URL: {issue['url']}")
    lines += ["", "### Description", "", issue["description"] or "_(empty)_"]

    if issue.get("subtasks"):
        lines += ["", "### Subtasks", ""]
        lines += [f"- {sub['key']} {sub['summary']}" for sub in issue["subtasks"]]
    if issue.get("links"):
        lines += ["", "### Linked issues", ""]
        lines += [
            f"- {link['relation']} {link['key']} [{link['status']}] {link['summary']}"
            for link in issue["links"]
        ]
    return lines + [""]


def epic_document(
    parent: dict[str, Any],
    children: list[dict[str, Any]],
    jql: str,
    full: list[dict[str, Any]] | None = None,
) -> str:
    """The epic, a table of the stories under it, then each story in full.

    `full` is the fetched context for each child, or None/empty to list the
    children without their descriptions.
    """
    lines = [f"# {parent['key']} — {parent['summary']}", ""]
    lines += issue_block(parent)

    if not children:
        return "\n".join(
            lines
            + [
                "## Stories under this epic",
                "",
                "_None found._ The epic has no children, or this site links them by a "
                "field other than Epic Link, parent or Parent Link.",
                "",
            ]
        )

    lines += [f"## Stories under this epic ({len(children)})", "", f"Found with: `{jql}`", ""]
    lines += ["| Key | Type | Status | Summary |", "| --- | --- | --- | --- |"]
    for child in children:
        fields = child.get("fields", {})
        lines.append(
            f"| {child['key']} "
            f"| {(fields.get('issuetype') or {}).get('name', '?')} "
            f"| {(fields.get('status') or {}).get('name', '?')} "
            f"| {fields.get('summary', '')} |"
        )
    lines.append("")

    if full:
        lines += ["---", "", "# Stories in full", ""]
        for story in full:
            lines += issue_block(story)

    return "\n".join(lines)
