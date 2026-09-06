"""Agent-loop tests with a stubbed Claude client — no network, no API key spend."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.agent import JiraChatAgent
from app.config import Settings


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str = "tu_1"
    type: str = "tool_use"


@dataclass
class FakeMessage:
    content: list[Any]
    stop_reason: str = "end_turn"


class FakeMessages:
    def __init__(self, responses: list[FakeMessage]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeMessage:
        self.calls.append(kwargs)
        return self._responses.pop(0)


@dataclass
class FakeAnthropic:
    messages: FakeMessages = field(default=None)


def build_agent(make_client, responses, routes=None, **overrides) -> tuple[JiraChatAgent, Any, Any]:
    jira, seen = make_client(routes or {})
    settings = Settings(
        jira_base_url="https://example.atlassian.net",
        jira_email="me@example.com",
        jira_api_token="token",
        anthropic_api_key="test-key",
        **overrides,
    )
    agent = JiraChatAgent(jira=jira, settings=settings, jira_user={"display_name": "Sam Patel"})
    agent.client = FakeAnthropic(messages=FakeMessages(responses))
    return agent, jira, seen


async def test_plain_answer_needs_no_tools(make_client):
    agent, jira, _ = build_agent(make_client, [FakeMessage([TextBlock("Hello!")])])
    result = await agent.chat("hi")

    assert result.reply == "Hello!"
    assert result.tool_calls == []
    await jira.aclose()


async def test_tool_call_result_is_fed_back_and_recorded(make_client):
    responses = [
        FakeMessage([ToolUseBlock("search_issues", {"jql": "assignee = currentUser()"})], stop_reason="tool_use"),
        FakeMessage([TextBlock("You have 1 open issue.")]),
    ]
    routes = {
        "POST /rest/api/3/search/jql": {
            "issues": [{"key": "ABC-1", "fields": {"summary": "Fix login", "status": {"name": "To Do"}}}]
        }
    }
    agent, jira, seen = build_agent(make_client, responses, routes)
    result = await agent.chat("what's on my plate?")

    assert result.reply == "You have 1 open issue."
    assert [(call.name, call.ok) for call in result.tool_calls] == [("search_issues", True)]
    assert "ABC-1" in result.tool_calls[0].summary
    assert len(seen) == 1

    # The tool result must go back as a user turn keyed to the tool_use id.
    tool_turn = agent.messages[2]
    assert tool_turn["role"] == "user"
    assert tool_turn["content"][0]["tool_use_id"] == "tu_1"
    assert "is_error" not in tool_turn["content"][0]
    await jira.aclose()


async def test_parallel_tool_calls_return_in_one_user_message(make_client):
    responses = [
        FakeMessage(
            [
                ToolUseBlock("get_issue", {"key": "ABC-1"}, id="a"),
                ToolUseBlock("get_issue", {"key": "ABC-2"}, id="b"),
            ],
            stop_reason="tool_use",
        ),
        FakeMessage([TextBlock("Both are in progress.")]),
    ]
    routes = {
        "GET /rest/api/3/issue/ABC-1": {"key": "ABC-1", "fields": {"summary": "One"}},
        "GET /rest/api/3/issue/ABC-2": {"key": "ABC-2", "fields": {"summary": "Two"}},
    }
    agent, jira, _ = build_agent(make_client, responses, routes)
    result = await agent.chat("compare ABC-1 and ABC-2")

    assert len(result.tool_calls) == 2
    tool_turn = agent.messages[2]
    assert [block["tool_use_id"] for block in tool_turn["content"]] == ["a", "b"]
    assert result.reply == "Both are in progress."
    await jira.aclose()


async def test_failed_tool_is_marked_as_an_error_for_claude(make_client):
    responses = [
        FakeMessage([ToolUseBlock("get_issue", {"key": "NOPE-1"})], stop_reason="tool_use"),
        FakeMessage([TextBlock("That issue doesn't exist.")]),
    ]
    routes = {"GET /rest/api/3/issue/NOPE-1": (404, {"errorMessages": ["Issue does not exist"]})}
    agent, jira, _ = build_agent(make_client, responses, routes)
    result = await agent.chat("show NOPE-1")

    assert result.tool_calls[0].ok is False
    assert agent.messages[2]["content"][0]["is_error"] is True
    assert result.reply == "That issue doesn't exist."
    await jira.aclose()


async def test_no_write_tools_are_ever_offered(make_client):
    agent, jira, _ = build_agent(make_client, [FakeMessage([TextBlock("I can only read Jira.")])])
    await agent.chat("close ABC-1")

    call = agent.client.messages.calls[0]
    offered = {tool["name"] for tool in call["tools"]}
    for forbidden in ("transition_issue", "create_issue", "update_issue", "add_comment", "log_work"):
        assert forbidden not in offered
    assert "search_issues" in offered and "get_issue" in offered

    assert "READ-ONLY" in call["system"][1]["text"]
    assert "You can only read Jira" in call["system"][0]["text"]
    await jira.aclose()


async def test_runaway_tool_loop_is_stopped(make_client):
    responses = [
        FakeMessage([ToolUseBlock("list_projects", {})], stop_reason="tool_use") for _ in range(3)
    ]
    agent, jira, _ = build_agent(
        make_client, responses, {"GET /rest/api/3/project/search": {"values": []}}, max_tool_iterations=3
    )
    result = await agent.chat("loop forever")

    assert "ran out of steps" in result.reply
    assert len(result.tool_calls) == 3
    await jira.aclose()


async def test_history_is_trimmed_at_a_user_turn(make_client):
    agent, jira, _ = build_agent(
        make_client, [FakeMessage([TextBlock("ok")]) for _ in range(6)], history_turns=2
    )
    for index in range(6):
        await agent.chat(f"question {index}")

    assert len(agent.messages) <= 4 + 2
    assert agent.messages[0]["role"] == "user"
    assert isinstance(agent.messages[0]["content"], str)
    await jira.aclose()


async def test_system_prompt_is_cached_and_carries_the_jira_context(make_client):
    agent, jira, _ = build_agent(make_client, [FakeMessage([TextBlock("hi")])])
    await agent.chat("hi")

    system = agent.client.messages.calls[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "example.atlassian.net" in system[1]["text"]
    assert "Sam Patel" in system[1]["text"]
    await jira.aclose()


async def test_reset_clears_the_conversation(make_client):
    agent, jira, _ = build_agent(make_client, [FakeMessage([TextBlock("hi")])])
    await agent.chat("hi")
    assert agent.messages
    agent.reset()
    assert agent.messages == []
    await jira.aclose()
