"""Conversion helpers between plain text and Atlassian Document Format (ADF).

Jira Cloud's REST API v3 stores rich text (descriptions, comments, worklog
comments) as ADF documents rather than wiki markup, so every write needs
`text_to_adf` and every read needs `adf_to_text`.
"""

from __future__ import annotations

from typing import Any

_LIST_TYPES = {"bulletList", "orderedList"}


def text_to_adf(text: str) -> dict[str, Any]:
    """Wrap plain text in a minimal ADF document.

    Blank lines separate paragraphs; single newlines become hard breaks so the
    text renders in Jira the way the user typed it.
    """
    doc: dict[str, Any] = {"type": "doc", "version": 1, "content": []}
    if not text:
        doc["content"].append({"type": "paragraph"})
        return doc

    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = block.split("\n")
        content: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            if index:
                content.append({"type": "hardBreak"})
            if line:
                content.append({"type": "text", "text": line})
        paragraph: dict[str, Any] = {"type": "paragraph"}
        if content:
            paragraph["content"] = content
        doc["content"].append(paragraph)
    return doc


def adf_to_text(node: Any, _depth: int = 0) -> str:
    """Flatten an ADF document (or fragment) into readable plain text.

    Unknown node types degrade gracefully: their children are still rendered.
    Accepts the plain strings that some endpoints return instead of ADF.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(child, _depth) for child in node)
    if not isinstance(node, dict):
        return str(node)

    node_type = node.get("type")
    children = node.get("content", [])

    if node_type == "text":
        return node.get("text", "")
    if node_type == "hardBreak":
        return "\n"
    if node_type in {"paragraph", "heading"}:
        return adf_to_text(children, _depth) + "\n\n"
    if node_type == "listItem":
        body = adf_to_text(children, _depth + 1).strip()
        indent = "  " * _depth
        return f"{indent}- {body}\n"
    if node_type in _LIST_TYPES:
        return adf_to_text(children, _depth) + "\n"
    if node_type == "codeBlock":
        return "```\n" + adf_to_text(children, _depth).strip() + "\n```\n\n"
    if node_type == "rule":
        return "---\n\n"
    if node_type == "mention":
        return "@" + node.get("attrs", {}).get("text", "").lstrip("@")
    if node_type == "emoji":
        attrs = node.get("attrs", {})
        return attrs.get("text") or attrs.get("shortName", "")
    if node_type == "inlineCard":
        return node.get("attrs", {}).get("url", "")
    if node_type == "mediaSingle" or node_type == "mediaGroup":
        return "[attachment]\n\n"

    return adf_to_text(children, _depth)


def render(node: Any, limit: int | None = 2000) -> str:
    """`adf_to_text` with whitespace tidied and an optional length cap."""
    text = adf_to_text(node).strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    if limit is not None and len(text) > limit:
        text = text[:limit].rstrip() + f"… [truncated, {len(text)} chars total]"
    return text
