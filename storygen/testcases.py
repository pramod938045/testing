"""Ask Claude to write manual test cases for a Jira issue.

Nothing here touches Jira. It takes the context read by `Jira.get_context`
and returns test cases for a human to review. Publishing them back to Jira
is a separate, explicitly confirmed step — see `storygen.publish`.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import anthropic

SYSTEM = """You are an experienced QA engineer writing manual test cases for a \
delivery team, from a Jira issue.

Rules:
- Work ONLY from the issue content given to you. Never invent scope, systems, \
integrations, field names or limits that the source does not support.
- Cover the whole source: every requirement and acceptance criterion should be \
exercised by at least one test case.
- Write for a human tester who does not know the feature. Each step is one \
action, phrased as an instruction, with the expected result of that step.
- Include negative and boundary cases wherever the source states a rule, a \
limit, a required field or a validation — a rule with no negative test is a \
gap. Do not invent rules just to test them.
- Use the source's own terms: event names, field names, status values, limits, \
error messages. Do not paraphrase an identifier.
- Every test case names the requirement it covers in `covers`, quoting or \
closely paraphrasing the acceptance criterion or line of the description it \
came from. This is the traceability record.
- Preconditions state what must already be true — data set up, user role, \
system state. Leave the list empty if the test genuinely needs nothing.
- Where the source is unclear or silent on something a tester would need \
(expected error text, a limit's exact value, which role can do this), do NOT \
guess: record it in open_questions.
- Where something in the source cannot be verified manually (a performance \
target, an async job's internal behaviour, an infrastructure concern), record \
it in not_manually_testable rather than writing a test case that pretends.
- Aim for 5 to 15 test cases. Fewer is fine if the source is small. Do not pad \
with near-duplicates."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "test_cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Sequential identifier: TC-01, TC-02, ...",
                    },
                    "title": {
                        "type": "string",
                        "description": "What this verifies, under 100 characters.",
                    },
                    "type": {
                        "type": "string",
                        "enum": [
                            "Functional",
                            "Negative",
                            "Boundary",
                            "Integration",
                            "Regression",
                            "Security",
                        ],
                    },
                    "priority": {"type": "string", "enum": ["High", "Medium", "Low"]},
                    "covers": {
                        "type": "string",
                        "description": "The requirement or acceptance criterion this traces to.",
                    },
                    "preconditions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "What must be true before the first step. May be empty.",
                    },
                    "test_data": {
                        "type": "string",
                        "description": "Concrete data to use, or an empty string if none is needed.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "description": "One instruction to the tester.",
                                },
                                "expected": {
                                    "type": "string",
                                    "description": "What the tester should observe after this step.",
                                },
                            },
                            "required": ["action", "expected"],
                            "additionalProperties": False,
                        },
                        "minItems": 1,
                    },
                    "expected_result": {
                        "type": "string",
                        "description": "The overall pass condition for the whole case.",
                    },
                },
                "required": [
                    "id",
                    "title",
                    "type",
                    "priority",
                    "covers",
                    "preconditions",
                    "test_data",
                    "steps",
                    "expected_result",
                ],
                "additionalProperties": False,
            },
        },
        "open_questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Things the source does not answer that a tester needs. Empty if none.",
        },
        "not_manually_testable": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Requirements that need automation or tooling, not a manual test.",
        },
    },
    "required": ["test_cases", "open_questions", "not_manually_testable"],
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
        parts.append("\n--- Linked issues (context only, do not test these) ---")
        for link in context["links"]:
            parts.append(f"{link['relation']} {link['key']} [{link['status']}] {link['summary']}")

    if context.get("subtasks"):
        parts.append("\n--- Existing subtasks (scope context) ---")
        for sub in context["subtasks"]:
            parts.append(f"{sub['key']} {sub['summary']}")

    parts.append(f"\nWrite the manual test cases needed to verify {context['key']}.")
    return "\n".join(parts)


def generate_test_cases(
    context: dict[str, Any],
    api_key: str,
    model: str = "claude-opus-5",
    client: Any | None = None,
) -> dict[str, Any]:
    """Return {"test_cases": [...], "open_questions": [...], "not_manually_testable": [...]}.

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


# --------------------------------------------------------------- rendering


def render_markdown(key: str, result: dict[str, Any], url: str = "") -> str:
    """The review text: what the AI suggests, for a human to judge."""
    cases = result.get("test_cases") or []
    lines = [f"# Manual test cases for {key}", ""]
    if url:
        lines += [url, ""]

    for case in cases:
        lines.append(f"## {case.get('id', '')} — {case.get('title', '')}")
        lines.append("")
        lines.append(
            f"**Type:** {case.get('type', '')}  |  "
            f"**Priority:** {case.get('priority', '')}"
        )
        lines.append("")
        if case.get("covers"):
            lines += [f"**Covers:** {case['covers']}", ""]
        if case.get("preconditions"):
            lines.append("**Preconditions:**")
            lines += [f"- {item}" for item in case["preconditions"]]
            lines.append("")
        if case.get("test_data"):
            lines += [f"**Test data:** {case['test_data']}", ""]

        lines += ["| # | Action | Expected |", "|---|--------|----------|"]
        for number, step in enumerate(case.get("steps") or [], start=1):
            action = str(step.get("action", "")).replace("|", "\\|")
            expected = str(step.get("expected", "")).replace("|", "\\|")
            lines.append(f"| {number} | {action} | {expected} |")
        lines.append("")

        if case.get("expected_result"):
            lines += [f"**Expected result:** {case['expected_result']}", ""]

    for heading, items in (
        ("Open questions (the issue does not answer these)", result.get("open_questions")),
        ("Not manually testable (needs automation or tooling)", result.get("not_manually_testable")),
    ):
        if items:
            lines += [f"## {heading}", ""]
            lines += [f"- {item}" for item in items]
            lines.append("")

    lines.append(f"({len(cases)} test cases suggested. Nothing has been created in Jira.)")
    return "\n".join(lines)


CSV_COLUMNS = [
    "ID",
    "Title",
    "Type",
    "Priority",
    "Covers",
    "Preconditions",
    "Test Data",
    "Step",
    "Action",
    "Expected",
    "Overall Expected Result",
]


def render_csv(result: dict[str, Any]) -> str:
    """One row per step — the shape Xray, Zephyr and TestRail all import.

    The case-level columns repeat on each of its rows, which is what those
    importers expect; they group consecutive rows by ID.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)

    for case in result.get("test_cases") or []:
        for number, step in enumerate(case.get("steps") or [], start=1):
            writer.writerow(
                [
                    case.get("id", ""),
                    case.get("title", ""),
                    case.get("type", ""),
                    case.get("priority", ""),
                    case.get("covers", ""),
                    "; ".join(case.get("preconditions") or []),
                    case.get("test_data", ""),
                    number,
                    step.get("action", ""),
                    step.get("expected", ""),
                    case.get("expected_result", ""),
                ]
            )
    return buffer.getvalue()
