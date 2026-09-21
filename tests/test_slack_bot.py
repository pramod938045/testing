"""Slack handler tests with a stubbed Slack web client and a stubbed agent."""

from dataclasses import dataclass
from typing import Any

import pytest

import slack_bot
from app.agent import ChatResult, ToolCallRecord


class FakeSlackClient:
    def __init__(self):
        self.posted: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []

    async def chat_postMessage(self, **kwargs):
        self.posted.append(kwargs)
        return {"ts": "1700000000.000100"}

    async def chat_update(self, **kwargs):
        self.updated.append(kwargs)
        return {"ok": True}


class FakeAgent:
    def __init__(self, result: ChatResult | Exception):
        self.result = result
        self.seen: list[str] = []

    async def chat(self, message: str) -> ChatResult:
        self.seen.append(message)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def agent(monkeypatch):
    """Point the bot's session store at a single stubbed agent."""

    def _install(result: ChatResult | Exception) -> FakeAgent:
        stub = FakeAgent(result)
        monkeypatch.setattr(slack_bot.sessions, "_factory", lambda: stub)
        monkeypatch.setattr(slack_bot.sessions, "_items", {})
        return stub

    return _install


def mention(text: str, **overrides) -> dict[str, Any]:
    event = {"type": "app_mention", "text": text, "channel": "C1", "ts": "111.1", "user": "U9"}
    event.update(overrides)
    return event


def test_session_key_is_per_thread():
    assert slack_bot.session_key(mention("hi")) == "C1:111.1"
    assert slack_bot.session_key(mention("hi", thread_ts="100.0")) == "C1:100.0"


def test_session_key_for_an_untreaded_dm_is_per_user():
    event = mention("hi", channel_type="im", channel="D1")
    assert slack_bot.session_key(event) == "dm:D1:U9"


def test_only_human_dms_are_answered():
    assert slack_bot.is_direct_question(mention("hi", channel_type="im")) is True
    assert slack_bot.is_direct_question(mention("hi", channel_type="channel")) is False
    assert slack_bot.is_direct_question(mention("hi", channel_type="im", bot_id="B1")) is False
    assert (
        slack_bot.is_direct_question(mention("hi", channel_type="im", subtype="message_changed"))
        is False
    )


async def test_reply_updates_the_placeholder_with_formatted_text(agent):
    stub = agent(
        ChatResult(
            reply="**ABC-1** — [open it](https://x.atlassian.net/browse/ABC-1)",
            tool_calls=[ToolCallRecord("search_issues", {}, True, "1 issue")],
        )
    )
    client = FakeSlackClient()
    await slack_bot.respond(mention("<@U0BOT> what's mine?"), client)

    # The mention is stripped before the agent sees the question.
    assert stub.seen == ["what's mine?"]
    assert client.posted[0]["text"] == "_Checking Jira…_"

    update = client.updated[0]
    assert update["ts"] == "1700000000.000100"
    body = update["blocks"][0]["text"]["text"]
    assert "*ABC-1*" in body
    assert "<https://x.atlassian.net/browse/ABC-1|open it>" in body
    assert update["blocks"][1]["elements"][0]["text"] == "Jira: search_issues"


async def test_channel_mentions_reply_in_thread(agent):
    agent(ChatResult(reply="ok"))
    client = FakeSlackClient()
    await slack_bot.respond(mention("<@U0BOT> hi"), client)

    assert client.posted[0]["thread_ts"] == "111.1"


async def test_dms_reply_without_starting_a_thread(agent):
    agent(ChatResult(reply="ok"))
    client = FakeSlackClient()
    await slack_bot.respond(mention("hi", channel_type="im", channel="D1"), client)

    assert client.posted[0]["thread_ts"] is None


async def test_reset_clears_the_thread_and_does_not_call_the_agent(agent):
    stub = agent(ChatResult(reply="should not be used"))
    client = FakeSlackClient()

    slack_bot.sessions.get("C1:111.1")
    await slack_bot.respond(mention("<@U0BOT> reset"), client)

    assert stub.seen == []
    assert "C1:111.1" not in slack_bot.sessions
    assert "fresh conversation" in client.posted[0]["text"]


async def test_an_empty_mention_gets_a_prompt_not_an_agent_call(agent):
    stub = agent(ChatResult(reply="unused"))
    client = FakeSlackClient()
    await slack_bot.respond(mention("<@U0BOT>"), client)

    assert stub.seen == []
    assert "Ask me something about Jira" in client.posted[0]["text"]
    assert client.updated == []


async def test_an_agent_failure_is_reported_in_the_thread(agent):
    agent(RuntimeError("boom"))
    client = FakeSlackClient()
    await slack_bot.respond(mention("<@U0BOT> what's mine?"), client)

    body = client.updated[0]["blocks"][0]["text"]["text"]
    assert "Something went wrong" in body


async def test_a_long_reply_is_truncated_to_fit_a_slack_block(agent):
    agent(ChatResult(reply="\n".join(f"line {i}" for i in range(2000))))
    client = FakeSlackClient()
    await slack_bot.respond(mention("<@U0BOT> everything"), client)

    body = client.updated[0]["blocks"][0]["text"]["text"]
    assert len(body) <= 2950
    assert body.endswith("… (truncated)")
    assert len(client.updated[0]["text"]) <= 300  # notification fallback
