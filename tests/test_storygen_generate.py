"""Generation tests with a stubbed Claude client — no API key, no spend.

The fixture is the real DFE-9067 description, so the prompt is exercised
against the shape of description this team actually writes.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from storygen.generate import SCHEMA, build_prompt, generate_stories
from storygen.main import _render

REAL_DESCRIPTION = """Story:

As a web-focused marketing specialist, I would like to learn how many players made a
first-time deposit after completing registration through the web.

Details:

- Capture successful deposit events and send to Google Analytics
  - Amount of the deposit made by the player
  - Currency of the deposit if available
- Event name example: ga_first_time_deposit
  - Parameters
    - ga_revenue
    - ga_currency
- Deposit failures should not be captured
- Verify / Validate correct information within Appsflyer"""

CONTEXT = {
    "key": "DFE-9067",
    "summary": "First Time Deposit Tracking (Web - Google Analytics)",
    "type": "Change Request",
    "status": "In QA",
    "project": "DFE",
    "priority": "Major",
    "labels": ["analytics"],
    "description": REAL_DESCRIPTION,
    "links": [
        {"relation": "blocks", "key": "UPAM-3395", "summary": "Sync deletion",
         "status": "Review", "type": "Change Request"},
    ],
    "subtasks": [{"key": "DFE-9068", "summary": "Add GA event"}],
    "url": "https://scientificgames.atlassian.net/browse/DFE-9067",
}

REPLY = {
    "stories": [
        {
            "summary": "Capture first-time deposit event and send to Google Analytics",
            "user_story": "As a web-focused marketing specialist, I would like to see a "
                          "ga_first_time_deposit event, so that I can measure acquisition.",
            "details": "Fire ga_first_time_deposit on the first successful web deposit.",
            "acceptance_criteria": [
                "ga_first_time_deposit fires once on a player's first successful deposit",
                "A failed deposit produces no event",
            ],
        }
    ],
    "open_questions": ["Is a deposit after account closure and reopening still 'first time'?"],
}


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeResponse:
    content: list
    stop_reason: str = "end_turn"


@dataclass
class FakeMessages:
    reply: Any
    calls: list = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@dataclass
class FakeClient:
    messages: FakeMessages


def client_for(reply):
    return FakeClient(messages=FakeMessages(reply=reply))


def json_reply(payload):
    return FakeResponse([TextBlock(json.dumps(payload))])


# --- the prompt -----------------------------------------------------------


def test_prompt_carries_the_issue_content():
    prompt = build_prompt(CONTEXT)

    assert "DFE-9067" in prompt
    assert "First Time Deposit Tracking" in prompt
    assert "ga_first_time_deposit" in prompt
    assert "Change Request" in prompt


def test_prompt_marks_links_as_context_and_subtasks_as_done():
    prompt = build_prompt(CONTEXT)

    assert "do not write stories for these" in prompt
    assert "UPAM-3395" in prompt
    assert "do not duplicate" in prompt
    assert "DFE-9068" in prompt


def test_prompt_handles_an_issue_with_no_description_or_links():
    bare = {**CONTEXT, "description": "", "links": [], "subtasks": [], "labels": [], "priority": ""}
    prompt = build_prompt(bare)

    assert "(no description)" in prompt
    assert "Linked issues" not in prompt


# --- the request ----------------------------------------------------------


def test_request_uses_the_configured_model_and_a_strict_json_schema():
    client = client_for(json_reply(REPLY))
    generate_stories(CONTEXT, api_key="k", model="claude-opus-5", client=client)

    sent = client.messages.calls[0]
    assert sent["model"] == "claude-opus-5"
    assert sent["output_config"]["format"]["schema"] == SCHEMA
    assert SCHEMA["additionalProperties"] is False
    assert sent["system"].startswith("You are an experienced business analyst")


def test_schema_requires_every_story_field():
    story = SCHEMA["properties"]["stories"]["items"]
    assert set(story["required"]) == {"summary", "user_story", "details", "acceptance_criteria"}
    assert story["additionalProperties"] is False


def test_result_is_parsed_into_stories_and_questions():
    result = generate_stories(CONTEXT, api_key="k", client=client_for(json_reply(REPLY)))

    assert len(result["stories"]) == 1
    assert result["stories"][0]["summary"].startswith("Capture first-time deposit")
    assert len(result["open_questions"]) == 1


def test_a_refusal_is_reported_clearly():
    refused = FakeResponse([TextBlock("")], stop_reason="refusal")
    with pytest.raises(RuntimeError, match="declined"):
        generate_stories(CONTEXT, api_key="k", client=client_for(refused))


def test_an_empty_response_is_reported_clearly():
    with pytest.raises(RuntimeError, match="empty"):
        generate_stories(CONTEXT, api_key="k", client=client_for(FakeResponse([TextBlock("  ")])))


def test_generation_never_calls_jira():
    """The generator is handed context; it must not import Jira or any HTTP client."""
    import ast
    import inspect

    import storygen.generate as module

    tree = ast.parse(inspect.getsource(module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert not [name for name in imported if "jira" in name.lower()]
    assert not [name for name in imported if name in {"httpx", "requests", "urllib"}]


# --- the review output ----------------------------------------------------


def test_rendered_review_shows_stories_criteria_and_questions():
    text = _render("DFE-9067", REPLY)

    assert "# Suggested Stories for DFE-9067" in text
    assert "## Story 1: Capture first-time deposit event" in text
    assert "  - ga_first_time_deposit fires once" in text
    assert "## Open questions" in text
    assert "Nothing has been created in Jira." in text


def test_rendered_review_handles_no_suggestions():
    text = _render("DFE-1", {"stories": [], "open_questions": []})
    assert "(0 stories suggested" in text
