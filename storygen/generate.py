"""Ask Claude to suggest Stories for an Epic or Change Request.

Nothing here touches Jira. It takes the context read by `Jira.get_context`
and returns suggestions for a human to review.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

SYSTEM = """You are an experienced business analyst who splits Jira Epics and Change \
Requests into implementable Stories for a delivery team.

Rules:
- Work ONLY from the issue content given to you. Never invent scope, systems, \
integrations or acceptance criteria that the source does not support.
- Each Story must be independently deliverable and testable, and small enough for one \
team to finish in a sprint.
- Cover the whole source: every requirement in the Details should land in some Story.
- Do not create Stories for work already covered by an existing subtask; those are listed.
- Write the user story as "As a <role>, I would like to <goal>, so that <benefit>", \
matching the phrasing already used in the source issue.
- Acceptance criteria must be concrete and checkable — a tester should be able to pass or \
fail each one. Prefer the source's own terms (event names, field names, limits).
- Where the source is genuinely unclear or silent on something a developer would need, \
do NOT guess: record it in open_questions.
- Aim for 3 to 8 Stories. Fewer is fine if the source is small."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Short Jira title, under 100 characters.",
                    },
                    "user_story": {
                        "type": "string",
                        "description": "As a <role>, I would like to <goal>, so that <benefit>.",
                    },
                    "details": {
                        "type": "string",
                        "description": "What the work involves, grounded in the source issue.",
                    },
                    "acceptance_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Concrete pass/fail checks.",
                    },
                },
                "required": ["summary", "user_story", "details", "acceptance_criteria"],
                "additionalProperties": False,
            },
        },
        "open_questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Things the source issue does not answer. Empty if none.",
        },
    },
    "required": ["stories", "open_questions"],
    "additionalProperties": False,
}


def build_prompt(context: dict[str, Any]) -> str:
    """Render the Jira context as the text the model reads."""
    parts = [
        f"Issue: {context['key']} ({context['type']}, status {context['status']})",
        f"Project: {context['project']}",
        f"Summary: {context['summary']}",
    ]
    if context.get("priority"):
        parts.append(f"Priority: {context['priority']}")
    if context.get("labels"):
        parts.append("Labels: " + ", ".join(context["labels"]))

    parts.append("\n--- Description ---\n" + (context.get("description") or "(no description)"))

    if context.get("links"):
        parts.append("\n--- Linked issues (context only, do not write stories for these) ---")
        for link in context["links"]:
            parts.append(f"{link['relation']} {link['key']} [{link['status']}] {link['summary']}")

    if context.get("subtasks"):
        parts.append("\n--- Existing subtasks (already covered, do not duplicate) ---")
        for sub in context["subtasks"]:
            parts.append(f"{sub['key']} {sub['summary']}")

    parts.append(
        f"\nSuggest the Stories needed to deliver {context['key']}."
    )
    return "\n".join(parts)


def generate_stories(
    context: dict[str, Any],
    api_key: str,
    model: str = "claude-opus-5",
    client: Any | None = None,
) -> dict[str, Any]:
    """Return {"stories": [...], "open_questions": [...]}.

    `client` is injectable so tests can run without an API key or network.
    """
    client = client or anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": build_prompt(context)}],
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )

    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError("The AI declined to answer this request.")

    text = next((block.text for block in response.content if block.type == "text"), "")
    if not text.strip():
        raise RuntimeError("The AI returned an empty response. Try again.")
    return json.loads(text)
