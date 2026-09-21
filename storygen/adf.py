"""Turn Jira Cloud's rich-text format (ADF) into plain text.

Descriptions come back as nested JSON, not a string. Unknown node types fall
through to their children, so an unusual block loses its formatting but never
its text.
"""

from __future__ import annotations

from typing import Any


def adf_to_text(node: Any, depth: int = 0) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(child, depth) for child in node)
    if not isinstance(node, dict):
        return str(node)

    kind = node.get("type")
    kids = node.get("content", [])

    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("paragraph", "heading"):
        return adf_to_text(kids, depth) + "\n\n"
    if kind == "listItem":
        return "  " * depth + "- " + adf_to_text(kids, depth + 1).strip() + "\n"
    if kind in ("bulletList", "orderedList"):
        return adf_to_text(kids, depth) + "\n"
    if kind == "codeBlock":
        return "```\n" + adf_to_text(kids, depth).strip() + "\n```\n\n"
    if kind == "tableCell" or kind == "tableHeader":
        return adf_to_text(kids, depth).strip() + " | "
    if kind == "tableRow":
        return adf_to_text(kids, depth).rstrip(" |") + "\n"
    if kind == "table":
        return adf_to_text(kids, depth) + "\n"
    if kind == "rule":
        return "---\n\n"
    if kind == "mention":
        return "@" + node.get("attrs", {}).get("text", "").lstrip("@")
    if kind == "inlineCard":
        return node.get("attrs", {}).get("url", "")
    if kind in ("mediaSingle", "mediaGroup", "media"):
        return "[attachment]\n"

    return adf_to_text(kids, depth)


def to_text(node: Any, limit: int | None = None) -> str:
    """Flattened text with blank lines collapsed and an optional length cap."""
    text = adf_to_text(node).strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + f"\n… [cut, {len(text)} characters total]"
    return text
